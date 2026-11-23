"""Codex review #324 parts 2-4, group C (security for one pharmacy now): one test per finding.
Synthetic data only. Every test here failed on develop @ 1de18b8 (verified)."""
import io
import json
import lzma
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from starlette.testclient import TestClient

from app.catalog import service as cat
from app.core import accounts, approvals, backup, people
from app.core.errors import Conflict
from app.dataimport.service import export_catalog
from app.db import new_id
from app.inventory import service as inv
from app.reports import service as reports
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase, OWNER_PW, today
from tests.test_api import APICase, H, approval_headers
from tests.test_review_324_money import PW, _sell

T = date.today()


def _stock(db, ctx, item, qty, cost="15"):
    with db.transaction():
        b = inv.get_or_create_batch(db, ctx, item, "B1", (T + timedelta(days=400)).isoformat())
        inv.post_movement(db, ctx, item_id=item, qty_delta=qty, batch_id=b, movement_type="purchase",
                          unit_cost=Decimal(cost))


def _staff(case, username, role):
    uid = people.add_user(case.db, case.ctx, username=username, full_name=username, password=PW, role_code=role)
    return uid, case.login(username, PW)


# ---------------------------------------------------------------- X5: one approval = one action, once
class TestX5ApprovalUsedOnce(APICase):
    def setUp(self):
        super().setUp()
        approvals.clear()
        people.set_pin(self.db, self.ctx, self.ctx.user_id, "9753")
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO categories (id, org_id, name, no_return, mark, sort) VALUES (?,?,?,?,?,?)",
                            (cid, self.ctx.org_id, "مستحضرات", 1, None, 1))
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Cream", "pack_size": 1, "public_price": 1000})
        with self.db.transaction():
            self.db.execute("UPDATE items SET category_id=? WHERE id=?", (cid, self.item))
        _stock(self.db, self.ctx, self.item, 10)
        CashDrawer(self.db).open(self.ctx, 100000)
        people.add_user(self.db, self.ctx, username="asst", full_name="asst", password=PW, role_code="assistant")
        self.sale = _sell(Sales(self.db), self.login("asst", PW), self.item, 3)      # her own sale
        self.a = TestClient(self.app)
        self.http_login("asst", PW, client=self.a)
        self.path = f"/api/sales/{self.sale['id']}/return"
        self.line = self.sale["lines"][0]["id"]

    def payload(self, qty):
        return {"lines": {self.line: qty}, "refund_method": "cash", "reason": "مرتجع"}

    def ret(self, hdr, qty):
        return self.a.post(self.path, json=self.payload(qty), headers=hdr)

    def test_one_approval_returns_one_quantity_once(self):
        hdr = approval_headers(self.a, self.ctx.user_id, "9753", "sales.return_restricted", self.path, "POST",
                               payload=self.payload(1))
        self.assertEqual(self.ret(hdr, 1).status_code, 200)
        again = self.ret(hdr, 1)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")

    def test_approval_checks_the_intent_before_the_action_starts(self):
        hdr = approval_headers(self.a, self.ctx.user_id, "9753", "sales.return_restricted", self.path, "POST",
                               payload=self.payload(1))
        self.assertEqual(self.ret(hdr, 2).json()["error"]["code"], "approval_mismatch")
        self.assertEqual(self.ret(hdr, 1).status_code, 200)


# ---------------------------------------------------------------- X7: cached reply after a permission is revoked
class TestX7ReplayAfterRevoke(APICase):
    def test_a_cached_payroll_answer_is_not_replayed_after_payroll_manage_is_revoked(self):
        mgr = people.add_user(self.db, self.ctx, username="mgr", full_name="mgr", password=PW, role_code="manager")
        people.set_exception(self.db, self.ctx, mgr, "payroll.manage", "grant")
        c = TestClient(self.app)
        self.http_login("mgr", PW, client=c)
        hdr = {**H, "Idempotency-Key": "pay-settings-1"}
        body = {"action": "pay_settings", "user_id": mgr, "monthly_salary": 500000, "work_days": 26}
        first = c.post("/api/staff", json=body, headers=hdr)
        self.assertEqual(first.status_code, 200, first.text)
        people.set_exception(self.db, self.ctx, mgr, "payroll.manage", "deny")
        retry = c.post("/api/staff", json=body, headers=hdr)
        self.assertNotEqual(retry.status_code, 200, retry.text)
        self.assertNotIn("500000", retry.text)


# ---------------------------------------------------------------- X13: unit costs in sale answers
class TestX13NoCostsForCashiers(DBTestCase):
    def test_sale_answers_carry_no_unit_cost_without_costs_read(self):
        item = cat.create_item(self.db, self.ctx, {"name_en": "Tab", "pack_size": 1, "public_price": 1000})
        _stock(self.db, self.ctx, item, 5, cost="7.25")
        _, actx = _staff(self, "asst", "assistant")
        s = Sales(self.db)
        CashDrawer(self.db).open(actx, 0)
        done = _sell(s, actx, item, 1)
        for sale in (done, s.get(actx, done["id"])):
            for a in sale["lines"][0]["allocations"]:
                self.assertNotIn("unit_cost", a)
        self.assertIn("unit_cost", s.get(self.ctx, done["id"])["lines"][0]["allocations"][0])


# ---------------------------------------------------------------- S8 / X12 / R7: hidden items
class _Hidden(DBTestCase):
    def setUp(self):
        super().setUp()
        self.hid = cat.create_item(self.db, self.ctx, {"name_en": "Secret Item", "pack_size": 1, "public_price": 1000,
                                                       "hidden": True})
        cat.add_barcode(self.db, self.ctx, self.hid, "622100000017")
        _stock(self.db, self.ctx, self.hid, 17)


class TestS8X12Export(_Hidden):
    def test_export_leaves_out_hidden_items_and_stock_without_the_permissions(self):
        _, actx = _staff(self, "asst", "assistant")
        people.set_exception(self.db, self.ctx, actx.user_id, "inventory.read", "deny")
        actx = self.login("asst", PW)
        ws = load_workbook(io.BytesIO(export_catalog(self.db, actx))).active
        cells = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
        self.assertNotIn("Secret Item", cells)
        self.assertNotIn("622100000017", cells)
        self.assertNotIn("stock_base_units", cells)

    def test_items_write_cannot_unhide_without_items_hidden(self):
        _, pctx = _staff(self, "pharm1", "pharmacist")
        with self.assertRaises(Exception) as e:
            cat.update_item(self.db, pctx, self.hid, {"hidden": False})
        self.assertIn(getattr(e.exception, "code", ""), ("approval_required", "not_found"))
        self.assertTrue(self.db.one("SELECT hidden FROM items WHERE id=?", (self.hid,))["hidden"])


class TestR7TopItems(_Hidden):
    def test_top_items_masks_a_hidden_item_for_a_manager_without_items_hidden(self):
        CashDrawer(self.db).open(self.ctx, 0)
        _sell(Sales(self.db), self.ctx, self.hid, 1)
        mid, _ = _staff(self, "mgr", "manager")
        people.set_exception(self.db, self.ctx, mid, "items.hidden", "deny")
        mctx = self.login("mgr", PW)
        top = reports.sales_report(self.db, mctx, today(), today())["top_items"]
        self.assertEqual(len(top), 1)
        self.assertNotIn("Secret Item", json.dumps(top, ensure_ascii=False))


# ---------------------------------------------------------------- S17 / X10: stocked item switched to service
class TestS17X10StockedItemToService(DBTestCase):
    def test_an_item_with_stock_history_cannot_become_a_service(self):
        item = cat.create_item(self.db, self.ctx, {"name_en": "Tab", "pack_size": 1, "public_price": 1000})
        _stock(self.db, self.ctx, item, 5)
        with self.assertRaises(Conflict) as e:
            cat.update_item(self.db, self.ctx, item, {"is_service": True})
        self.assertEqual(e.exception.code, "service_has_stock")


# ---------------------------------------------------------------- X15-X17: restore
class _Restore(DBTestCase):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "uploads"
        org = self.ctx.org_id
        (self.up / org).mkdir(parents=True)
        (self.up / org / "inv1.pdf").write_bytes(b"%PDF original")
        with self.db.transaction():
            self.db.execute("INSERT INTO source_files (id, org_id, filename, storage_path, size_bytes, sha256, mime_type,"
                            " uploaded_by) VALUES (?,?,?,?,?,?,?,?)",
                            (new_id(), org, "inv1.pdf", str(self.up / org / "inv1.pdf"), 13,
                             __import__("hashlib").sha256(b"%PDF original").hexdigest(), "application/pdf",
                             self.ctx.user_id))
        self.zip = backup.create(self.db, self.up, self.tmp / "bk")

    def tamper(self, member, data):
        out = self.tmp / "tampered.zip"
        with zipfile.ZipFile(self.zip) as src, zipfile.ZipFile(out, "w") as dst:
            for i in src.infolist():
                dst.writestr(i, data if i.filename == member else src.read(i.filename))
        return out


class TestX15BadAttachmentNotInstalled(_Restore):
    def test_an_attachment_that_failed_its_checksum_is_not_copied(self):
        bad = self.tamper(f"uploads/{self.ctx.org_id}/inv1.pdf", b"%PDF changed!")
        target = self.tmp / "restore-to"
        r = backup.restore(bad, target / "pharmacy.db", target / "uploads", self.tmp / "pre",
                           allow_missing_attachments=True)
        self.assertEqual(len(r["missing_attachments"]), 1)
        self.assertFalse((target / "uploads" / self.ctx.org_id / "inv1.pdf").exists())

    def test_two_members_that_differ_only_in_case_are_refused(self):
        out = self.tmp / "case.zip"
        with zipfile.ZipFile(self.zip) as src, zipfile.ZipFile(out, "w") as dst:
            for i in src.infolist():
                dst.writestr(i, src.read(i.filename))
            dst.writestr(f"uploads/{self.ctx.org_id}/INV1.pdf", b"other")
        self.assertTrue(any("case" in p for p in backup.verify_archive(out)["problems"]))


class TestX16NoPartialRestore(_Restore):
    def test_a_failed_attachment_copy_leaves_the_old_database_live(self):
        from unittest import mock
        target = self.tmp / "live"
        live = target / "pharmacy.db"
        target.mkdir()
        cat.create_item(self.db, self.ctx, {"name_en": "Made after the backup", "pack_size": 1})
        self.db.backup_to(live)
        real = backup.shutil.copy2

        def full(src, dst, *a, **k):                    # the disk fills while the attachments are copied
            if Path(dst).is_relative_to(target / "uploads"):
                raise OSError("disk full")
            return real(src, dst, *a, **k)
        with mock.patch("app.core.backup.shutil.copy2", side_effect=full):
            with self.assertRaises(OSError):
                backup.restore(self.zip, live, target / "uploads", self.tmp / "pre")
        from app.db import Database
        db = Database(live)
        try:                                            # still the data from before the restore
            self.assertEqual(db.one("SELECT count(*) n FROM items WHERE name_en='Made after the backup'")["n"], 1)
        finally:
            db.close()


class TestX17RestoreEndsSessions(_Restore):
    def test_a_session_saved_in_the_backup_is_not_alive_after_restore(self):
        tok = accounts.login(self.db, "owner", OWNER_PW)["token"]
        zip2 = backup.create(self.db, self.up, self.tmp / "bk2")
        accounts.logout(self.db, tok) if hasattr(accounts, "logout") else None
        target = self.tmp / "r"
        backup.restore(zip2, target / "pharmacy.db", target / "uploads", self.tmp / "pre2")
        from app.db import Database
        db = Database(target / "pharmacy.db")
        try:
            with self.assertRaises(Exception):
                accounts.resolve_session(db, tok)
        finally:
            db.close()


# ---------------------------------------------------------------- X18: upload size
class TestX18UploadLimits(APICase):
    def test_a_chunked_upload_is_refused_before_it_is_read(self):
        """The shipped h11 hands the app a chunked body together with a smaller Content-Length (probe on
        #324: CL 100, 5000 bytes streamed). The upload must be refused before any byte is read, not spooled."""
        import anyio
        from app.api.app import COOKIE
        self.http_login()
        sid = self.c.cookies.get(COOKIE)
        chunk, sent = b"x" * 65536, []

        async def call():
            headers = [(b"host", b"testserver"), (b"x-requested-with", b"pharm"),
                       (b"content-type", b"multipart/form-data; boundary=B"), (b"content-length", b"100"),
                       (b"transfer-encoding", b"chunked"), (b"cookie", f"{COOKIE}={sid}".encode())]
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                     "scheme": "http", "path": "/api/purchases/import", "raw_path": b"/api/purchases/import",
                     "query_string": b"", "headers": headers, "client": ("testclient", 1), "server": ("testserver", 80)}
            status = []

            async def receive():
                if len(sent) < 400:                       # 25 MB if read to the end
                    sent.append(1)
                    return {"type": "http.request", "body": chunk, "more_body": True}
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(m):
                if m["type"] == "http.response.start":
                    status.append(m["status"])
            await self.app(scope, receive, send)
            return status[0]
        status = anyio.run(call)
        self.assertIn(status, (411, 413, 422))
        self.assertEqual(len(sent), 0, "the body was read before the refusal")

    def test_a_backup_database_that_expands_past_the_limit_is_refused(self):
        from unittest import mock
        z = self.tmp / "bomb.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("pharmacy.db.xz", lzma.compress(b"\0" * (4 << 20)))
        with mock.patch.object(backup, "MAX_DB_BYTES", 1 << 20):
            with zipfile.ZipFile(z) as f, self.assertRaises(RuntimeError):
                backup._extract_db(f, str(self.tmp))
