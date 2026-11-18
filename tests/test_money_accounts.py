"""F105 money accounts: treasury, owner accounts, shift close to the treasury, payouts from a source,
card/transfer routing, owner withdrawals, counts, opening balances, reports. Synthetic data only."""
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.core import people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.db import MIGRATIONS_DIR, Database
from app.finance import money
from tests.helpers import today
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"


class MoneyCase(Base):
    def setUp(self):
        super().setUp()
        people.add_user(self.db, self.ctx, username="mgr", full_name="M", password=PW, role_code="manager", pin="2468")
        people.add_user(self.db, self.ctx, username="pharm", full_name="P", password=PW, role_code="pharmacist")
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        self.m, self.ph, self.a = self.login("mgr", PW), self.login("pharm", PW), self.login("asst", PW)
        self.t = money.treasury_id(self.db, self.ctx)
        self.bank = money.create_account(self.db, self.ctx, "إنستاباي", "bank")["id"]

    def bal(self, aid):
        return money.balance(self.db, self.ctx.org_id, aid)

    def close_shift(self, ctx=None, counted=None, float_left=None, note=None):
        """The close as phase 2 wires it: cash.close then the transfer, one transaction."""
        ctx = ctx or self.ctx
        with self.db.transaction():
            sid = self.cd.close(ctx, counted, note=note)["id"]
            return money.shift_close_transfer(self.db, ctx, sid, float_left)


class TestAccounts(MoneyCase):
    def test_one_treasury_made_once_and_audited(self):
        self.assertEqual(money.treasury_id(self.db, self.ctx), self.t)
        n = self.db.one("SELECT count(*) n FROM money_accounts WHERE org_id=? AND kind='treasury'", (self.ctx.org_id,))["n"]
        self.assertEqual(n, 1)
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='money.account_create' AND entity_id=?", (self.t,)))
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction():   # the database itself refuses it
            self.db.execute("INSERT INTO money_accounts (id, org_id, kind, name) VALUES ('x', ?, 'treasury', 'تانية')",
                            (self.ctx.org_id,))

    def test_listing_drawer_first_then_treasury_then_accounts(self):
        self.cd.open(self.ctx, 50000)
        rows = money.accounts(self.db, self.m)
        self.assertEqual([r["id"] for r in rows[:3]], [money.DRAWER, self.t, self.bank])
        self.assertEqual(rows[0]["balance"], 50000)

    def test_only_owner_defines_accounts_and_only_owner_or_manager_sees_them(self):
        with self.assertRaises(Forbidden):
            money.create_account(self.db, self.m, "فودافون كاش", "wallet")
        for c in (self.ph, self.a):
            with self.assertRaises(Forbidden):
                money.accounts(self.db, c)
        with self.assertRaises(ValidationFailed):
            money.create_account(self.db, self.ctx, "خزنة تانية", "treasury")
        with self.assertRaises(Conflict):
            money.create_account(self.db, self.ctx, "إنستاباي", "wallet")

    def test_account_with_money_cannot_be_stopped(self):
        money.post_opening(self.db, self.ctx, self.bank, 1000)
        with self.assertRaises(Conflict):
            money.update_account(self.db, self.ctx, self.bank, {"is_active": False})
        with self.assertRaises(Conflict):
            money.update_account(self.db, self.ctx, self.t, {"is_active": False})

    def test_movements_are_append_only(self):
        money.post_opening(self.db, self.ctx, self.t, 5000)
        for sql in ("UPDATE money_movements SET amount=1", "DELETE FROM money_movements"):
            with self.assertRaises(sqlite3.DatabaseError), self.db.transaction():
                self.db.execute(sql)


class TestOpening(MoneyCase):
    """Openings come from the E-Stock import (owner 18/11 22:05; manager 22:16): the old program's figure as it
    is, then a real count records the difference as "difference from the old program"."""

    def test_estock_openings_by_key_once_per_account_audited(self):
        with self.db.transaction():                            # the import calls it inside its own step
            t = money.post_opening(self.db, self.ctx, "treasury", 70000000, source="estock", source_ref="run-1")
            i = money.post_opening(self.db, self.ctx, "instapay", 1250000, source="estock", source_ref="run-1")
            v = money.post_opening(self.db, self.ctx, "vodafone_cash", 340000, source="estock", source_ref="run-1")
        self.assertEqual((t["id"], t["balance"]), (self.t, 70000000))
        self.assertEqual(i["id"], self.bank)                   # the owner's «إنستاباي» is adopted, not duplicated
        self.assertEqual((v["kind"], v["code"], v["balance"]), ("wallet", "vodafone_cash", 340000))
        mv = money.movements(self.db, self.ctx, account_id=self.t)["rows"][0]
        self.assertEqual((mv["kind"], mv["amount"], mv["meta"]), ("opening", 70000000,
                                                                  {"source": "estock", "source_ref": "run-1"}))
        a = self.db.one("SELECT after_json FROM audit_log WHERE action='money.opening' AND entity_id=?", (self.t,))
        self.assertIn('"source": "estock"', a["after_json"])
        with self.assertRaises(Conflict) as e:
            money.post_opening(self.db, self.ctx, "treasury", 1, source="estock", source_ref="run-2")
        self.assertEqual(e.exception.code, "opening_exists")

    def test_a_resumed_import_gets_the_first_result_back(self):
        first = money.post_opening(self.db, self.ctx, "treasury", 5000, source_ref="run-1")
        again = money.post_opening(self.db, self.ctx, "treasury", 5000, source_ref="run-1")
        self.assertTrue(again["replayed"])
        self.assertEqual((again["movement_id"], self.bal(self.t)), (first["movement_id"], 5000))
        with self.assertRaises(Conflict):                      # same run, other amount: never a second opening
            money.post_opening(self.db, self.ctx, "treasury", 5001, source_ref="run-1")

    def test_refused_once_the_account_has_movements(self):
        money.owner_movement(self.db, self.ctx, self.bank, 100, "owner_deposit")
        with self.assertRaises(Conflict) as e:
            money.post_opening(self.db, self.ctx, "instapay", 9000, source_ref="run-1")
        self.assertEqual(e.exception.code, "account_has_movements")
        self.assertEqual(self.bal(self.bank), 100)

    def test_zero_opening_also_only_once(self):
        money.post_opening(self.db, self.ctx, self.bank, 0)
        with self.assertRaises(Conflict):
            money.post_opening(self.db, self.ctx, self.bank, 500)

    def test_estock_figure_taken_as_it_is_even_negative_but_a_count_opening_is_not(self):
        self.assertEqual(money.post_opening(self.db, self.ctx, "treasury", -12345, source_ref="run-1")["balance"], -12345)
        with self.assertRaises(ValidationFailed) as e:
            money.post_opening(self.db, self.ctx, self.bank, -100, source="count")
        self.assertEqual(e.exception.code, "account_negative")

    def test_owner_only_whole_piasters_known_source_and_account(self):
        for bad in (dict(amount=10.5), dict(amount=1, source="eStock")):
            with self.assertRaises(ValidationFailed):
                money.post_opening(self.db, self.ctx, self.t, **{"amount": 1, **bad})
        with self.assertRaises(NotFound):                    # not a key, a code, a source reference or an id
            money.post_opening(self.db, self.ctx, "fawry", 1)
        for c in (self.m, self.ph):
            with self.assertRaises(Forbidden):
                money.post_opening(self.db, c, self.t, 100)

    def test_huge_estock_opening_then_a_large_negative_count_is_the_old_programs_difference(self):
        money.post_opening(self.db, self.ctx, "treasury", 167789758, source_ref="run-1")    # 1,677,897.58
        r = money.count(self.db, self.m, "treasury", 2500000, "عدّ فعلي بعد الإطلاق")
        self.assertEqual((r["difference"], r["balance"], r["against_old_program"]), (-165289758, 2500000, True))
        p = money.summary(self.db, self.m, today(), today())["periods"][0]
        kinds = {k["kind"]: k for k in p["by_kind"]}
        self.assertEqual((kinds["old_program_difference"]["out"], kinds["old_program_difference"]["flow"]),
                         (165289758, False))
        self.assertEqual((p["in"], p["out"]), (0, 0))          # not a loss of the month
        second = money.count(self.db, self.m, "treasury", 2400000, "ورقة ناقصة")
        self.assertFalse(second["against_old_program"])        # later counts are ordinary differences
        p = money.summary(self.db, self.m, today(), today())["periods"][0]
        self.assertEqual(p["out"], 100000)

    def test_owner_corrects_an_opening_without_counting(self):
        money.post_opening(self.db, self.ctx, "treasury", 70000000, source_ref="run-1")
        with self.assertRaises(ValidationFailed):
            money.adjust_opening(self.db, self.ctx, "treasury", 69000000, "")
        with self.assertRaises(Forbidden):
            money.adjust_opening(self.db, self.m, "treasury", 69000000, "تصحيح")
        r = money.adjust_opening(self.db, self.ctx, "treasury", 69000000, "رقم إي ستوك كان غلط")
        self.assertEqual((r["difference"], r["balance"]), (-1000000, 69000000))
        mv = money.movements(self.db, self.ctx, kind="opening_adjustment")["rows"]
        self.assertEqual([(m["amount"], m["note"]) for m in mv], [(-1000000, "رقم إي ستوك كان غلط")])
        p = money.summary(self.db, self.m, today(), today())["periods"][0]
        self.assertEqual((p["in"], p["out"]), (0, 0))


class TestImportAccounts(MoneyCase):
    """The import makes one account per used E-Stock money place (Cash_depots) and checks the move."""

    def test_accounts_from_cash_depots_matched_once(self):
        t = money.ensure_account(self.db, self.ctx, kind="treasury", name="الخزينة", source_ref="estock:2")
        self.assertEqual(t, self.t)                            # the main treasury, never a second one
        w = money.ensure_account(self.db, self.ctx, kind="wallet", name="فودافون كاش", source_ref="estock:5")
        self.assertEqual(money.ensure_account(self.db, self.ctx, kind="wallet", name="اسم تاني", source_ref="estock:5"), w)
        b = money.ensure_account(self.db, self.ctx, kind="bank", name="إنستاباي", source_ref="estock:7")
        self.assertEqual(b, self.bank)                         # adopted by name, gains its source reference
        keys = {r["source_ref"]: r["id"] for r in money.listing(self.db, self.m)}
        self.assertEqual((keys["estock:2"], keys["estock:5"], keys["estock:7"]), (self.t, w, self.bank))
        self.assertEqual(money.resolve(self.db, self.ctx, "estock:5"), w)
        with self.assertRaises(Conflict):                      # one account, one old money place
            money.ensure_account(self.db, self.ctx, kind="bank", name="إنستاباي", source_ref="estock:8")
        with self.assertRaises(Forbidden):
            money.ensure_account(self.db, self.m, kind="wallet", name="محفظة", source_ref="estock:9")

    def test_opening_by_source_reference_and_the_check_for_the_import_report(self):
        w = money.ensure_account(self.db, self.ctx, kind="wallet", name="فودافون كاش", source_ref="estock:5")
        money.post_opening(self.db, self.ctx, "estock:5", 340058, source_ref="run-1")       # 3,400.577 rounded
        money.post_opening(self.db, self.ctx, "treasury", 167789758, source_ref="run-1")
        c = money.check_openings(self.db, self.m, {"estock:5": 340058, "treasury": 167789758, "estock:9": 100})
        self.assertFalse(c["ok"])
        by = {r["account"]: r for r in c["rows"]}
        self.assertEqual((by["estock:5"]["account_id"], by["estock:5"]["ok"]), (w, True))
        self.assertTrue(by["treasury"]["ok"])
        self.assertEqual((by["estock:9"]["account_id"], by["estock:9"]["ok"]), (None, False))

    def test_network_routes_to_the_imported_accounts(self):
        w = money.ensure_account(self.db, self.ctx, kind="wallet", name="فودافون كاش", source_ref="estock:5")
        with self.db.transaction():
            self.db.execute("INSERT INTO payment_channels (id, org_id, kind, name) VALUES ('net-5', ?, 'transfer', 'فودافون')",
                            (self.ctx.org_id,))
        money.set_route(self.db, self.ctx, "transfer", "net-5", w)
        self.assertEqual(money.route_for(self.db, self.ctx.org_id, "transfer", "net-5"), w)


class TestShiftClose(MoneyCase):
    def test_counted_minus_float_goes_to_treasury_once(self):
        self.cd.open(self.ctx, 20000)
        self.sell(units=4)                                     # 80.00 cash
        r = self.close_shift(counted=28000, float_left=5000)
        self.assertEqual((r["float_left"], r["to_treasury"]), (5000, 23000))
        self.assertEqual(self.bal(self.t), 23000)
        again = money.shift_close_transfer(self.db, self.ctx, r["shift_id"], 1000)
        self.assertTrue(again["replayed"])
        self.assertEqual((again["to_treasury"], self.bal(self.t)), (23000, 23000))
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='money.shift_close' AND entity_id=?",
                                    (r["shift_id"],)))
        self.assertEqual(money.drawer_balance(self.db, self.ctx)["balance"], 5000)

    def test_float_defaults_to_the_opening_cash(self):
        self.cd.open(self.ctx, 20000)
        self.sell(units=2)                                     # 40.00
        r = self.close_shift(counted=24000)
        self.assertEqual((r["float_left"], r["to_treasury"]), (20000, 4000))

    def test_float_larger_than_counted_is_refused_and_nothing_closes(self):
        self.cd.open(self.ctx, 20000)
        with self.assertRaises(ValidationFailed):
            self.close_shift(counted=10000, float_left=15000, note="عجز تجربة")
        self.assertTrue(money.drawer_balance(self.db, self.ctx)["open"])     # the close rolled back
        self.assertEqual(self.bal(self.t), 0)

    def test_shortage_moves_only_the_real_cash(self):
        self.cd.open(self.ctx, 20000)
        r = self.close_shift(counted=15000, note="عجز تجربة")    # 50.00 short, float defaults to all of it
        self.assertEqual((r["float_left"], r["to_treasury"]), (15000, 0))
        self.assertEqual(self.bal(self.t), 0)

    def test_a_shift_moves_to_the_treasury_once_even_by_hand(self):
        self.cd.open(self.ctx, 0)
        r = self.close_shift(counted=0)
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction():
            for _ in range(2):
                self.db.execute("INSERT INTO money_movements (id, org_id, account_id, amount, kind, ref_id, created_by)"
                                " VALUES (lower(hex(randomblob(8))), ?, ?, 100, 'shift_close', ?, 'x')",
                                (self.ctx.org_id, self.t, r["shift_id"]))

    def test_an_open_shift_cannot_be_moved(self):
        sid = self.cd.open(self.ctx, 0)
        with self.assertRaises(Conflict):
            money.shift_close_transfer(self.db, self.ctx, sid)


class TestPayouts(MoneyCase):
    def test_payout_lowers_the_account_and_is_refused_below_zero(self):
        money.post_opening(self.db, self.ctx, self.t, 10000)
        with self.db.transaction():
            r = money.pay_out(self.db, self.m, self.t, 4000, "supplier_payment", ref_type="supplier_ledger",
                              ref_id="L1", note="دفع للمورد")
        self.assertEqual((r["source"], self.bal(self.t)), (self.t, 6000))
        with self.assertRaises(Conflict) as e:
            with self.db.transaction():
                money.pay_out(self.db, self.m, self.t, 6001, "expense", ref_type="expense", ref_id="E1", note="x")
        self.assertEqual(e.exception.code, "account_negative")
        self.assertEqual(self.bal(self.t), 6000)

    def test_owner_may_allow_negative_on_one_account(self):
        money.update_account(self.db, self.ctx, self.bank, {"allow_negative": True})
        with self.db.transaction():
            money.pay_out(self.db, self.ctx, self.bank, 500, "tax", ref_type="tax", ref_id=None, note="ضريبة")
        self.assertEqual(self.bal(self.bank), -500)
        with self.assertRaises(Conflict):              # cannot forbid it again while the account is below zero
            money.update_account(self.db, self.ctx, self.bank, {"allow_negative": False})

    def test_drawer_payout_is_a_drawer_movement_and_cannot_go_below_zero(self):
        self.cd.open(self.ctx, 3000)
        with self.db.transaction():
            r = money.pay_out(self.db, self.m, money.DRAWER, 1000, "expense", ref_type="expense", ref_id="E1", note="أكياس")
        self.assertEqual((r["source"], self.cd.summary(self.ctx)["expected_cash"]), (money.DRAWER, 2000))
        with self.assertRaises(Conflict) as e:
            with self.db.transaction():
                money.pay_out(self.db, self.m, money.DRAWER, 2001, "expense", ref_type="expense", ref_id="E2", note="x")
        self.assertEqual(e.exception.code, "drawer_negative")

    def test_outside_is_the_owners_choice_only(self):
        with self.assertRaises(Forbidden):
            money.pay_out(self.db, self.m, money.OUTSIDE, 100, "expense", ref_type="expense", ref_id=None, note="x")
        with self.assertRaises(Forbidden):
            money.pay_out(self.db, self.m, None, 100, "expense", ref_type="expense", ref_id=None, note="x")
        r = money.pay_out(self.db, self.ctx, money.OUTSIDE, 100, "expense", ref_type="expense", ref_id=None, note="x")
        self.assertEqual((r["source"], r["movement_id"]), (money.OUTSIDE, None))
        self.assertEqual(money.source_label(self.db, self.ctx, money.OUTSIDE), money.OUTSIDE_LABEL)

    def test_paying_from_an_account_needs_owner_or_manager(self):
        money.post_opening(self.db, self.ctx, self.t, 10000)
        with self.assertRaises(Forbidden):
            money.pay_out(self.db, self.ph, self.t, 100, "expense", ref_type="expense", ref_id=None, note="x")

    def test_unknown_stopped_or_other_pharmacys_account_is_refused(self):
        with self.assertRaises(NotFound):
            money.pay_out(self.db, self.ctx, "nope", 100, "expense", ref_type="expense", ref_id=None, note="x")
        _, octx = self.second_tenant()
        with self.assertRaises(NotFound):
            money.get_account(self.db, octx, self.t)
        with self.assertRaises(NotFound):
            money.pay_out(self.db, octx, self.bank, 1, "expense", ref_type="expense", ref_id=None, note="x")
        money.update_account(self.db, self.ctx, self.bank, {"is_active": False})
        with self.assertRaises(Conflict):
            money.pay_out(self.db, self.ctx, self.bank, 1, "expense", ref_type="expense", ref_id=None, note="x")


class TestTransfersAndOwner(MoneyCase):
    def test_transfer_between_accounts_and_drawer(self):
        self.cd.open(self.ctx, 10000)
        money.transfer(self.db, self.m, money.DRAWER, self.t, 6000, "توريد للخزنة")
        self.assertEqual((self.cd.summary(self.ctx)["expected_cash"], self.bal(self.t)), (4000, 6000))
        money.transfer(self.db, self.m, self.t, self.bank, 2500)
        self.assertEqual((self.bal(self.t), self.bal(self.bank)), (3500, 2500))
        with self.assertRaises(Conflict):
            money.transfer(self.db, self.m, self.bank, self.t, 2501)
        with self.assertRaises(ValidationFailed):
            money.transfer(self.db, self.m, self.t, self.t, 1)
        with self.assertRaises(Forbidden):
            money.transfer(self.db, self.ph, self.t, self.bank, 1)

    def test_owner_withdrawal_and_deposit_are_their_own_kinds_not_expenses(self):
        money.owner_movement(self.db, self.ctx, self.t, 9000, "owner_deposit", "رأس مال")
        money.owner_movement(self.db, self.ctx, self.t, 4000, "owner_withdrawal")
        kinds = {r["kind"]: r["amount"] for r in money.movements(self.db, self.ctx, account_id=self.t)["rows"]}
        self.assertEqual(kinds, {"owner_deposit": 9000, "owner_withdrawal": -4000})
        self.assertEqual(self.db.one("SELECT count(*) n FROM expenses")["n"], 0)
        with self.assertRaises(Conflict):
            money.owner_movement(self.db, self.ctx, self.t, 5001, "owner_withdrawal")


class TestRouting(MoneyCase):
    def test_card_and_transfer_land_in_the_routed_account(self):
        ch = self.db.one("SELECT id FROM payment_channels WHERE org_id=? AND kind='transfer' LIMIT 1", (self.ctx.org_id,))
        wallet = money.create_account(self.db, self.ctx, "فودافون كاش", "wallet")["id"]
        money.set_route(self.db, self.ctx, "card", None, self.bank)
        if ch:
            money.set_route(self.db, self.ctx, "transfer", ch["id"], wallet)
        with self.db.transaction():
            self.assertIsNotNone(money.receive(self.db, self.ctx, "card", None, 7000, "sale", ref_type="sale", ref_id="S1"))
            self.assertIsNone(money.receive(self.db, self.ctx, "transfer", "no-route", 100, "sale", ref_type="sale",
                                            ref_id="S2"))
        self.assertEqual(self.bal(self.bank), 7000)
        with self.assertRaises(Forbidden):
            money.set_route(self.db, self.m, "card", None, self.t)
        with self.assertRaises(ValidationFailed):
            money.set_route(self.db, self.ctx, "cash", None, self.t)

    def test_channel_route_wins_over_the_method_default(self):
        cid = "ch-test"
        with self.db.transaction():
            self.db.execute("INSERT INTO payment_channels (id, org_id, kind, name) VALUES (?,?,?,?)",
                            (cid, self.ctx.org_id, "transfer", "إنستاباي تجربة"))
        wallet = money.create_account(self.db, self.ctx, "فودافون كاش", "wallet")["id"]
        money.set_route(self.db, self.ctx, "transfer", None, self.bank)
        money.set_route(self.db, self.ctx, "transfer", cid, wallet)
        self.assertEqual(money.route_for(self.db, self.ctx.org_id, "transfer", cid), wallet)
        self.assertEqual(money.route_for(self.db, self.ctx.org_id, "transfer", "other"), self.bank)
        money.set_route(self.db, self.ctx, "transfer", cid, None)
        self.assertEqual(money.route_for(self.db, self.ctx.org_id, "transfer", cid), self.bank)


class TestCount(MoneyCase):
    def test_difference_needs_a_reason_and_is_recorded(self):
        money.post_opening(self.db, self.ctx, self.t, 10000)
        with self.assertRaises(ValidationFailed):
            money.count(self.db, self.m, self.t, 9500)
        r = money.count(self.db, self.m, self.t, 9500, "ورقة ناقصة")
        self.assertEqual((r["expected"], r["difference"], r["balance"]), (10000, -500, 9500))
        ok = money.count(self.db, self.m, self.t, 9500)        # matches: no reason needed, still kept
        self.assertEqual(ok["difference"], 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM money_counts WHERE account_id=?", (self.t,))["n"], 2)
        with self.assertRaises(Forbidden):
            money.count(self.db, self.a, self.t, 9500)


class TestReports(MoneyCase):
    def test_where_the_money_went(self):
        self.cd.open(self.ctx, 10000)
        self.sell(units=2)                                     # 40.00 cash in the drawer
        with self.db.transaction():
            money.pay_out(self.db, self.m, money.DRAWER, 1000, "expense", ref_type="expense", ref_id=None, note="أكياس")
        self.close_shift(counted=13000, float_left=10000)      # 30.00 to the treasury (internal)
        money.owner_movement(self.db, self.ctx, self.t, 500, "owner_withdrawal")
        s = money.summary(self.db, self.m, today(), today())
        self.assertEqual(len(s["periods"]), 1)
        p = s["periods"][0]
        kinds = {k["kind"]: (k["in"], k["out"]) for k in p["by_kind"]}
        self.assertEqual(kinds["sale"], (4000, 0))
        self.assertEqual(kinds["drawer_payout"], (0, 1000))
        self.assertEqual(kinds["shift_close"], (3000, 0))
        self.assertEqual(kinds["owner_withdrawal"], (0, 500))
        self.assertEqual((p["in"], p["out"]), (4000, 1500))   # the shift close is not money in
        month = money.summary(self.db, self.m, today(), today(), by="month")
        self.assertEqual(month["periods"][0]["period"], today()[:7])
        with self.assertRaises(Forbidden):
            money.summary(self.db, self.ph, today(), today())

    def test_movements_filters(self):
        money.post_opening(self.db, self.ctx, self.t, 10000)
        money.post_opening(self.db, self.ctx, self.bank, 2000)
        money.transfer(self.db, self.m, self.t, self.bank, 1000)
        self.assertEqual(len(money.movements(self.db, self.m, account_id=self.bank)["rows"]), 2)
        self.assertEqual(len(money.movements(self.db, self.m, kind="transfer")["rows"]), 2)
        with self.assertRaises(ValidationFailed):
            money.movements(self.db, self.m, kind="bogus")


class TestMigration(MoneyCase):
    def test_ledgers_rebuilt_without_losing_rows(self):
        """0038 rebuilds supplier_ledger and customer_ledger: rows written before it survive, with their columns."""
        tmp = Path(tempfile.mkdtemp(prefix="pharm-0038-"))
        try:
            old = tmp / "old"
            old.mkdir()
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.stem < "0038":
                    shutil.copy(f, old / f.name)
            d = Database(tmp / "x.db")
            d.migrate(old)
            with d.transaction():
                d.execute("INSERT INTO organizations (id, org_type, name) VALUES ('o', 'pharmacy', 'صيدلية')")
                d.execute("INSERT INTO suppliers (id, org_id, name, name_norm) VALUES ('s', 'o', 'مورد', 'مورد')")
                d.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind, pay_method, pay_ref)"
                          " VALUES ('l1', 'o', 's', -500, 'payment', 'cheque', '123')")
                d.execute("INSERT INTO customers (id, org_id, name) VALUES ('c', 'o', 'عميل')")
                d.execute("INSERT INTO customer_ledger (id, org_id, customer_id, amount, kind) VALUES ('k1','o','c',700,'credit_sale')")
            shutil.copy(MIGRATIONS_DIR / "0038_money_accounts.sql", old / "0038_money_accounts.sql")
            self.assertEqual(d.migrate(old), ["0038_money_accounts"])
            r = d.one("SELECT amount, kind, pay_method, pay_ref FROM supplier_ledger WHERE id='l1'")
            self.assertEqual(tuple(r), (-500, "payment", "cheque", "123"))
            self.assertEqual(d.one("SELECT amount FROM customer_ledger WHERE id='k1'")["amount"], 700)
            with d.transaction():
                d.execute("INSERT INTO supplier_ledger (id, org_id, supplier_id, amount, kind) VALUES ('l2','o','s',-10,'discount')")
            with self.assertRaises(sqlite3.DatabaseError), d.transaction():
                d.execute("UPDATE supplier_ledger SET amount=1 WHERE id='l1'")
            d.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestReadsWriteNothing(MoneyCase):
    def test_reports_run_inside_a_read_snapshot_before_the_treasury_exists(self):
        """API reports run in db.read_snapshot() (#249): a read must never create the treasury."""
        _, octx = self.second_tenant()
        people.add_user(self.db, octx, username="omgr", full_name="OM", password=PW, role_code="manager")
        om = self.login("omgr", PW)
        with self.db.read_snapshot():
            rows = money.accounts(self.db, om)
            money.movements(self.db, om, account_id="treasury")
            money.summary(self.db, om, today(), today())
        self.assertEqual([r["id"] for r in rows], [money.DRAWER, "treasury"])
        self.assertIsNone(money.find_treasury(self.db, octx.org_id))
