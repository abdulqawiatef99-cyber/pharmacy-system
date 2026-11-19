"""#324 items handed to the money session: R10 (reports through off_loop), X25 (login only from the program's own
screen), X26 sales half (a transfer proof is read only from the current upload folder), B8 (the transfers report
narrows its sub-queries by org), S30 on the screen (the return proposal knows the supplier). Each test failed on
the code before it. Synthetic data only."""
import asyncio
from pathlib import Path

from app.core import audit
from app.core.errors import NotFound
from app.procurement.service import Procurement
from app.sales import attachments as att
from app.sales import contracts as k
from tests.test_253_no_full_reads import plan
from tests.test_api import APICase, H
from tests.test_attachments import PNG
from tests.test_cashier_acceptance import Base

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestR10ReportsOffTheLoop(APICase):
    def spy(self, owner, name):
        real, seen = getattr(owner, name), {}

        def wrapped(*a, **kw):
            try:
                asyncio.get_running_loop()
                seen["on_loop"] = True
            except RuntimeError:
                seen["on_loop"] = False
            return real(*a, **kw)

        setattr(owner, name, wrapped)
        self.addCleanup(setattr, owner, name, real)
        return seen

    def test_each_report_runs_in_a_worker_thread(self):
        self.http_login()
        cid = k.save(self.db, self.ctx, {"name": "نقابة تجربة"})[0]["id"]
        d = "2026-01-01"
        for owner, name, path in ((k, "report", f"/api/contracts/{cid}/report?from={d}&to={d}"),
                                  (Procurement, "suggest", "/api/procurement/suggestions"),
                                  (Procurement, "stock_health", "/api/procurement/health"),
                                  (Procurement, "supplier_costs", f"/api/procurement/suppliers/{self.pan}"),  # an item's suppliers
                                  (audit, "listing", f"/api/audit?from={d}&to={d}")):
            with self.subTest(path=path):
                seen = self.spy(owner, name)
                r = self.c.get(path, headers=H)
                self.assertEqual(r.status_code, 200, r.text[:300])
                self.assertIs(seen.get("on_loop"), False)


class TestX25LoginFromTheProgramOnly(APICase):
    def test_a_form_post_from_another_site_cannot_sign_in(self):
        body = '{"username": "owner", "password": "x"}'
        r = self.c.post("/api/auth/login", content=body, headers={"Content-Type": "text/plain"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "csrf"))
        self.assertNotIn("set-cookie", r.headers)

    def test_the_program_and_json_clients_still_sign_in(self):
        self.http_login()                                                  # the screen's header
        from tests.helpers import OWNER_PW
        r = self.c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW})
        self.assertEqual(r.status_code, 200, r.text)                       # a JSON client (needs a preflight)


class TestX26ProofFromTheUploadFolderOnly(Base):
    def test_a_stored_path_outside_the_folder_is_never_read(self):
        sid = self.s.create(self.ctx)
        up = self.tmp / "up"
        a = att.attach(self.db, up, self.ctx, sid, PNG)["attachments"][0]["id"]
        outside = self.tmp / "elsewhere.png"
        outside.write_bytes(PNG + b"secret")
        with self.db.transaction():
            self.db.execute("UPDATE source_files SET storage_path=? WHERE id=(SELECT file_id FROM sale_attachments"
                            " WHERE id=?)", (str(outside), a))
        with self.assertRaises(NotFound):                               # no file of that name in the folder
            att.file(self.db, up, self.ctx, a)
        (up / self.ctx.org_id / "transfers" / "elsewhere.png").write_bytes(PNG)
        data, _ = att.file(self.db, up, self.ctx, a)                    # the one in the current folder
        self.assertEqual(data, PNG)


class TestB8TransfersReportByOrg(Base):
    def test_the_sub_queries_use_the_org_index(self):
        real, seen = self.db.all, []
        self.db.all = lambda sql, *a, **kw: (seen.append((sql, a[0] if a else ())), real(sql, *a, **kw))[1]
        try:
            att.transfers(self.db, self.ctx, "2026-01-01", "2026-01-02")
        finally:
            del self.db.all
        sql, params = next(q for q in seen if "sale_attachments" in q[0])
        p = plan(self.db, sql, params)
        self.assertNotIn("SCAN a", p, p)
        self.assertIn("ix_sale_attachments", p, p)


class TestS30ReturnProposalKnowsTheSupplier(APICase):
    def test_the_route_passes_the_supplier(self):
        from app.procurement import returns
        got = {}
        real = returns.suggest_value

        def spy(db, ctx, item, batch, supplier_id=None):
            got["supplier"] = supplier_id
            return 0
        returns.suggest_value = spy
        self.addCleanup(setattr, returns, "suggest_value", real)
        self.http_login()
        r = self.c.get(f"/api/supplier-returns/suggest?item={self.pan}&batch=&supplier=s-1", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(got["supplier"], "s-1")

    def test_the_screen_sends_it_and_asks_again_when_it_changes(self):
        start = JS.index("async function viewSupplierReturns(")
        body = JS[start:JS.index("\nasync function ", start + 10)]
        self.assertEqual(body.count("/api/supplier-returns/suggest?"), body.count("&supplier="))
        self.assertIn('$("#srSup")?.addEventListener("change"', body)
