"""#19 operating-day script (docs/DAY19_SCRIPT.md): the exact entries the owner makes, run through HTTP.

The expected end-of-day figures in the script come from this test, so the owner can reconcile the
real day against numbers the app itself produced. Synthetic data only; no hardware.
The script quotes the screens word for word («…»); TestDay19ScriptWording keeps those quotes true.
"""
import re
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.catalog import service as cat
from app.inventory import service as inventory
from app.core import audit
from tests.test_api import APICase, H


class TestDay19Script(APICase):
    def req(self, method, url, expected=200, **kw):
        r = self.c.request(method, url, headers=H, **kw)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def stock(self, item):
        return self.req("GET", f"/api/items/{item}")["stock"]["total_qty"]

    def sale(self, lines, *, customer=None, cash=0, credit=0, tag):
        sid = self.req("POST", "/api/sales", expected=201, json={"customer_id": customer})["id"]
        draft = None
        for item, units, disc in lines:
            for _ in range(units):              # entered one unit at a time, as the cashier does
                draft = self.req("POST", f"/api/sales/{sid}/lines", json={"item_id": item, "units": 1, "discount_bp": disc})
        findings = self.req("POST", f"/api/sales/{sid}/check", json={})["findings"]
        return self.req("POST", f"/api/sales/{sid}/complete",
                        json={"paid_cash": cash, "on_credit": credit, "version": draft["version"],
                              "safety_decisions": {str(i): "ok" for i in range(len(findings))},
                              "request_id": "day19-" + tag})

    def test_day19_script_figures(self):
        a = cat.create_item(self.db, self.ctx, {"name_ar": "صنف تجربة أ", "pack_size": 3, "public_price": 3100})
        b = cat.create_item(self.db, self.ctx, {"name_ar": "صنف تجربة ب", "pack_size": 1, "public_price": 5000})
        sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.http_login()
        expiry = (date.today() + timedelta(days=700)).isoformat()

        # 1. purchase: A 10 boxes + 2 bonus at 20.00, B 5 boxes at 40.00
        inv = self.req("POST", "/api/purchases", expected=201, json={"supplier_id": sup, "invoice_no": "TEST-19"})
        iid = inv["invoice"]["id"]
        for item, qty, bonus, net, public, batch in ((a, 10, 2, 2000, 3100, "T19A"), (b, 5, 0, 4000, 5000, "T19B")):
            v = self.req("GET", f"/api/purchases/{iid}")["invoice"]["version"]
            self.req("POST", f"/api/purchases/{iid}/lines", json={"version": v, "line": {
                "raw_name": "x", "item_id": item, "qty": qty, "bonus_qty": bonus, "unit_net_price": net,
                "public_price": public, "line_total": qty * net, "batch_no": batch, "expiry_date": expiry}})
        d = self.req("GET", f"/api/purchases/{iid}")
        self.assertTrue(d["can_approve"], d["issues"])
        self.req("POST", f"/api/purchases/{iid}/approve", json={"version": d["invoice"]["version"]})
        self.assertEqual((self.stock(a), self.stock(b)), (36, 5))
        self.assertEqual(self.req("GET", f"/api/suppliers/{sup}")["balance"], 40000)

        # 2. open the shift with 100.00
        self.req("POST", "/api/cash/open", expected=201, json={"opening_cash": 10000})

        # 3. sale 1, cash: A three strips one by one = the box price exactly
        s1 = self.sale([(a, 3, 0)], cash=3100, tag="s1")
        self.assertEqual(s1["total"], 3100)
        self.assertEqual(len(s1["lines"]), 1)
        # 4. sale 2, cash: B one box with 10 % discount
        s2 = self.sale([(b, 1, 1000)], cash=4500, tag="s2")
        self.assertEqual((s2["subtotal"], s2["discount_total"], s2["total"]), (5000, 500, 4500))
        # 5. return one strip of sale 1, cash
        ret = self.req("POST", f"/api/sales/{s1['id']}/return",
                       json={"lines": {s1["lines"][0]["id"]: 1}, "refund_method": "cash", "reason": "تجربة"})
        self.assertEqual(ret["refund"], 1034)       # 31.00 split 10.33 + 10.33 + 10.34; the return gives back 10.34
        # 6. credit customer: limit 200, credit sale B 50.00, collect 20.00 cash
        cust = self.req("POST", "/api/customers", expected=201, json={"name": "عميل تجربة", "credit_limit": 20000})["id"]
        self.sale([(b, 1, 0)], customer=cust, credit=5000, tag="s3")
        self.assertEqual(self.req("POST", f"/api/customers/{cust}/payments", json={"amount": 2000, "method": "cash"})["balance"], 3000)
        # 7. expense 15.00 from the drawer
        cats = self.req("GET", "/api/expenses")
        self.req("POST", "/api/expenses", expected=201,
                 json={"category_id": cats["categories"][0]["id"], "amount": 1500, "paid_from": "drawer", "note": "تجربة"})
        # 8. loss: A one strip damaged
        self.req("POST", f"/api/items/{a}/adjust", json={"qty_delta": -1, "reason": "damaged", "note": "تجربة"})
        # 9. supplier payment 100.00 by cheque 123456 (outside the drawer)
        self.req("POST", f"/api/suppliers/{sup}/pay", json={"amount": 10000, "method": "cheque", "ref": "123456"})

        # end of day: the figures written in docs/DAY19_SCRIPT.md
        cash = self.req("GET", "/api/cash/current")
        self.assertEqual(cash["expected_cash"], 17066)      # 100 + 31 + 45 - 10.34 + 20 - 15
        self.assertEqual(cash["cash_by_kind"], {"sale": 7600, "refund": -1034, "customer_payment": 2000, "payout": -1500})
        self.assertEqual((cash["credit_total"], cash["sales_count"]), (5000, 3))
        self.assertEqual((self.stock(a), self.stock(b)), (33, 3))
        self.assertEqual(self.req("GET", f"/api/suppliers/{sup}")["balance"], 30000)
        self.assertEqual(self.req("GET", f"/api/customers/{cust}")["balance"], 3000)
        # 12. the close screen counts notes and coins (smallest coin 0.25), so 170.66 is counted as 170.50:
        # one each of 100, 50, 20 and a half pound. A difference needs a note; with it the shift closes 0.16 short.
        count = {"10000": 1, "5000": 1, "2000": 1, "50": 1}
        refused = self.c.post("/api/cash/close", json={"denominations": count}, headers=H)
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (422, "difference_note_required"), refused.text)
        closed = self.req("POST", "/api/cash/close", json={"denominations": count, "note": "تجربة كسور القروش"})
        self.assertEqual((closed["status"], closed["expected_cash"], closed["counted_cash"], closed["difference"]),
                         ("closed", 17066, 17050, -16))
        day = date.today().isoformat()
        t = self.req("GET", f"/api/reports/sales?from={day}&to={day}")["totals"]
        self.assertEqual((t["n"], t["total"], t["returns"], t["discount"]), (3, 12600, 1034, 500))
        losses = self.req("GET", f"/api/reports/losses?from={day}&to={day}")
        self.assertIn("damaged", str(losses))
        self.assertEqual(inventory.verify_balances(self.db, self.ctx.org_id), [])
        self.assertEqual(audit.verify_chain(self.db), [])

    def test_script_refusals_and_screen_only_rules(self):
        """What the script tells the owner he will see at steps 5 and 8 (behaviour of 16/11)."""
        b = cat.create_item(self.db, self.ctx, {"name_ar": "صنف تجربة ب", "pack_size": 1, "public_price": 5000})
        self.http_login()
        self.req("POST", f"/api/items/{b}/adjust", json={"qty_delta": 5, "reason": "found", "note": "تجربة"})
        self.req("POST", "/api/cash/open", expected=201, json={"opening_cash": 10000})
        s1 = self.sale([(b, 1, 0)], cash=5000, tag="r1")
        line = s1["lines"][0]["id"]
        # step 5: a return without a reason is refused; after a return the invoice cannot be voided
        r = self.c.post(f"/api/sales/{s1['id']}/return", json={"lines": {line: 1}, "refund_method": "cash", "reason": ""}, headers=H)
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("سبب المرتجع", r.json()["error"]["message"])
        self.req("POST", f"/api/sales/{s1['id']}/return", json={"lines": {line: 1}, "refund_method": "cash", "reason": "تجربة"})
        r = self.c.post(f"/api/sales/{s1['id']}/void", json={"reason": "تجربة"}, headers=H)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("عليها مرتجع", r.json()["error"]["message"])
        # step 8: a collection above the customer's debt is refused and nothing is recorded
        cust = self.req("POST", "/api/customers", expected=201, json={"name": "عميل تجربة", "credit_limit": 20000})["id"]
        self.sale([(b, 1, 0)], customer=cust, credit=5000, tag="r2")
        r = self.c.post(f"/api/customers/{cust}/payments", json={"amount": 8000, "method": "cash"}, headers=H)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "over_collection"), r.text)
        self.assertEqual(r.json()["error"]["message"], "المبلغ أكبر من المستحق على العميل")
        self.assertEqual(self.req("GET", f"/api/customers/{cust}")["balance"], 5000)


ROOT = Path(__file__).resolve().parent.parent
# what the owner types, not what the screen says
TYPED = {"صنف تجربة أ", "صنف تجربة ب", "مورد تجربة", "عميل تجربة", "تجربة", "تجربة سحب", "تجربة كسور القروش"}


class TestDay19ScriptWording(unittest.TestCase):
    """Every «…» in the owner's script and in his hands-on list is a screen text of today's program, so a
    renamed screen, button or message breaks this test instead of the trial day."""

    def test_every_quoted_text_is_on_a_screen(self):
        ui = "\n".join(p.read_text(encoding="utf-8") for p in
                       [ROOT / "app" / "web" / "app.js", ROOT / "app" / "web" / "index.html",
                        *sorted((ROOT / "app").rglob("*.py"))])
        for name in ("DAY19_SCRIPT.md", "DAY19_OWNER_HANDS.md"):
            doc = (ROOT / "docs" / name).read_text(encoding="utf-8")
            quoted = set(re.findall(r"«([^«»]+)»", doc)) - TYPED
            self.assertGreater(len(quoted), 15, name)
            missing = sorted(q for q in quoted if q not in ui)
            self.assertEqual(missing, [], f"{name} quotes screen text the program no longer has")

    def test_the_script_matches_the_close_screen(self):
        """The close is counted in notes and coins: the script must not ask for an amount no coin can make."""
        from app.sales.cash import DENOMINATIONS
        doc = (ROOT / "docs" / "DAY19_SCRIPT.md").read_text(encoding="utf-8")
        self.assertEqual(min(DENOMINATIONS), 25)
        self.assertEqual(sum((10000, 5000, 2000, 50)), 17050)
        for text in ("170.66", "170.50", "0.16", "100 جنيه", "50 جنيه", "20 جنيه", "«نصف جنيه»"):
            self.assertIn(text, doc)

