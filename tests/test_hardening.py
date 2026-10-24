"""Regression tests for the review fixes of 2026-10-24 (second session)."""
import json
import sqlite3
from decimal import Decimal

from starlette.testclient import TestClient

from app.catalog import service as cat
from app.core import accounts, audit
from app.core.errors import Conflict
from app.inventory import service as inv
from app.sales.service import Sales
from tests.helpers import DBTestCase, OWNER_PW
from tests.test_api import APICase, H


class TestAuditChain(DBTestCase):
    def test_chain_intact_then_tamper_detected(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab"})
        self.assertEqual(audit.verify_chain(self.db), [])
        # an attacker with raw DB access drops the guard trigger and edits history
        c = sqlite3.connect(self.db.path)
        c.execute("DROP TRIGGER audit_log_no_update")
        c.execute("UPDATE audit_log SET after_json='{}' WHERE action='item.create'")
        c.commit()
        c.close()
        probs = audit.verify_chain(self.db)
        self.assertTrue(any(p["problem"] == "row_modified" for p in probs))

    def test_deleted_row_detected(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab"})
        c = sqlite3.connect(self.db.path)
        c.execute("DROP TRIGGER audit_log_no_delete")
        c.execute("DELETE FROM audit_log WHERE seq=2")
        c.commit()
        c.close()
        self.assertTrue(audit.verify_chain(self.db))

    def test_no_secrets_in_audit(self):
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        accounts.change_password(self.db, self.ctx, OWNER_PW, "New-Owner-Pass-1")
        dump = " ".join(str(tuple(r)) for r in self.db.all("SELECT * FROM audit_log"))
        for secret in ("Assist-Pass-2026", "New-Owner-Pass-1", OWNER_PW, "scrypt$"):
            self.assertNotIn(secret, dump)
        tokens = [r[0] for r in self.db.all("SELECT token_hash FROM sessions")]
        self.assertFalse(any(t in dump for t in tokens))
        self.assertEqual(audit._scrub({"a": {"password": "x", "ok": 1}}), {"a": {"password": "***", "ok": 1}})


class TestPackSizeLock(DBTestCase):
    def test_locked_once_stock_exists(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2})
        cat.update_item(self.db, self.ctx, it, {"pack_size": 3})          # no stock yet: allowed
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=6, movement_type="opening",
                              unit_cost=Decimal("1"))
        with self.assertRaises(Conflict) as e:
            cat.update_item(self.db, self.ctx, it, {"pack_size": 2})
        self.assertEqual(e.exception.code, "pack_size_locked")
        cat.update_item(self.db, self.ctx, it, {"location": "A1"})         # other edits fine


class TestSaleIdempotency(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s = Sales(self.db)
        self.it = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "public_price": 4000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.it, qty_delta=5, movement_type="opening",
                              unit_cost=Decimal("20"))

    def test_double_scan_and_retried_complete(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.it, units=1, client_ref="scan-1")
        d = self.s.add_line(self.ctx, sid, self.it, units=1, client_ref="scan-1")   # double click
        self.assertEqual(len(d["lines"]), 1)
        f = self.s.safety_check(self.ctx, sid)
        dec = {i: "ok" for i in range(len(f))}
        a = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=dec, request_id="pay-1")
        b = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=dec, request_id="pay-1")
        self.assertTrue(b["replayed"])
        self.assertEqual(a["sale_no"], b["sale_no"])
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.it)["total_qty"], 4)
        with self.assertRaises(Conflict):        # a different click id cannot complete again
            self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=dec, request_id="pay-2")


class TestApiHardening(APICase):
    def test_idempotency_key_replay_and_mismatch(self):
        self.http_login()
        h = {**H, "Idempotency-Key": "k-1"}
        r1 = self.c.post("/api/suppliers", headers=h, json={"name": "Abda"})
        r2 = self.c.post("/api/suppliers", headers=h, json={"name": "Abda"})
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r2.headers.get("idempotent-replay"), "true")
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        self.assertEqual(len([s for s in self.c.get("/api/suppliers").json() if s["name"] == "Abda"]), 1)
        r3 = self.c.post("/api/suppliers", headers=h, json={"name": "Other"})
        self.assertEqual(r3.json()["error"]["code"], "idempotency_mismatch")

    def test_feature_switch(self):
        self.http_login()
        self.assertEqual(self.c.get("/api/counts").status_code, 200)
        accounts.set_entitlement(self.db, self.org["org_id"], "counting", enabled=False)
        r = self.c.get("/api/counts")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "feature_disabled"))
        self.assertEqual(self.c.get("/api/items").status_code, 200)     # rest unaffected

    def test_import_cannot_touch_other_tenant(self):
        import io
        from openpyxl import Workbook
        other_org, other_ctx = self.second_tenant()
        foreign = cat.create_item(self.db, other_ctx, {"name_en": "Foreign Item 5mg tab"})
        wb = Workbook()
        ws = wb.active
        ws.append(["كود الصنف", "اسم الصنف", "سعر الجمهور"])
        ws.append([1, "Panadol 500mg 24 tab", 41])
        b = io.BytesIO()
        wb.save(b)
        self.http_login()
        run = self.c.post("/api/imports", headers=H, data={"kind": "items"},
                          files={"file": ("x.xlsx", b.getvalue(), "x")}).json()
        row = run["rows"][0]
        r = self.c.post(f"/api/imports/{run['id']}/decide", headers=H,
                        json={"row_id": row["id"], "decision": f"link:{foreign}"})
        self.assertEqual(r.status_code, 404)
        other = TestClient(self.app)
        self.http_login("other", "Other-Pass-2026", client=other)
        self.assertEqual(other.get(f"/api/imports/{run['id']}").status_code, 404)
        self.assertEqual(other.post(f"/api/imports/{run['id']}/apply", headers=H).status_code, 404)
        self.assertEqual(cat.get_item(self.db, other_ctx, foreign)["public_price"], None)
