"""An invoice-level discount larger than the lines must never be approved: it gave a negative invoice
total, a negative supplier balance and a negative average cost (R4 review). Synthetic data only."""
from decimal import Decimal
from unittest import mock

from app.catalog import service as cat
from app.core.errors import Conflict
from app.inventory import service as inv
from app.procurement import suppliers as sup
from app.purchasing import validation
from app.purchasing.service import Purchasing
from app.sales.service import local_today
from tests.helpers import DBTestCase


class TestExtraDiscountAboveLines(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db)
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                        "public_price": 4000})
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد")
        self.inv = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, self.inv)["invoice"]["version"]
        d = self.p.add_line(self.ctx, self.inv, {"item_id": self.item, "qty": 5, "public_price": 4000,
                                                 "discount_bp": 2000, "batch_no": "X1",
                                                 "expiry_date": "12/2029"}, v)      # 5 x 32.00 = 160.00
        self.version = d["invoice"]["version"]

    def header(self, **changes):
        d = self.p.update_header(self.ctx, self.inv, {"invoice_no": "77", "invoice_date": local_today().isoformat(),
                                                      **changes}, self.version)
        self.version = d["invoice"]["version"]
        return d

    def assert_nothing_posted(self):
        self.assertEqual(self.p.get(self.ctx, self.inv)["invoice"]["status"], "draft")
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), 0)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.item)["total_qty"], 0)

    def test_discount_above_the_lines_blocks_approval(self):
        d = self.header(extra_discount=5000000)
        errors = [i for i in d["issues"] if i["level"] == "error"]
        self.assertEqual([i["code"] for i in errors], ["extra_discount_too_large"])
        self.assertFalse(d["can_approve"])
        with self.assertRaises(Conflict) as e:
            self.p.approve(self.ctx, self.inv, self.version)
        self.assertEqual(e.exception.code, "has_blocking_issues")
        self.assert_nothing_posted()

    def test_it_cannot_be_acknowledged_away(self):
        self.assertNotIn("extra_discount_too_large", validation.ACKNOWLEDGEABLE)
        self.assertNotIn("negative_total", validation.ACKNOWLEDGEABLE)

    def test_approve_itself_refuses_a_negative_total(self):
        """Second line of defence: even if validation let it through."""
        self.header(extra_discount=5000000)
        with mock.patch("app.purchasing.service.validation.blocking", return_value=[]):
            with self.assertRaises(Conflict) as e:
                self.p.approve(self.ctx, self.inv, self.version)
        self.assertEqual(e.exception.code, "negative_total")
        self.assert_nothing_posted()

    def test_discount_equal_to_or_below_the_lines_is_still_fine(self):
        d = self.header(extra_discount=6000)
        self.assertEqual([i for i in d["issues"] if i["level"] == "error"], [])
        d = self.p.approve(self.ctx, self.inv, self.version)
        self.assertEqual(d["invoice"]["computed_total"], 10000)
        self.assertEqual(sup.balance(self.db, self.ctx.org_id, self.sup), 10000)
        self.assertEqual(Decimal(inv.stock_for_item(self.db, self.ctx, self.item)["avg_cost"]), Decimal("10"))
