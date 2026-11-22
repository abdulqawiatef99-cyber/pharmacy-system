"""U4-1: the smart order reads the E-Stock sales archive (synthetic data only).

The archive is built by the real move (estock_db.run on an export shaped like E-Stock's: Sales_header +
Branches_sales_details of this branch, Back_sales_header + Back_Sales_details, Products, the item map). Before
this, the screen counted only the program's own sales since the move and suggested almost nothing."""
from datetime import date, datetime, timedelta
from unittest import mock

from app.db import new_id
from app.procurement import demand
from app.procurement.smart_order import smart_order
from tests.test_estock_db_import import Base, base_tables

TODAY = date(2026, 11, 22)


def weekly_sales(t, weeks=13, packs="3", start_id=100):
    """One invoice a week for item "100" (E-Stock product 1, 3 units a pack), the newest 2 days ago."""
    for w in range(weeks):
        sid = str(start_id + w)
        at = (datetime(2026, 11, 20, 11, 0) - timedelta(days=7 * w)).strftime("%Y-%m-%d %H:%M:%S.000")  # E-Stock: space
        t["Sales_header"].append({"sales_id": sid, "store_id": "1", "customer_id": "0", "total_bill_net": "90.0000",
                                  "insert_date": at, "ticket_name": "", "ticket_id": "", "ticket_num": ""})
        t["Branches_sales_details"].append({"branch_id": "3", "details_id": f"d{sid}", "sales_id": sid, "product_id": "1",
                                            "amount": packs, "sale_unit_change": "3", "sale_unit": "1",
                                            "sell_price": "30.0000", "insert_date": at})


class ArchiveDemand(Base):
    def build(self, **kw):
        t = base_tables()
        weekly_sales(t, **kw)
        t["Back_sales_header"][0]["insert_date"] = "2026-10-15T12:00:00.000"
        self.assertTrue(self.run_import(tables=t)["ok"])
        demand._cache.clear()
        return self.item("100")

    def test_monthly_from_the_archive_returns_netted_one_event_per_invoice(self):
        it = self.build()
        m = demand.monthly(self.db, self.ctx, months=12, today=TODAY)
        self.assertEqual(m["months"][0], "2025-12")
        self.assertEqual(m["months"][-1], "2026-11")
        per = m["items"][it["id"]]
        # weekly invoices of 3 packs x 3 units; the 2026-11-18 sale of base_tables adds 1 pack (invoice 1)
        nov = sum(1 for w in range(13) if (datetime(2026, 11, 20) - timedelta(days=7 * w)).month == 11
                  and (datetime(2026, 11, 20) - timedelta(days=7 * w)).year == 2026)
        self.assertEqual(per["2026-11"], {"units": nov * 9 + 3, "events": nov + 1})
        octw = sum(1 for w in range(13) if (datetime(2026, 11, 20) - timedelta(days=7 * w)).month == 10)
        self.assertEqual(per["2026-10"], {"units": octw * 9 - 3, "events": octw})       # one pack came back
        self.assertNotIn("2025-12", per)

    def test_the_line_back_amount_wins_over_return_invoices_never_both(self):
        t = base_tables()
        weekly_sales(t, weeks=1)
        t["Branches_sales_details"][-1]["back_amount"] = "1"
        t["Back_sales_header"][0]["insert_date"] = "2026-11-20T12:00:00.000"
        self.assertTrue(self.run_import(tables=t)["ok"])
        demand._cache.clear()
        per = demand.monthly(self.db, self.ctx, today=TODAY)["items"][self.item("100")["id"]]
        self.assertEqual(per["2026-11"]["units"], (3 - 1) * 3 + 3)        # line return once; return invoice ignored

    def test_no_double_count_after_the_cut_off(self):
        it = self.build()
        with mock.patch.object(demand, "cutoff", return_value="2026-11-19T00:00:00"):
            demand._cache.clear()
            per = demand.monthly(self.db, self.ctx, today=TODAY)["items"][it["id"]]
        nov_before = sum(1 for w in range(13) if datetime(2026, 11, 1) <= datetime(2026, 11, 20) - timedelta(days=7 * w)
                         < datetime(2026, 11, 19))
        self.assertEqual(per["2026-11"], {"units": nov_before * 9 + 3, "events": nov_before + 1})

    def test_the_manual_upload_counts_only_where_the_archive_does_not(self):
        it = self.build()
        with self.db.transaction():
            for start, end in (("2026-01-01", "2026-01-31"), ("2026-10-01", "2026-10-31")):
                self.db.execute("INSERT INTO consumption_history (id, org_id, branch_id, item_id, period_start, period_end,"
                                " qty_base, source) VALUES (?,?,?,?,?,?,?, 'manual')",
                                (new_id(), self.ctx.org_id, self.ctx.branch_id, it["id"], start, end, 30))
        per = demand.monthly(self.db, self.ctx, today=TODAY)["items"][it["id"]]
        self.assertEqual(per["2026-01"]["units"], 30)                    # before the archive: the upload counts
        octw = sum(1 for w in range(13) if (datetime(2026, 11, 20) - timedelta(days=7 * w)).month == 10)
        self.assertEqual(per["2026-10"]["units"], octw * 9 - 3)          # archive month: the upload is not added

    def test_smart_order_suggests_from_the_archive(self):
        it = self.build()
        r = smart_order(self.db, self.ctx, today=TODAY)
        self.assertTrue(r["estock_archive"])
        row = [x for x in r["rows"] if x["item_id"] == it["id"]]
        self.assertEqual(len(row), 1, "the archived sales must make the item show")
        row = row[0]
        self.assertEqual(row["events"], 14)                              # 13 weekly + the base invoice, one each
        self.assertEqual(row["movement"], "fast")
        self.assertGreater(row["suggested_packs"], 0)
        self.assertTrue(any("أرشيف إي ستوك" in x for x in row["reasons"]))

    def test_without_an_archive_nothing_changes(self):
        t = base_tables()
        weekly_sales(t)
        self.assertTrue(self.run_import(tables=t, write_archive=False)["ok"])
        demand._cache.clear()
        r = smart_order(self.db, self.ctx, today=TODAY)
        self.assertFalse(r["estock_archive"])
        self.assertEqual(demand.monthly(self.db, self.ctx, today=TODAY)["archive_from"], None)
