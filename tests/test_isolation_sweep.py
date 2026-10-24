"""Every route that takes an id is called by ANOTHER pharmacy with pharmacy A's
real ids. Nothing may succeed (2xx) or leak A's data. The route list is read
from the app itself, so new routes are covered automatically."""
import re
from datetime import date, timedelta
from decimal import Decimal

from starlette.routing import Route
from starlette.testclient import TestClient

from app.catalog import service as cat
from app.inventory import service as inv
from app.inventory.counting import Counting
from app.recalls.service import Recalls
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.fixtures.make_invoices import excel_invoice
from tests.test_api import APICase, H

FUT = (date.today() + timedelta(days=700)).strftime("%m/%Y")


class TestIsolationSweep(APICase):
    def test_no_route_leaks_or_changes_other_tenant_data(self):
        ctx = self.ctx
        with self.db.transaction():
            inv.post_movement(self.db, ctx, item_id=self.pan, qty_delta=10, movement_type="opening",
                              unit_cost=Decimal("15"))
        self.http_login()
        inv_id = self.c.post("/api/purchases/import", headers=H, files={"file": ("a.xlsx", excel_invoice(
            [("PANADOL 500 MG 24 TAB", 1, 0, 40, 20, 32, 32, "P1", FUT, None)]), "x")}).json()["invoice"]["id"]
        d = self.c.get(f"/api/purchases/{inv_id}").json()
        CashDrawer(self.db).open(ctx, 0)
        s = Sales(self.db)
        sale = s.create(ctx)
        sd = s.add_line(ctx, sale, self.pan, units=1)
        f = s.safety_check(ctx, sale)
        s.complete(ctx, sale, paid_cash=sd["total"], safety_decisions={i: "x" for i in range(len(f))})
        cust = s.create_customer(ctx, name="عميل أ", credit_limit=1000)
        count = Counting(self.db).create(ctx, title="جرد")
        Recalls(self.db).add_notice(ctx, title="سحب", items=[{"product_name": "Panadol", "batch_numbers": ["P1"]}])
        ids = {"id": [self.pan, inv_id, sale, cust, count, d["invoice"]["source_file_id"]],
               "line_id": [d["lines"][0]["id"]], "kind": ["items"]}
        before = {t: self.db.one(f"SELECT count(*) FROM {t}")[0] for t in
                  ("items", "stock_movements", "sales", "purchase_invoices", "customer_ledger", "cash_movements")}

        self.second_tenant()
        other = TestClient(self.app)
        self.http_login("other", "Other-Pass-2026", client=other)
        leaks, tried = [], 0
        for r in self.app.routes:
            if not isinstance(r, Route) or "{" not in r.path:
                continue
            params = re.findall(r"{(\w+)}", r.path)
            for method in sorted(r.methods - {"HEAD"}):
                for value in ids.get(params[0], ["x"]):
                    path = r.path
                    for p in params:
                        path = path.replace("{" + p + "}", value if p == params[0] else ids.get(p, ["x"])[0])
                    tried += 1
                    resp = other.request(method, path, headers=H, json={"version": 1, "reason": "محاولة اختراق",
                                                                        "changes": {"qty": 1}})
                    body = resp.text
                    if resp.status_code < 300 and r.path != "/api/imports/templates/{kind}.xlsx":
                        leaks.append((method, path, resp.status_code))
                    if any(v in body for v in (self.pan, inv_id, sale, cust)) and resp.status_code < 300:
                        leaks.append((method, path, "id echoed"))
        self.assertGreater(tried, 40)
        self.assertEqual(leaks, [])
        after = {t: self.db.one(f"SELECT count(*) FROM {t}")[0] for t in before}
        self.assertEqual(before, after)                  # nothing of A was changed
