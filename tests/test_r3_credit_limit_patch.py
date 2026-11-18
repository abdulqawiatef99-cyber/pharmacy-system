"""PATCH /api/customers/{id}: the credit limit is a whole number of piastres, as on create.
Synthetic data only."""
from app.sales.service import Sales
from tests.test_api import APICase, H


class TestCreditLimitPatch(APICase):
    def setUp(self):
        super().setUp()
        self.cid = Sales(self.db).create_customer(self.ctx, name="عميل تجربة", credit_limit=5000)
        self.http_login()

    def patch(self, value):
        return self.c.patch(f"/api/customers/{self.cid}", headers=H, json={"credit_limit": value})

    def limit(self):
        return self.db.one("SELECT credit_limit FROM customers WHERE id=? AND org_id=?", (self.cid, self.ctx.org_id))[0]

    def test_true_and_huge_and_broken_values_are_refused(self):
        for bad in (True, False, 2 ** 70, 2 ** 53, -1, 10.5, "abc", None, [1]):
            with self.subTest(value=bad):
                r = self.patch(bad)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(self.limit(), 5000)

    def test_a_whole_number_is_saved(self):
        r = self.patch(20000)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["credit_limit"], self.limit()), (20000, 20000))
        self.assertEqual(self.patch(0).json()["credit_limit"], 0)
