"""Purchase invoices: typed quantities and piasters are refused when not whole, never cut down.

`int()` turned 2.5 boxes into 2 (and the approved invoice then put 2 boxes in stock), 1234.5 piasters
into 1234, and `true` into 1, without a word; a declared total of "abc" was a server error.
Synthetic data only.
"""
from app.catalog import service as cat
from tests.test_owner_plan_priority_1_2 import PlanCase


class PurchaseWholeNumbers(PlanCase):
    def setUp(self):
        super().setUp()
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.inv = self.call("POST", "/api/purchases", 201, json={"supplier_id": self.sup, "invoice_no": "W-1"})
        self.inv = self.inv["id"] if "id" in self.inv else self.inv["invoice"]["id"]
        d = self.doc()
        d = self.call("POST", f"/api/purchases/{self.inv}/lines", json={"version": d["invoice"]["version"], "line": {
            "raw_name": "x", "item_id": self.pan, "qty": 2, "bonus_qty": 0, "unit_net_price": 2000,
            "public_price": 4000, "line_total": 4000, "expiry_date": self.expiry, "batch_no": "W-1"}})
        self.line_id = d["lines"][0]["id"]

    def doc(self):
        return self.call("GET", f"/api/purchases/{self.inv}")

    def line(self):
        return self.doc()["lines"][0]

    def test_line_quantities_and_prices_must_be_whole(self):
        before = {k: self.line()[k] for k in ("qty", "bonus_qty", "unit_net_price", "public_price", "line_total")}
        for field, bad in (("qty", 2.5), ("qty", "2.5"), ("qty", True), ("bonus_qty", 0.5),
                           ("unit_net_price", 1999.5), ("public_price", 3999.9), ("line_total", 4000.4),
                           ("tax_amount", 0.5), ("discount_bp", 12.5)):
            with self.subTest(field=field, bad=bad):
                v = self.doc()["invoice"]["version"]
                self.call("PATCH", f"/api/purchases/{self.inv}/lines/{self.line_id}", 422,
                          json={"version": v, "changes": {field: bad}})
                self.assertEqual({k: self.line()[k] for k in before}, before)
        v = self.doc()["invoice"]["version"]
        self.call("PATCH", f"/api/purchases/{self.inv}/lines/{self.line_id}",
                  json={"version": v, "changes": {"qty": "3", "line_total": 6000.0}})
        self.assertEqual((self.line()["qty"], self.line()["line_total"]), (3, 6000))

    def test_header_amounts_must_be_whole(self):
        for field, bad in (("declared_total", 4000.5), ("declared_total", "abc"), ("extra_discount", 0.5),
                           ("extra_tax", True)):
            with self.subTest(field=field, bad=bad):
                d = self.doc()
                self.call("PATCH", f"/api/purchases/{self.inv}", 422,
                          json={"version": d["invoice"]["version"], "changes": {field: bad}})
                self.assertEqual(self.doc()["invoice"][field] or 0, d["invoice"][field] or 0)
        d = self.doc()
        self.call("PATCH", f"/api/purchases/{self.inv}", json={"version": d["invoice"]["version"],
                                                              "changes": {"declared_total": "4000"}})
        self.assertEqual(self.doc()["invoice"]["declared_total"], 4000)

    def test_the_approved_stock_is_what_was_typed(self):
        stock = self.qty(self.pan)
        d = self.doc()
        self.call("PATCH", f"/api/purchases/{self.inv}", json={"version": d["invoice"]["version"],
                                                              "changes": {"declared_total": d["invoice"]["computed_total"]}})
        d = self.doc()
        self.call("POST", f"/api/purchases/{self.inv}/approve", json={"version": d["invoice"]["version"]})
        self.assertEqual(self.qty(self.pan), stock + 2 * 2)                 # 2 boxes of 2 strips


JS_PATH = __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "web" / "app.js"


class PurchaseScreenSendsWhatWasTyped(__import__("unittest").TestCase):
    """The screen cut 2.5 to 2 with parseInt before the server could refuse it: it now sends what was typed."""

    def setUp(self):
        self.js = JS_PATH.read_text(encoding="utf-8")

    def test_purchase_fields_and_quick_add_do_not_cut(self):
        self.assertIn("qty: (v) => typedWhole(v),", self.js)
        self.assertIn("bonus_qty: (v) => typedWhole(v) ?? 0,", self.js)
        self.assertIn('const qty = typedWhole($("#qaQty").value);', self.js)
        self.assertNotIn('parseInt($("#qaQty").value', self.js)
        self.assertNotIn('parseInt($("#qaBonus").value', self.js)

    def test_typed_whole(self):
        import json
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("Node not available")
        fn = re.search(r"\nconst typedWhole = .*?\n", self.js).group(0)
        code = fn + 'console.log(JSON.stringify(["", " ", "3", " 3 ", "2.5", "abc", "-1", "0", null, undefined, 4].map(typedWhole)));'
        r = subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [None, None, 3, 3, "2.5", "abc", -1, 0, None, None, 4])
