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
        # the goods are on the road (a return still waits), but the courier carries no money
        self.assertEqual((r["delivery"]["status"], r["delivery"]["cash_due"]), ("out", 0))
        self.assertEqual([x["cash_due"] for x in self.s.open_deliveries(self.ctx)], [0])
        sm = self.cd.summary(self.ctx)
        self.assertEqual((sm["expected_cash"], sm["with_courier"]), (10000, 0))
        self.s.delivery_status(self.ctx, d["id"], "settled")      # «اتسلّمت — اقفلها»: nothing enters the drawer
        self.assertEqual(self.cd.summary(self.ctx)["expected_cash"], 10000)
        self.assertEqual(self.s.open_deliveries(self.ctx), [])

    def test_card_delivery_too(self):
        d = self.delivery()
        r = self.done(d, [{"method": "card", "amount": 5000, "channel_id": self.visa}])
        self.assertEqual(r["delivery"]["cash_due"], 0)
        self.assertEqual(self.cd.summary(self.ctx)["with_courier"], 0)

    def test_sold_with_no_shift_open_joins_the_shift_it_is_closed_in(self):
        # review P1: a card delivery sold before any shift opened must still reach a shift's card total
        sm = self.cd.summary(self.ctx)
        self.cd.close(self.ctx, sm["expected_cash"])
        d = self.delivery()
        self.done(d, [{"method": "card", "amount": 5000, "channel_id": self.visa}])
        self.assertIsNone(self.db.one("SELECT shift_id FROM sales WHERE id=?", (d["id"],))["shift_id"])
        self.cd.open(self.ctx, 0)
        self.s.delivery_status(self.ctx, d["id"], "settled")
        self.assertIsNotNone(self.db.one("SELECT shift_id FROM sales WHERE id=?", (d["id"],))["shift_id"])
        self.assertEqual(self.cd.summary(self.ctx)["card_total"], 5000)

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

    def test_panel_keeps_non_cash_out_of_the_hand_in_list(self):
        js = Path("app/web/app.js").read_text(encoding="utf-8")
        at = js.index('$("#posDeliv").addEventListener')
        body = js[at:at + 3000]
        self.assertIn("d.cash_due > 0", body)
        self.assertIn('id="dlvNoCash"', body)

    def test_message_after_saving_names_the_courier_only_for_cash(self):
        js = Path("app/web/app.js").read_text(encoding="utf-8")
        at = js.index("toast(`✓ تم البيع — فاتورة رقم ${done.sale_no}")
        line = js[at:js.index("\n", at)]
        self.assertIn("done.delivery", line)
        self.assertIn("cash_due", line)                           # the courier line follows the cash he carries
        self.assertNotIn('done.kind === "delivery" ? " (توصيل: المبلغ مع مندوب التوصيل حتى يسلّمه)"', line)
