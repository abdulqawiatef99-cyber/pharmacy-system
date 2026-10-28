"""Codex review: valid mutation payloads must not bypass permissions or tenant isolation.
These tests verify existing row values, not just row counts. Synthetic data only.
"""
from starlette.testclient import TestClient

from app.core import people
from app.core.permissions import PERMISSIONS
from app.sales.service import Sales
from tests.test_api import APICase, H


class TestCodexAccessGuards(APICase):
    def _rows(self):
        return {
            table: [dict(r) for r in self.db.all("SELECT * FROM " + table + " ORDER BY id")]
            for table in ("items", "customers", "memberships", "roles", "sale_lines", "stock_movements")
        }

    def _customer(self):
        return Sales(self.db).create_customer(self.ctx, name="Synthetic customer", credit_limit=0)

    def test_owner_valid_payloads_reach_real_mutations(self):
        self.http_login()
        cid = self._customer()
        r = self.c.patch("/api/items/" + self.pan, headers=H, json={"name_en": "Synthetic renamed item"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.patch("/api/customers/" + cid, headers=H, json={"name": "Synthetic renamed customer"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.one("SELECT name_en FROM items WHERE id=?", (self.pan,))[0],
                         "Synthetic renamed item")
        self.assertEqual(self.db.one("SELECT name FROM customers WHERE id=?", (cid,))[0],
                         "Synthetic renamed customer")

    def test_no_permission_valid_patches_leave_existing_values_unchanged(self):
        cid = self._customer()
        uid = people.add_user(self.db, self.ctx, username="review-none", full_name="Review none",
                              password="Review-Pass-2026", role_code="assistant")
        for permission in PERMISSIONS:
            people.set_exception(self.db, self.ctx, uid, permission, "deny")
        other = TestClient(self.app)
        self.http_login("review-none", "Review-Pass-2026", client=other)
        before = self._rows()
        requests = [
            ("/api/items/" + self.pan, {"name_en": "Unauthorized item rename"}),
            ("/api/customers/" + cid, {"name": "Unauthorized customer rename"}),
        ]
        for path, payload in requests:
            with self.subTest(path=path):
                r = other.patch(path, headers=H, json=payload)
                self.assertEqual(r.status_code, 403, r.text)
                self.assertEqual(self._rows(), before)

    def test_other_tenant_valid_patches_leave_existing_values_unchanged(self):
        cid = self._customer()
        self.second_tenant()
        other = TestClient(self.app)
        self.http_login("other", "Other-Pass-2026", client=other)
        before = self._rows()
        requests = [
            ("/api/items/" + self.pan, {"name_en": "Foreign item rename"}),
            ("/api/customers/" + cid, {"name": "Foreign customer rename"}),
        ]
        for path, payload in requests:
            with self.subTest(path=path):
                r = other.patch(path, headers=H, json=payload)
                self.assertEqual(r.status_code, 404, r.text)
                self.assertEqual(self._rows(), before)

    def test_foreign_owner_pin_cannot_approve_local_write(self):
        uid = people.add_user(self.db, self.ctx, username="review-assistant", full_name="Review assistant",
                              password="Review-Pass-2026", role_code="assistant")
        _, foreign = self.second_tenant()
        people.set_pin(self.db, foreign, foreign.user_id, "573921")
        other = TestClient(self.app)
        self.http_login("review-assistant", "Review-Pass-2026", client=other)
        before = self._rows()
        r = other.patch("/api/items/" + self.pan,
                        headers={**H, "X-Approver": foreign.user_id, "X-Approver-Pin": "573921"},
                        json={"name_en": "Unauthorized approved rename"})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self._rows(), before)
