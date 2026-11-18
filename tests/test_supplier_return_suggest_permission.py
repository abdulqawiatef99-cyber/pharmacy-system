"""GET /api/supplier-returns/suggest answers with an item's purchase cost. It had no permission check: any
signed-in user (a cashier) could read costs through it (R4 review). It now needs what recording a return
with a value needs: purchases.approve, and the right to see values (costs.read or suppliers.pay).
Synthetic data only."""
from starlette.testclient import TestClient

from app.catalog import service as cat
from app.core import people
from app.core.errors import DomainError
from app.procurement import returns, suppliers as sup
from tests.test_owner_plan_priority_1_2 import PW, PlanCase


class SuggestPermission(PlanCase):
    def setUp(self):
        super().setUp()
        self.batch = self.db.one("SELECT id FROM batches WHERE item_id=?", (self.pan,))["id"]
        self.url = f"/api/supplier-returns/suggest?item={self.pan}&batch={self.batch}"
        for name, role in (("asst1", "assistant"), ("pharm1", "pharmacist"), ("mgr1", "manager")):
            people.add_user(self.db, self.ctx, username=name, full_name=name, password=PW, role_code=role)

    def as_user(self, name):
        c = TestClient(self.app)
        self.http_login(name, PW, client=c)
        return c

    def test_owner_and_manager_get_the_value(self):
        self.assertEqual(self.call("GET", self.url)["unit_value"], 1500)
        r = self.as_user("mgr1").get(self.url)
        self.assertEqual((r.status_code, r.json().get("unit_value")), (200, 1500))

    def test_a_cashier_gets_no_cost(self):
        for url in (self.url, f"/api/supplier-returns/suggest?item={self.pan}&batch="):
            r = self.as_user("asst1").get(url)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertNotIn("unit_value", r.text)
        with self.assertRaises(DomainError):
            returns.suggest_value(self.db, self.login("asst1", PW), self.pan, self.batch)

    def test_who_may_return_but_not_see_values_gets_no_cost_and_can_still_return(self):
        c = self.as_user("pharm1")
        r = c.get(self.url)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertNotIn("unit_value", r.text)
        s = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        from tests.test_api import H
        r = c.post("/api/supplier-returns", headers=H, json={"supplier_id": s, "lines": [
            {"item_id": self.pan, "batch_id": self.batch, "qty_base": 1, "reason": "damaged"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("total", r.json())
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, s), -1500)     # valued by the server, unseen
