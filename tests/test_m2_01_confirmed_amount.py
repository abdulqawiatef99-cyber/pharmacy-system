"""#324 M2-01, second half: the amount a transfer proof confirms. On a 10,000 transfer split over two wallets,
approving one proof for 6,000 says only 6,000 came: the transfers report counts 6,000 as verified, not 10,000,
and only what was confirmed funds a cash or card refund. Needs the `confirmed_amount` column of 0038.
Synthetic data only."""
from app.core import notify
from app.core.errors import Conflict, ValidationFailed
from app.sales import attachments as att
from app.sales.service import local_today
from tests.test_attachments import PNG
from tests.test_cashier_acceptance import Base
from tests.test_review_m2_sales_proofs import transfer_sale


class TestConfirmedAmount(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 10000)
        self.sale = transfer_sale(self, units=4)                     # 8,000 by transfer (4 units of 20.00)
        self.aid = att.attach(self.db, self.tmp, self.ctx, self.sale["id"], PNG)["attachments"][0]["id"]

    def report(self):
        d = local_today().isoformat()
        return att.transfers(self.db, self.ctx, d, d)

    def test_a_part_confirmed_is_what_the_report_counts(self):
        att.review(self.db, self.ctx, self.aid, True, amount=5000)
        t = self.report()
        self.assertEqual((t["rows"][0]["verified"], t["rows"][0]["state"]), (5000, "problem"))
        self.assertEqual((t["totals"]["ok"], t["totals"]["problem"]), (5000, 3000))
        self.assertIn("transfer_problem", [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]])

    def test_only_what_was_confirmed_funds_a_refund(self):
        att.review(self.db, self.ctx, self.aid, True, amount=2000)  # one unit's worth came
        line = self.sale["lines"][0]["id"]
        self.s.return_items(self.ctx, self.sale["id"], {line: 1}, refund_method="cash", reason="مرتجع عميل")
        with self.assertRaises(Conflict) as e:
            self.s.return_items(self.ctx, self.sale["id"], {line: 1}, refund_method="cash", reason="مرتجع عميل")
        self.assertEqual(e.exception.details["max_cash"], 0)

    def test_the_whole_transfer_confirmed_as_before(self):
        att.review(self.db, self.ctx, self.aid, True)
        t = self.report()
        self.assertEqual((t["rows"][0]["verified"], t["rows"][0]["state"], t["totals"]["ok"]), (8000, "ok", 8000))
        line = self.sale["lines"][0]["id"]
        self.s.return_items(self.ctx, self.sale["id"], {line: 4}, refund_method="cash", reason="مرتجع عميل")

    def test_two_proofs_add_up(self):
        att.review(self.db, self.ctx, self.aid, True, amount=5000)
        other = att.attach(self.db, self.tmp, self.ctx, self.sale["id"], PNG + b"2")["attachments"][1]["id"]
        att.review(self.db, self.ctx, other, True, amount=3000)
        t = self.report()
        self.assertEqual((t["rows"][0]["verified"], t["rows"][0]["state"]), (8000, "ok"))

    def test_an_amount_is_a_whole_positive_number_within_the_transfer(self):
        for bad in (0, -1, 8001, True, "50", 12.5):
            with self.subTest(bad=bad), self.assertRaises(ValidationFailed):
                att.review(self.db, self.ctx, self.aid, True, amount=bad)

    def test_the_route_takes_the_amount(self):
        import inspect

        from app.api import app as api_app
        src = inspect.getsource(api_app)
        at = src.index("async def attachment_review(req):")
        self.assertIn('amount=', src[at:at + 400])


class TestTheScreenAsksTheAmount(Base):
    def test_a_partial_button_sends_the_amount(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-rvpart", js)
        at = js.index('querySelectorAll("[data-rvpart]")')
        self.assertIn("body: { ok: true, amount", js[at:at + 600])
