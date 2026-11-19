"""The move on the launch-candidate schema (0038 money accounts, 0039 hospital-owned batches): E-Stock's money
places become real accounts with their opening balances, and store 2 (the hospital's own stock) becomes
hospital-owned batches that no normal sale can take. Synthetic data only."""
from decimal import Decimal
from pathlib import Path

from app.dataimport import estock_db
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.test_estock_db_import import Base, base_tables

NAMES = ["نقطة بيع 1", "خزينة 1", "حساب بنك مصر", "نقطة بيع 2", "نقطة بيع 3", "0000", "انستا باي",
         "فودافون كاش", "اتصالات كاش", "فيزا باى موب - موقوف", "امان"]


def tables():
    t = base_tables()
    t["Cash_depots"] = [{"cd_id": str(i), "cd_name": n, "cd_class": "1", "cd_balance": f"{i * 10}.2500"}
                        for i, n in enumerate(NAMES, start=1)]
    t["Cash_disk_close"][0].update({"cdc_cash_id": "1", "insert_date": "2026-11-15T14:19:00.000"})
    return t


class TestMoneyPlacesBecomeAccounts(Base):
    def setUp(self):
        super().setUp()
        self.res = self.run_import(tables=tables())

    def acct(self, eid):
        return self.db.one("SELECT * FROM money_accounts WHERE org_id=? AND source_ref=?", (self.ctx.org_id, f"estock:{eid}"))

    def bal(self, aid):
        return self.db.one("SELECT coalesce(sum(amount),0) b FROM money_movements WHERE account_id=?", (aid,))["b"]

    def test_every_account_place_is_a_real_account_with_its_opening(self):
        self.assertTrue(self.res["ok"], self.res["reconciliation"]["mismatches"])
        for i in (2, 3, 4, 5, 7, 8, 9, 10, 11):
            a = self.acct(i)
            self.assertIsNotNone(a, NAMES[i - 1])
            self.assertEqual(self.bal(a["id"]), i * 1000 + 25, NAMES[i - 1])
        self.assertEqual(self.acct(2)["kind"], "treasury")
        self.assertEqual(self.acct(3)["kind"], "bank")
        self.assertEqual(self.acct(8)["kind"], "wallet")
        self.assertEqual(self.acct(10)["is_active"], 0)                    # «موقوف»: kept, stopped

    def test_the_shift_drawer_and_0000_are_not_accounts(self):
        self.assertIsNone(self.acct(1))
        self.assertIsNone(self.acct(6))

    def test_reconciled_and_reported_as_moved(self):
        m = self.res["reconciliation"]["money"]
        self.assertEqual((m["accounts"], m["mismatches"]), (9, 0))
        owner = Path(self.res["reports"]["owner"]).read_text(encoding="utf-8-sig")
        self.assertNotIn("لسه ما اتنقل", owner)
        self.assertIn("اتنقلت حسابات", owner)

    def test_the_same_export_again_writes_no_second_opening(self):
        n = self.db.one("SELECT count(*) n FROM money_movements WHERE kind='opening'")["n"]
        again = self.run_import(folder=self._last)
        self.assertTrue(again["ok"], again["reconciliation"]["mismatches"])
        self.assertIn("money", again["runs"])
        self.assertEqual(self.db.one("SELECT count(*) n FROM money_movements WHERE kind='opening'")["n"], n)


class TestHospitalStock(Base):
    """Hospital-owned batches once inventory is ownership-aware (switched on here; off for launch, see below)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        with mock.patch.object(estock_db, "HOSPITAL_BATCHES", True):
            self.res = self.run_import()

    def test_store_2_lands_as_hospital_owned_batches(self):
        self.assertTrue(self.res["ok"], self.res["reconciliation"]["mismatches"])
        rows = self.db.all("SELECT b.owner, b.batch_no, sum(m.qty_delta) q FROM stock_movements m JOIN batches b ON"
                           " b.id=m.batch_id WHERE b.owner='hospital' GROUP BY b.id")
        self.assertEqual([(r["owner"], r["batch_no"], r["q"]) for r in rows], [("hospital", "2-1", 56)])  # 4 x 14
        h = self.res["reconciliation"]["hospital"]
        self.assertEqual((h["rows"], h["units"], h["mismatches"]), (1, 56, 0))
        st = self.res["reconciliation"]["stock"]
        self.assertNotIn("2-1", str(st))                                   # never in the pharmacy's stock value

    def test_a_normal_sale_never_takes_hospital_stock(self):
        iid = self.item("102")["id"]
        s = Sales(self.db)
        sellable, expired, recalled = s._held(self.ctx, self.ctx.branch_id, iid)
        pharmacy = self.db.one("SELECT coalesce(sum(m.qty_delta),0) q FROM stock_movements m JOIN batches b ON"
                               " b.id=m.batch_id WHERE m.item_id=? AND b.owner='pharmacy'", (iid,))["q"]
        self.assertEqual(sellable + expired + recalled, pharmacy)
        CashDrawer(self.db).open(self.ctx, 0)
        sid = s.create(self.ctx, None)
        s.add_line(self.ctx, sid, iid, units=pharmacy)
        tot = s.get(self.ctx, sid)["total"]
        f = s.safety_check(self.ctx, sid)
        s.complete(self.ctx, sid, paid_cash=tot, safety_decisions={i: "ok" for i in range(len(f))})
        left = self.db.one("SELECT coalesce(sum(m.qty_delta),0) q FROM stock_movements m JOIN batches b ON"
                           " b.id=m.batch_id WHERE m.item_id=? AND b.owner='hospital'", (iid,))["q"]
        self.assertEqual(left, 56)                                         # the hospital's units untouched


class TestOnTheOldSchema(Base):
    """Before 0038/0039 (develop, or the upgrade test at 0036) the same code keeps the report-only path."""

    def test_report_only_without_the_tables(self):
        from unittest import mock
        with mock.patch.object(estock_db, "_has_money", return_value=False), \
                mock.patch.object(estock_db, "_has_batch_owner", return_value=False):
            res = self.run_import(tables=tables())
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertNotIn("money", res["runs"])
        self.assertEqual(self.db.one("SELECT count(*) n FROM money_accounts WHERE source_ref LIKE 'estock:%'")["n"], 0)


class TestCodexReview381(Base):
    """Codex on #381 @c9e46ba (each failed before the fix)."""

    def test_p1_for_launch_store_2_never_enters_operational_stock(self):
        res = self.run_import()
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        self.assertFalse(estock_db.HOSPITAL_BATCHES)
        self.assertEqual(self.db.one("SELECT count(*) n FROM batches WHERE owner='hospital'")["n"], 0)
        self.assertEqual(self.units("102"), 28)                                # the pharmacy's own only
        cost = self.db.one("SELECT avg_cost FROM item_costs WHERE item_id=?", (self.item("102")["id"],))
        self.assertEqual(Decimal(cost["avg_cost"]) if cost else None, Decimal("10"))   # 140 / 14, no hospital mix
        text = Path(res["reports"]["hospital_stock"]).read_text(encoding="utf-8-sig")
        self.assertIn("Testaclav 1g Tab", text)                                # still listed in full

    def test_p1_a_fractional_piaster_opening_is_rounded_and_listed_never_silently(self):
        t = tables()
        t["Cash_depots"][1]["cd_balance"] = "20.2550"                         # «خزينة 1»
        res = self.run_import(tables=t)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"])
        line = [d for d in res["plan"].dq if d["table"] == "Cash_depots" and "كسر قرش" in d["action"]]
        self.assertEqual(len(line), 1)
        self.assertIn("Cash_depots", res["plan"].checks["rounding"])
        acct = self.db.one("SELECT id FROM money_accounts WHERE source_ref='estock:2'")["id"]
        self.assertEqual(self.db.one("SELECT sum(amount) b FROM money_movements WHERE account_id=?", (acct,))["b"], 2026)

    def test_p1_with_no_round_piastres_a_fractional_opening_stops_the_move(self):
        t = tables()
        t["Cash_depots"][1]["cd_balance"] = "20.2550"
        res = self.run_import(tables=t, round_piastres=False)
        self.assertFalse(res["ok"])
        self.assertFalse(self.db.one("SELECT 1 FROM money_movements"))
