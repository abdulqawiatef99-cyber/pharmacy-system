"""A supplier file with a negative price or line total must not be approved: the line went into stock
with a negative unit cost, the average cost became negative and the supplier was owed less while the
shelf got more. The screen already refuses a negative value; a file did not pass that check.
Synthetic data only."""
from unittest import mock

from app.catalog import service as cat
from app.core.errors import Conflict
from app.inventory import service as inv
from app.procurement import suppliers as sup
from app.purchasing import validation
from app.purchasing.service import Purchasing
from tests.helpers import DBTestCase

FILE = ("invoice no: 55\nitem,qty,net price,total,batch,expiry\n"
        "Alpha 10mg 10 tab,10,30,300,A1,12/2029\n"
        "Beta 20mg 20 tab,2,-40,-80,B1,12/2029\n"
        "total,,,220\n").encode()


class TestNegativePurchaseLine(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db)
        self.a = cat.create_item(self.db, self.ctx, {"name_en": "Alpha 10mg 10 tab", "public_price": 4000})
        self.b = cat.create_item(self.db, self.ctx, {"name_en": "Beta 20mg 20 tab", "public_price": 5000})
        self.sup = cat.create_supplier(self.db, self.ctx, name="Supplier One")
        self.inv = self.p.import_file(self.ctx, FILE, "x.csv", supplier_id=self.sup)
        d = self.p.get(self.ctx, self.inv)
        self.version = d["invoice"]["version"]
        for l in d["lines"]:                    # whatever the matcher decided, the lines are confirmed
            item = self.a if "Alpha" in l["raw_name"] else self.b
            self.version = self.p.update_line(self.ctx, self.inv, l["id"], {"item_id": item},
                                              self.version)["invoice"]["version"]

    def assert_nothing_posted(self):
        self.assertEqual(self.p.get(self.ctx, self.inv)["invoice"]["status"], "draft")
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), 0)
        self.assertEqual(self.db.one("SELECT count(*) c FROM stock_movements")["c"], 0)
        self.assertEqual(self.db.one("SELECT count(*) c FROM item_costs")["c"], 0)

    def test_negative_values_block_approval(self):
        d = self.p.get(self.ctx, self.inv)
        neg = sorted(i["field"] for i in d["issues"] if i["code"] == "negative_amount")
        self.assertEqual(neg, ["line_total", "unit_net_price"])
        self.assertFalse(d["can_approve"])
        with self.assertRaises(Conflict) as e:
            self.p.approve(self.ctx, self.inv, self.version)
        self.assertEqual(e.exception.code, "has_blocking_issues")
        self.assert_nothing_posted()

    def test_it_cannot_be_acknowledged_away(self):
        self.assertNotIn("negative_amount", validation.ACKNOWLEDGEABLE)

    def test_approve_itself_refuses_a_negative_line(self):
        with mock.patch("app.purchasing.service.validation.blocking", return_value=[]):
            with self.assertRaises(Conflict) as e:
                self.p.approve(self.ctx, self.inv, self.version)
        self.assertEqual(e.exception.code, "negative_amount")
        self.assert_nothing_posted()

    def test_the_corrected_file_line_is_approved(self):
        d = self.p.get(self.ctx, self.inv)
        bad = next(l for l in d["lines"] if "Beta" in l["raw_name"])
        d = self.p.update_line(self.ctx, self.inv, bad["id"], {"unit_net_price": 4000, "line_total": 8000},
                               self.version)
        d = self.p.update_header(self.ctx, self.inv, {"declared_total": 38000}, d["invoice"]["version"])
        self.assertTrue(d["can_approve"], d["issues"])
        self.p.approve(self.ctx, self.inv, d["invoice"]["version"])
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), 38000)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.b)["total_qty"], 2)
