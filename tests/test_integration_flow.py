"""Cross-tool integration: one realistic month in one pharmacy, through the
shared services only (no tool touches another tool's tables). After every step
the stock ledger, balances, average cost, cash drawer, customer balance and
audit chain must all agree."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app import config
from app.catalog import service as cat
from app.core import audit, backup
from app.dataimport.service import DataImport
from app.inventory import service as inv
from app.inventory.counting import Counting
from app.procurement.service import Procurement
from app.purchasing.service import Purchasing
from app.recalls.service import Recalls
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.fixtures.make_invoices import excel_invoice
from tests.helpers import DBTestCase

FUT = (date.today() + timedelta(days=500)).strftime("%m/%Y")
SOONER = (date.today() + timedelta(days=150)).strftime("%m/%Y")   # expires before FUT


class TestOneMonth(DBTestCase):
    def consistent(self, step: str):
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [], step)
        self.assertEqual(audit.verify_chain(self.db), [], step)
        self.assertEqual(backup.integrity_problems(self.db), [], step)

    def qty(self, item):
        return inv.stock_for_item(self.db, self.ctx, item)["total_qty"]

    def test_purchase_sale_return_count_recall_procurement(self):
        ctx = self.ctx
        # 1. migration: items + opening stock from the old system
        import io
        from openpyxl import Workbook

        def x(rows):
            wb = Workbook()
            ws = wb.active
            for r in rows:
                ws.append(r)
            b = io.BytesIO()
            wb.save(b)
            return b.getvalue()
        di = DataImport(self.db)
        run = di.analyze(ctx, x([["كود", "اسم الصنف", "باركود", "سعر الجمهور", "عدد الوحدات"],
                                 [1, "Panadol 500mg 24 tab", "6221025012345", 40, 2],
                                 [2, "Cataflam 50mg 20 tab", None, 60, 2]]), "items.xlsx")
        di.apply(ctx, run)
        pan = cat.search_items(self.db, ctx, "6221025012345")[0]["id"]
        caf = self.db.one("SELECT id FROM items WHERE code='2'")[0]
        run = di.analyze(ctx, x([["كود", "الرصيد", "التكلفة", "التشغيلة", "الصلاحية"],
                                 [1, 5, 30, "OLD", SOONER]]), "stock.xlsx", kind="opening_stock")
        di.apply(ctx, run)
        self.assertEqual(self.qty(pan), 10)                          # 5 packs x 2
        self.consistent("migration")

        # 2. purchase invoice: 10 packs + 2 bonus of Panadol, 5 Cataflam
        cat.create_supplier(self.db, ctx, name="ابن سينا فارما")
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        inv_id = p.import_file(ctx, excel_invoice([
            ("PANADOL 500 MG 24 TAB", 10, 2, 40, 20, 32, 320, "NEW", FUT, None),
            ("CATAFLAM 50 MG 20 TAB", 5, 0, 60, 18, 49.2, 246, "C9", FUT, None)]), "inv.xlsx")
        d = p.get(ctx, inv_id)
        p.approve(ctx, inv_id, d["invoice"]["version"])
        self.assertEqual(self.qty(pan), 10 + 24)
        # avg cost: (10 x 15.00 + 24 x 13.333333) / 34
        expected_avg = ((Decimal(10) * 15 + Decimal(24) * (Decimal(320) / 24).quantize(Decimal("0.000001")))
                        / 34).quantize(Decimal("0.000001"))
        self.assertEqual(Decimal(inv.stock_for_item(self.db, ctx, pan)["avg_cost"]), expected_avg)
        self.consistent("purchase")

        # 3. sales: cash, then credit to a customer; FEFO takes the OLD batch first
        cd = CashDrawer(self.db)
        cd.open(ctx, 20000)
        s = Sales(self.db)

        def sell(item, units, customer=None, credit=0):
            sid = s.create(ctx, customer)
            dd = s.add_line(ctx, sid, item, units=units)
            f = s.safety_check(ctx, sid)
            return s.complete(ctx, sid, paid_cash=dd["total"] - credit, on_credit=credit,
                              safety_decisions={i: "مراجعة" for i in range(len(f))})
        a = sell(pan, 12)
        self.assertEqual([x["batch_no"] for x in a["lines"][0]["allocations"]], ["OLD", "NEW"])
        cust = s.create_customer(ctx, name="أ. سعيد", credit_limit=50000)
        b = sell(caf, 4, customer=cust, credit=12000)
        self.assertEqual(self.qty(pan), 22)
        self.assertEqual(s.balance(ctx, cust), 12000)
        self.consistent("sales")

        # 4. return 2 strips of Panadol for cash
        s.return_items(ctx, a["id"], {a["lines"][0]["id"]: 2}, refund_method="cash", reason="العميل رجعها")
        self.assertEqual(self.qty(pan), 24)
        self.consistent("return")

        # 5. count: 1 strip of Panadol missing; approval posts an adjustment
        cnt = Counting(self.db)
        sid = cnt.create(ctx, title="جرد آخر الشهر", scope="items", item_ids=[pan])
        cnt.record(ctx, sid, item_id=pan, qty=23)
        cnt.approve(ctx, sid)
        self.assertEqual(self.qty(pan), 23)
        self.consistent("count")

        # 6. recall of batch NEW -> alert with the quantity we still hold
        Recalls(self.db).add_notice(ctx, title="منشور سحب", items=[{"product_name": "Panadol 500mg",
                                                                    "batch_numbers": ["NEW"]}])
        alerts = Recalls(self.db).alerts(ctx)
        self.assertEqual([(al["item_id"], al["qty_in_stock"]) for al in alerts], [(pan, 23)])

        # 7. procurement sees the sales and the supplier cost
        sugg = {x["item_id"]: x for x in Procurement(self.db).suggest(ctx, only_needed=False)}
        self.assertGreater(sugg[pan]["daily_usage"], 0)
        self.assertEqual(Procurement(self.db).supplier_costs(ctx, pan)[0]["supplier"], "ابن سينا فارما")

        # 8. drawer: 200 float + Panadol cash + Cataflam cash part - refund
        sm = cd.summary(ctx)
        refund = 2 * a["lines"][0]["allocations"][-1]["unit_price"]
        self.assertEqual(sm["expected_cash"], 20000 + a["total"] + (b["total"] - 12000) - refund)
        cd.close(ctx, sm["expected_cash"])
        self.consistent("end of month")
