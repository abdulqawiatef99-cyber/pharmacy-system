"""U3-3 Hikma claim in the program: per branch customer, any period, half-margin rule rounded once, lines by value,
returns of earlier periods taken off, the advance, the account reconciliation, cost rights."""
from dataclasses import replace

from app.core.errors import Forbidden, ValidationFailed
from app.db import new_id
from app.reports import hikma_claim as hk
from tests.helpers import today
from tests.test_sales import SalesCase


class TestHikmaClaim(SalesCase):
    def setUp(self):
        super().setUp()
        self.cust = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO customers (id, org_id, name, credit_limit, notes) VALUES (?,?,?,?,?)",
                            (self.cust, self.ctx.org_id, "مستشفى تجربة شبين", 10_000_000, "كود إي ستوك: 50"))

    def sell(self, units, disc=0):
        sid = self.s.create(self.ctx, self.cust)
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        if disc:
            d = self.s.update_line(self.ctx, sid, d["lines"][0]["id"], discount_amount=disc)
        return self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))

    def test_half_margin_rounded_once_and_advance(self):
        self.assertEqual(hk.settings(self.db, self.ctx.org_id)["shibin"]["customer_id"], self.cust)   # code 50
        self.sell(3)                                  # OLD lot 3 x 19.00 = 57.00, cost 3 x 15 = 45 -> margin 12
        self.sell(1, disc=1)                          # NEW lot 20.00 - 0.01 = 19.99, cost 16 -> margin 3.99
        r = hk.claim(self.db, self.ctx, "shibin", today(), today())
        ph = r["pharmacy"]
        # the cost is the real one each sale recorded: the moving average 205 / 13 = 15.769231 a strip
        self.assertEqual((ph["value"], ph["cost"], ph["margin"]), (7699, 6308, 1391))
        self.assertEqual(ph["hospital_half"], 696)    # 13.91 / 2 = 6.955 -> 6.96, once on the total
        self.assertEqual(ph["due"], 7699 - 696)
        self.assertEqual([l["value"] for l in ph["lines"]], [7699])     # one item, one line
        self.assertEqual(r["net"], r["due"] - 1_000_000)
        self.assertEqual(r["account"]["net"], 7699)
        self.assertEqual(r["account"]["matched_bp"], 10000)

    def test_returns_net_and_previous_period_returns(self):
        d = self.sell(2)
        lid = d["lines"][0]["id"]
        self.s.return_items(self.ctx, d["id"], {lid: 1}, refund_method="credit", reason="test")
        r = hk.claim(self.db, self.ctx, "shibin", today(), today())
        self.assertEqual(r["pharmacy"]["lines"][0]["qty"], 1)
        # the same return seen as one of an earlier period's sale
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at='2026-01-05T10:00:00.000000Z' WHERE id=?", (d["id"],))
        r = hk.claim(self.db, self.ctx, "shibin", today(), today())
        pr = r["previous_returns"]
        self.assertEqual(pr["value"], 1900)
        self.assertEqual(pr["due"], 1900 - 162)       # cost 15.77 + half of 3.23 (1.615 -> 1.62)
        self.assertEqual(r["due"], -pr["due"])

    def test_rights_and_inputs(self):
        with self.assertRaises(Forbidden):
            hk.claim(self.db, replace(self.ctx, permissions=self.ctx.permissions - {"costs.read"}), "shibin", today(), today())
        with self.assertRaises(ValidationFailed):
            hk.claim(self.db, self.ctx, "cairo", today(), today())
        with self.assertRaises(ValidationFailed):
            hk.claim(self.db, self.ctx, "mitkhalaf", today(), today())   # no customer with code 10
        hk.save_settings(self.db, self.ctx, "mitkhalaf", self.cust, 500_000)
        self.assertEqual(hk.claim(self.db, self.ctx, "mitkhalaf", today(), today())["advance"], 500_000)

    def test_other_pharmacy(self):
        _, other = self.second_tenant()
        with self.assertRaises(ValidationFailed):
            hk.claim(self.db, other, "shibin", today(), today())


from tests.test_owner_plan_priority_1_2 import PlanCase          # noqa: E402


class TestHikmaAPI(PlanCase):
    def test_settings_and_claim_through_the_screen(self):
        cust = self.call("POST", "/api/customers", 201, json={"name": "مستشفى تجربة"})["id"]
        self.call("PUT", "/api/settings/hikma-claim", json={"branch": "shibin", "customer_id": cust, "advance": 1000000})
        r = self.call("GET", f"/api/reports/hikma-claim?branch=shibin&from={self.today}&to={self.today}")
        self.assertEqual((r["due"], r["advance"]), (0, 1000000))
        self.call("GET", "/api/reports/hikma-claim?branch=shibin&from=x&to=y", 422)


class TestHikmaReviewFixes(TestHikmaClaim):
    def test_reconciliation_ignores_returns_of_older_sales(self):
        old = self.sell(1)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at='2026-01-05T10:00:00.000000Z' WHERE id=?", (old["id"],))
        self.s.return_items(self.ctx, old["id"], {old["lines"][0]["id"]: 1}, refund_method="credit", reason="test")
        self.sell(2)
        r = hk.claim(self.db, self.ctx, "shibin", today(), today())
        self.assertEqual(r["account"]["matched_bp"], 10000)              # the old return is its own line

    def test_hospital_lot_return_takes_off_half_margin_only(self):
        d = self.sell(1)
        with self.db.transaction():
            self.db.execute("UPDATE batches SET owner='hospital' WHERE item_id=?", (self.pan,))
            self.db.execute("UPDATE sales SET completed_at='2026-01-05T10:00:00.000000Z' WHERE id=?", (d["id"],))
        self.s.return_items(self.ctx, d["id"], {d["lines"][0]["id"]: 1}, refund_method="credit", reason="test")
        pr = hk.claim(self.db, self.ctx, "shibin", today(), today())["previous_returns"]
        margin = 1900 - pr["cost"]
        self.assertEqual(pr["due"], margin - (margin + 1) // 2)

    def test_bad_settings_input(self):
        with self.assertRaises(ValidationFailed):
            hk.save_settings(self.db, self.ctx, "shibin", ["x"], 0)


class TestCodexOn416(TestHikmaClaim):
    def test_old_return_without_line_refunds_keeps_its_value(self):
        from app.core import audit
        from app.catalog import service as cat
        from app.inventory import service as inv
        from decimal import Decimal
        other = cat.create_item(self.db, self.ctx, {"name_en": "Other Item", "public_price": 1000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=other, qty_delta=5, movement_type="opening", unit_cost=Decimal("6"))
        sid = self.s.create(self.ctx, self.cust)
        self.s.add_line(self.ctx, sid, self.pan, units=1)
        d = self.s.add_line(self.ctx, sid, other, units=1)
        d = self.s.complete(self.ctx, sid, on_credit=d["total"], safety_decisions=self.ack_all(sid))
        rid = new_id()
        lines = {l["id"]: 1 for l in d["lines"]}
        with self.db.transaction():          # a return booked before line_refunds existed: its total only
            self.db.execute("UPDATE sales SET completed_at='2026-01-05T10:00:00.000000Z' WHERE id=?", (sid,))
            self.db.execute("INSERT INTO sale_returns (id, org_id, sale_id, total, refund_method, reason, created_by,"
                            " created_at) VALUES (?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                            (rid, self.ctx.org_id, sid, d["total"], "credit", "قديم", self.ctx.user_id))
            audit.record(self.db, self.ctx, "sale.return", "sale", sid, after={"return_id": rid, "lines": lines})
        pr = hk.claim(self.db, self.ctx, "shibin", today(), today())["previous_returns"]
        self.assertEqual(pr["value"], d["total"])


class TestCodexOn416Replay(TestHikmaClaim):
    def test_successive_returns_take_each_lot_once(self):
        d = self.sell(4)                                  # 3 from OLD, 1 from NEW (the newest lot)
        lid = d["lines"][0]["id"]
        with self.db.transaction():
            self.db.execute("UPDATE batches SET owner='hospital' WHERE item_id=? AND batch_no='NEW'", (self.pan,))
            self.db.execute("UPDATE sales SET completed_at='2026-01-05T10:00:00.000000Z' WHERE id=?", (d["id"],))
        self.s.return_items(self.ctx, d["id"], {lid: 1}, refund_method="credit", reason="test")
        self.s.return_items(self.ctx, d["id"], {lid: 1}, refund_method="credit", reason="test")
        by = hk.claim(self.db, self.ctx, "shibin", today(), today())["previous_returns"]["by_owner"]
        self.assertGreater(by["hospital"], 0)             # the first return took the one hospital unit
        self.assertGreater(by["pharmacy"], 0)             # the second one an OLD (pharmacy) unit, not the same lot again


class TestShare(__import__("unittest").TestCase):
    def test_keeps_the_whole_refund(self):
        """Codex P1 on #416: weights summing below the refund (300 x 3,333 < 1,000,000) keep the whole refund."""
        self.assertEqual(sum(hk._share(1_000_000, [300 * 3333])), 1_000_000)
        self.assertEqual(hk._share(10, [1, 1, 1]), [4, 3, 3])
        self.assertEqual(hk._share(5, [0, 0]), [5, 0])
