from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from app.procurement.service import Procurement
from tests.helpers import DBTestCase

TODAY = date(2026, 10, 24)


class ProcCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Procurement(self.db, today=TODAY)
        self.fast = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                        "public_price": 4000})
        self.slow = cat.create_item(self.db, self.ctx, {"name_en": "Rare Cream 20gm", "public_price": 9000})
        self.noisy = cat.create_item(self.db, self.ctx, {"name_en": "Seasonal Syrup 100ml",
                                                         "public_price": 3000})
        # 3 monthly periods: 60 strips each for Panadol (2/day)
        for m in range(3):
            end = TODAY - timedelta(days=30 * m + 1)
            start = end - timedelta(days=29)
            self.p.record_consumption(self.ctx, self.fast, start.isoformat(), end.isoformat(), 60)
            self.p.record_consumption(self.ctx, self.noisy, start.isoformat(), end.isoformat(),
                                      [5, 60, 10][m])
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.fast, qty_delta=10, movement_type="opening",
                              unit_cost=Decimal("15"))
            b = inv.get_or_create_batch(self.db, self.ctx, self.slow, "R1",
                                        (TODAY + timedelta(days=60)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.slow, qty_delta=4, batch_id=b,
                              movement_type="opening", unit_cost=Decimal("60"))


class TestSuggest(ProcCase):
    def test_reorder_with_reasons_and_confidence(self):
        s = {x["item_id"]: x for x in self.p.suggest(self.ctx)}
        f = s[self.fast]
        self.assertAlmostEqual(f["daily_usage"], 2.0, places=2)
        # target = 2/day * (2+30+5) = 74 ; need 74 - 10 = 64 strips -> 32 packs
        self.assertEqual((f["suggested_base"], f["suggested_packs"]), (64, 32))
        self.assertEqual(f["confidence"], "high")
        self.assertTrue(any("يكفي 5 يوم" in r for r in f["reasons"]))
        self.assertNotIn(self.slow, s)                # no usage -> nothing to buy
        self.assertEqual(s[self.noisy]["confidence"], "medium")
        self.assertTrue(any("متذبذب" in r for r in s[self.noisy]["reasons"]))
        # most urgent first
        self.assertEqual(self.p.suggest(self.ctx)[0]["item_id"], self.noisy)

    def test_health_lists(self):
        h = self.p.stock_health(self.ctx)
        self.assertEqual([x["item_id"] for x in h["slow_movers"]], [self.slow])
        e = h["near_expiry"][0]
        self.assertEqual((e["item_id"], e["units_at_risk"]), (self.slow, 4))

    def test_offer_evaluation_never_just_discount(self):
        r = self.p.evaluate_offer(self.ctx, self.slow, packs=10, discount_pct=40)
        self.assertEqual(r["verdict"], "no")
        r = self.p.evaluate_offer(self.ctx, self.fast, packs=200, discount_pct=35)
        self.assertEqual(r["verdict"], "caution")       # 410 strips / 2 per day = 205 days
        r = self.p.evaluate_offer(self.ctx, self.fast, packs=20, discount_pct=25,
                                  expiry_date=(TODAY + timedelta(days=10)).isoformat())
        self.assertEqual(r["verdict"], "no")
        r = self.p.evaluate_offer(self.ctx, self.fast, packs=20, discount_pct=25)
        self.assertEqual(r["verdict"], "ok")


class TestSupplierCosts(ProcCase):
    def test_effective_cost_includes_bonus(self):
        from dataclasses import replace
        from app import config
        from app.purchasing.service import Purchasing
        from tests.fixtures.make_invoices import excel_invoice
        fut = (date.today() + timedelta(days=700)).strftime("%m/%Y")
        pur = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        s1 = cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما")
        s2 = cat.create_supplier(self.db, self.ctx, name="فارما أوفرسيز")
        # supplier 1: 20% discount no bonus ; supplier 2: 15% discount + 1 free per 10
        for sup, no, bonus, disc, net in ((s1, "A1", 0, 20, 32), (s2, "B1", 1, 15, 34)):
            data = excel_invoice([("PANADOL 500 MG 24 TAB", 10, bonus, 40, disc, net, net * 10, "P", fut, None)],
                                 invoice_no=no, supplier="x")
            inv_id = pur.import_file(self.ctx, data, f"{no}.xlsx", supplier_id=sup)
            d = pur.get(self.ctx, inv_id)
            pur.approve(self.ctx, inv_id, d["invoice"]["version"])
        costs = self.p.supplier_costs(self.ctx, self.fast)
        # s1: 320/(10*2)=16.00 ; s2: 340/(11*2)=15.4545 -> s2 cheaper despite smaller discount
        self.assertEqual([c["supplier_id"] for c in costs], [s2, s1])
        self.assertEqual(Decimal(costs[0]["last_unit_cost"]).quantize(Decimal("0.0001")), Decimal("15.4545"))


class TestConsumptionImport(ProcCase):
    def test_dry_run_then_apply(self):
        import io
        from openpyxl import Workbook
        from app.procurement.service import import_consumption
        cat.update_item(self.db, self.ctx, self.slow, {"code": "777"})
        wb = Workbook()
        ws = wb.active
        ws.append(["كود", "اسم الصنف", "الكمية"])
        ws.append([777, "Rare Cream", 3])
        ws.append([999, "Unknown", 5])
        b = io.BytesIO()
        wb.save(b)
        r = import_consumption(self.db, self.ctx, b.getvalue(), "sales.xlsx",
                               period_start="2026-09-01", period_end="2026-09-30")
        self.assertEqual((r["matched_rows"], len(r["unmatched"]), r["applied"]), (1, 1, False))
        self.assertEqual(self.db.one("SELECT count(*) FROM consumption_history WHERE item_id=?",
                                     (self.slow,))[0], 0)
        import_consumption(self.db, self.ctx, b.getvalue(), "sales.xlsx", period_start="2026-09-01",
                           period_end="2026-09-30", dry_run=False)
        self.assertEqual(self.db.one("SELECT qty_base FROM consumption_history WHERE item_id=?",
                                     (self.slow,))[0], 3)
