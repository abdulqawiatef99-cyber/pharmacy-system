"""QA-29: importing a 20,000-row items file froze the whole server for 164 s (a login waited 164 s too).

Measured here on synthetic data (30,000 items, 20,000 new rows): analyze 164.0 s, apply 4.9 s. Two causes:
- the import routes called the service on the server's event loop, so every request waited;
- the duplicate check by name for a row with no maker used substr(dedupe_key, ...), which no index can serve:
  one scan of all 30,000 items per row.
Now analyze can run as a background job with progress (POST /api/imports with background=1, then
GET /api/imports/jobs/{id}); without the flag it still answers in one request, from a worker thread. Apply runs
in a worker thread too.
"""
import asyncio
import sqlite3
import time
from pathlib import Path

from app.dataimport import service as dimp_svc
from tests.test_api import APICase, H

APP_JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


def csv(n, start=0):
    return ("code,item name,barcode,pack size,price\n" + "".join(
        f"QN{x},Synthetic New {x} 500mg,62{x:011d},2,{10 + x % 90}.50\n" for x in range(start, start + n))).encode()


class Spy:
    def __init__(self, name, delay=0.0):
        self.name, self.delay, self.seen = name, delay, []

    def __enter__(self):
        self.real = getattr(dimp_svc.DataImport, self.name)
        spy = self

        def wrapper(obj, *a, **kw):
            try:
                asyncio.get_running_loop()
                spy.seen.append("loop")
            except RuntimeError:
                spy.seen.append("worker")
            if spy.delay:
                time.sleep(spy.delay)
            return spy.real(obj, *a, **kw)
        setattr(dimp_svc.DataImport, self.name, wrapper)
        return self

    def __exit__(self, *exc):
        setattr(dimp_svc.DataImport, self.name, self.real)


class TestImportOffTheLoop(APICase):
    def post(self, data, **fields):
        return self.c.post("/api/imports", headers=H, files={"file": ("items.csv", data, "text/csv")},
                           data={"kind": "items", "source_system": "generic", **fields})

    def wait(self, job):
        for _ in range(200):
            j = self.c.get(f"/api/imports/jobs/{job}", headers=H).json()
            if j["status"] != "running":
                return j
            time.sleep(0.05)
        self.fail("the job never ended")

    def test_analyze_and_apply_run_in_a_worker_thread(self):
        self.http_login()
        with Spy("analyze") as a:
            r = self.post(csv(5))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(a.seen, ["worker"], "the analysis stopped every other request")
        with Spy("apply") as p:
            ok = self.c.post(f"/api/imports/{r.json()['id']}/apply", headers=H)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(p.seen, ["worker"])

    def test_background_job_reports_progress_and_the_result(self):
        self.http_login()
        r = self.post(csv(450), background="1")
        self.assertEqual(r.status_code, 202, r.text)
        job = r.json()["job"]
        j = self.wait(job)
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["total"], 450)
        self.assertEqual(j["done"], 450)
        run = self.c.get(f"/api/imports/{j['run_id']}", headers=H).json()
        self.assertEqual(run["report"]["counts"], {"create": 450})

    def test_a_refused_file_ends_the_job_with_the_same_error(self):
        self.http_login()
        bad = "colour,weight\nred,1\n".encode()
        j = self.wait(self.post(bad, background="1").json()["job"])
        self.assertEqual(j["status"], "failed")
        self.assertIn(j["error"]["code"], ("missing_columns", "mapping_required"))
        self.assertIn("preview", j["error"]["details"])            # the screen draws the same help from it

    def test_a_second_job_of_the_same_person_names_the_running_one(self):
        self.http_login()
        with Spy("analyze", delay=1.5):
            first = self.post(csv(2), background="1").json()["job"]
            again = self.post(csv(2, start=10), background="1")
            self.assertEqual(again.status_code, 409, again.text)
            self.assertEqual(again.json()["error"]["details"]["job"], first)
            self.wait(first)
        self.assertEqual(self.post(csv(2, start=20), background="1").status_code, 202)

    def test_a_job_is_read_only_by_whoever_started_it(self):
        self.http_login()
        job = self.post(csv(3), background="1").json()["job"]
        self.wait(job)
        from app.core import accounts
        other = accounts.create_organization(self.db, name="Synthetic Other", owner_username="other2",
                                             owner_full_name="Other", owner_password="Other-Pass-2026")
        self.assertTrue(other)
        from starlette.testclient import TestClient
        c2 = TestClient(self.app)
        self.http_login("other2", "Other-Pass-2026", client=c2)
        self.assertEqual(c2.get(f"/api/imports/jobs/{job}", headers=H).status_code, 404)

    def test_the_server_answers_while_a_long_analysis_runs(self):
        """A real server (one event loop for everyone, as on the pharmacy PC): a login and /api/health answer
        while an analysis of the file is still running."""
        import json
        import socket
        import threading
        import urllib.request
        import uvicorn
        from tests.helpers import OWNER_PW
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        base = f"http://127.0.0.1:{port}"

        def login():
            req = urllib.request.Request(base + "/api/auth/login", method="POST", headers={**H, "Content-Type": "application/json"},
                                         data=json.dumps({"username": "owner", "password": OWNER_PW}).encode())
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.headers["set-cookie"].split(";")[0]
        try:
            cookie = login()
            boundary = "qa29b"
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nitems\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"i.csv\"\r\n"
                    f"Content-Type: text/csv\r\n\r\n").encode() + csv(2) + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(base + "/api/imports", method="POST", data=body, headers={
                **H, "Cookie": cookie, "Content-Type": f"multipart/form-data; boundary={boundary}"})
            with Spy("analyze", delay=3.0):
                th = threading.Thread(target=lambda: urllib.request.urlopen(req, timeout=30).read())
                th.start()
                time.sleep(0.5)
                t0 = time.monotonic()
                with urllib.request.urlopen(base + "/api/health", timeout=30) as h:
                    self.assertEqual(h.status, 200)
                login()
                waited = time.monotonic() - t0
                th.join()
        finally:
            server.should_exit = True
            t.join(10)
        self.assertLess(waited, 1.5, f"health and a login waited {waited:.1f} s for the import")

    def test_the_screen_starts_a_job_and_shows_progress(self):
        part = APP_JS[APP_JS.index('$("#impForm").addEventListener("submit"'):][:2500]
        self.assertTrue('"background", "1"' in part, "the screen sends the job flag")
        self.assertTrue("/api/imports/jobs/" in part, "the screen polls the job")


class TestJobPermissionsBeforeStarting(APICase):
    """Codex P2 on #501: a refusal for the kind's own permission (opening stock needs inventory.adjust) has to reach
    the request, so the approval dialog opens; inside the job it became a failed job and no dialog."""

    def test_a_missing_kind_permission_answers_the_upload_itself(self):
        from starlette.testclient import TestClient
        from app.core import people
        from tests.test_dataimport import ITEMS, sheet
        uid = people.add_user(self.db, self.ctx, username="importer", full_name="x", password="Staff-Pass-2026",
                              role_code="assistant")
        people.set_exception(self.db, self.ctx, uid, "items.import", "grant")
        c2 = TestClient(self.app)
        self.http_login("importer", "Staff-Pass-2026", client=c2)
        r = c2.post("/api/imports", headers=H, files={"file": ("s.xlsx", sheet(ITEMS[:2]), "application/octet-stream")},
                    data={"kind": "opening_stock", "background": "1"})
        self.assertNotEqual(r.status_code, 202, r.text)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"]["code"], "approval_required")


class TestDuplicateLookupUsesTheIndex(APICase):
    def test_no_maker_name_check_is_an_index_range(self):
        base = "synthetic new 1 500mg"
        plan = " | ".join(r[3] for r in sqlite3.connect(self.db.path).execute(
            "EXPLAIN QUERY PLAN " + dimp_svc.SAME_NAME_ANY_MAKER, ("o", base, base + "|m:", base + "|m;")))
        self.assertNotIn("SCAN items", plan)

    def test_the_range_holds_every_maker_and_nothing_else(self):
        o = self.ctx.org_id
        base = "range base"
        with self.db.transaction():
            for i, key in enumerate((base + "|m:zeta", base + "|mx", base + "x")):
                self.db.execute("INSERT INTO items (id, org_id, name_en, name_norm, dedupe_key) VALUES (?,?,?,?,?)",
                                (f"rg-{i}", o, key, key, key))
        got = self.db.one(dimp_svc.SAME_NAME_ANY_MAKER, (o, base, base + "|m:", base + "|m;"))
        self.assertEqual(got["id"], "rg-0")
        with self.db.transaction():
            self.db.execute("DELETE FROM items WHERE id='rg-0'")
        self.assertIsNone(self.db.one(dimp_svc.SAME_NAME_ANY_MAKER, (o, base, base + "|m:", base + "|m;")))

    def test_same_name_with_a_maker_is_still_found(self):
        from app.catalog import service as cat
        it = cat.create_item(self.db, self.ctx, {"name_en": "Synthetic New 7 500mg", "manufacturer": "Synthetic Maker",
                                                 "pack_size": 2, "public_price": 1000})
        self.http_login()
        body = b"item name,pack size,price\nSynthetic New 7 500mg,2,10\n"
        r = self.c.post("/api/imports", headers=H, files={"file": ("i.csv", body, "text/csv")},
                        data={"kind": "items", "source_system": "generic"})
        rows = r.json()["rows"]
        self.assertEqual(rows[0]["action"], "review")
        self.assertEqual(rows[0]["target_id"], it)
