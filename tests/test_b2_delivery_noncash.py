"""B2 (owner 22/11 19:44): a delivery paid by InstaPay / card / wallet. The courier carries CASH only, so a delivery
with no cash part never appears in the courier's hand-in list, never in the cash «with the courier», and never in the
drawer's expected cash; the message after saving says nothing about the courier handing in money. Synthetic data."""
from pathlib import Path

from tests.test_cashier_v2 import V2


class TestNonCashDelivery(V2):
    def delivery(self):
        d = self.draft()
        return self.s.set_details(self.ctx, d["id"], {"kind": "delivery", "delivery": {"address": "شارع تجربة",
                                                                                    "phone": "0100", "fee": 1000}})

    def test_wallet_delivery_has_nothing_to_hand_in(self):
        d = self.delivery()
        r = self.done(d, [{"method": "transfer", "amount": 5000, "channel_id": self.insta}])
        self.assertEqual((r["delivery"]["status"], r["delivery"]["cash_due"]), ("settled", 0))
        self.assertEqual(self.s.open_deliveries(self.ctx), [])
        sm = self.cd.summary(self.ctx)
        self.assertEqual((sm["expected_cash"], sm["with_courier"]), (10000, 0))
        n = self.db.one("SELECT count(*) n FROM audit_log WHERE action='delivery.settled' AND entity_id=?", (d["id"],))["n"]
        self.assertEqual(n, 1)                                    # recorded as settled at the sale, nothing handed in

    def test_card_delivery_too(self):
        d = self.delivery()
        r = self.done(d, [{"method": "card", "amount": 5000, "channel_id": self.visa}])
        self.assertEqual(r["delivery"]["status"], "settled")
        self.assertEqual(self.s.open_deliveries(self.ctx), [])

    def test_void_of_a_wallet_delivery_takes_no_cash_from_the_drawer(self):
        d = self.delivery()
        self.done(d, [{"method": "transfer", "amount": 5000, "channel_id": self.insta}])
        self.s.void(self.ctx, d["id"], "العميل رفض الاستلام")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 10000)

    def test_mixed_cash_and_card_hands_in_the_cash_part_only(self):
        d = self.delivery()
        r = self.done(d, [{"method": "cash", "amount": 1000}, {"method": "card", "amount": 4000, "channel_id": self.visa}])
        self.assertEqual((r["delivery"]["status"], r["delivery"]["cash_due"]), ("out", 1000))
        self.assertEqual([x["cash_due"] for x in self.s.open_deliveries(self.ctx)], [1000])
        self.assertEqual(self.cd.summary(self.ctx)["with_courier"], 1000)
        self.s.delivery_status(self.ctx, d["id"], "settled")
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 11000)

    def test_cash_delivery_unchanged(self):
        d = self.delivery()
        r = self.done(d, [{"method": "cash", "amount": 5000}])
        self.assertEqual((r["delivery"]["status"], r["delivery"]["cash_due"]), ("out", 5000))

    def test_message_after_saving_names_the_courier_only_for_cash(self):
        js = Path("app/web/app.js").read_text(encoding="utf-8")
        at = js.index("toast(`✓ تم البيع — فاتورة رقم ${done.sale_no}")
        line = js[at:js.index("\n", at)]
        self.assertIn("done.delivery", line)
        self.assertIn("cash_due", line)                           # the courier line follows the cash he carries
        self.assertNotIn('done.kind === "delivery" ? " (توصيل: المبلغ مع مندوب التوصيل حتى يسلّمه)"', line)
