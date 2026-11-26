"""UX-28 PR3 (owner 26/11, theft visibility): every invoice void and every cash refund of the shift is listed in the
owner's shift summary (who, when, invoice, amount, reason) with a total for the whole day, and the notifications say
which refunds were cash and carry today's total. Voiding stays owner-only by default. Synthetic data."""
import json
from dataclasses import replace

from app.core import notify
from app.reports.owner_summary import day_totals, shift_summary
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase
from tests.test_sales import SalesCase


class Base(SalesCase):
    def sell(self, units=2, card=False):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=units)
        pay = {"paid_card": d["total"]} if card else {"paid_cash": d["total"]}
        d = self.s.complete(self.ctx, sid, safety_decisions=self.ack_all(sid), **pay)
        return sid, d

    def shift(self):
        return self.db.one("SELECT id FROM cash_shifts WHERE status='open'")["id"]

    def owner_name(self):
        return self.db.one("SELECT full_name FROM users WHERE id=?", (self.ctx.user_id,))["full_name"]


class TestShiftSummary(Base):
    def test_each_void_and_cash_refund_is_listed_with_who_when_invoice_amount_reason(self):
        shift = self.shift()
        v, dv = self.sell(units=2)
        self.s.void(self.ctx, v, "العميل رجع في كلامه")
        r_sid, dr = self.sell(units=4)
        self.s.return_items(self.ctx, r_sid, {dr["lines"][0]["id"]: 1}, refund_method="cash", reason="علبة مفتوحة")
        c_sid, dc = self.sell(units=2, card=True)
        self.s.return_items(self.ctx, c_sid, {dc["lines"][0]["id"]: 1}, refund_method="card", reason="على الفيزا")
        CashDrawer(self.db).close(self.ctx, counted_cash=CashDrawer(self.db).report(self.ctx, shift)["expected_cash"] or 0,
                                  note="عدّ", float_left=0)
        out = shift_summary(self.db, self.ctx, shift)
        self.assertEqual(len(out["voids"]), 1)
        x = out["voids"][0]
        self.assertEqual((x["sale_no"], x["amount"], x["reason"], x["by"]), (dv["sale_no"], dv["total"],
                                                                            "العميل رجع في كلامه", self.owner_name()))
        self.assertTrue(x["at"])
        self.assertEqual(len(out["cash_refunds"]), 1)
        y = out["cash_refunds"][0]
        self.assertEqual((y["sale_no"], y["reason"], y["by"]), (dr["sale_no"], "علبة مفتوحة", self.owner_name()))
        self.assertGreater(y["amount"], 0)
        t = out["text"]
        self.assertIn(f"فاتورة {dv['sale_no']}", t)
        self.assertIn("العميل رجع في كلامه", t)
        self.assertIn("علبة مفتوحة", t)
        self.assertIn(self.owner_name(), t)
        # the list agrees with the shift report's own count
        self.assertEqual(len(out["voids"]), CashDrawer(self.db).report(self.ctx, shift)["voided_count"])

    def test_card_refund_and_draft_void_are_not_cash_events(self):
        shift = self.shift()
        sid, d = self.sell(units=2, card=True)
        self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="card", reason="رجّعها على الفيزا")
        draft = self.s.create(self.ctx)
        self.s.add_line(self.ctx, draft, self.pan, units=1)
        self.s.void(self.ctx, draft, "مسودة مش محتاجينها")
        CashDrawer(self.db).close(self.ctx, counted_cash=CashDrawer(self.db).report(self.ctx, shift)["expected_cash"] or 0,
                                  note="عدّ", float_left=0)
        out = shift_summary(self.db, self.ctx, shift)
        self.assertEqual((out["voids"], out["cash_refunds"]), ([], []))
        self.assertIn("مفيش إلغاء ولا مرتجع نقدي", out["text"])

    def test_day_total_covers_every_shift_of_the_day(self):
        cd = CashDrawer(self.db)
        first = self.shift()
        v1, d1 = self.sell(units=2)
        self.s.void(self.ctx, v1, "غلط في الصنف")
        cd.close(self.ctx, counted_cash=cd.report(self.ctx, first)["expected_cash"], note="عدّ", float_left=0)
        cd.open(self.ctx, 0)
        second = self.shift()
        r2, d2 = self.sell(units=4)
        self.s.return_items(self.ctx, r2, {d2["lines"][0]["id"]: 2}, refund_method="cash", reason="العميل غير رأيه")
        refund = self.db.one("SELECT total FROM sale_returns WHERE sale_id=?", (r2,))["total"]
        cd.close(self.ctx, counted_cash=cd.report(self.ctx, second)["expected_cash"], note="عدّ", float_left=0)
        out = shift_summary(self.db, self.ctx, second)
        self.assertEqual(len(out["voids"]), 0)                      # the first shift's void is not this shift's
        self.assertEqual(out["day"], {"voids": 1, "voids_total": d1["total"], "cash_refunds": 1,
                                      "cash_refunds_total": refund})
        self.assertIn("إجمالي اليوم", out["text"])

    def test_another_pharmacy_is_never_counted(self):
        _, other = self.second_tenant()
        CashDrawer(self.db).open(other, 0)
        self.assertEqual(day_totals(self.db, self.ctx), {"voids": 0, "voids_total": 0, "cash_refunds": 0,
                                                          "cash_refunds_total": 0})
        sid, d = self.sell(units=2)
        self.s.void(self.ctx, sid, "اختبار عزل البيانات")
        self.assertEqual(day_totals(self.db, other)["voids"], 0)
        self.assertEqual(day_totals(self.db, self.ctx)["voids"], 1)

    def test_day_totals_need_the_owner_or_manager_right(self):
        from app.core.errors import Forbidden
        with self.assertRaises(Forbidden):
            day_totals(self.db, replace(self.ctx, permissions=self.ctx.permissions - {"cash.see_expected"}))


class TestNotifications(Base):
    def test_cash_refund_notification_says_cash_with_who_and_reason(self):
        sid, d = self.sell(units=4)
        self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="علبة مفتوحة")
        n = [i for i in notify.listing(self.db, self.ctx)["items"] if i["kind"] == "return"][0]
        self.assertIn("نقدي", n["title"])
        self.assertIn(f"فاتورة {d['sale_no']}", n["title"])
        self.assertIn("علبة مفتوحة", n["body"])
        self.assertEqual(n["actor"], self.owner_name())

    def test_card_refund_notification_says_card(self):
        sid, d = self.sell(units=2, card=True)
        self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="card", reason="على الفيزا")
        n = [i for i in notify.listing(self.db, self.ctx)["items"] if i["kind"] == "return"][0]
        self.assertNotIn("نقدي", n["title"])
        self.assertIn("بطاقة", n["title"])

    def test_void_notification_has_invoice_amount_reason_and_who(self):
        sid, d = self.sell(units=2)
        self.s.void(self.ctx, sid, "العميل رجع في كلامه")
        n = [i for i in notify.listing(self.db, self.ctx)["items"] if i["kind"] == "void"][0]
        self.assertIn(f"فاتورة {d['sale_no']}", n["title"])
        self.assertIn("العميل رجع في كلامه", n["body"])
        self.assertEqual(n["actor"], self.owner_name())


class TestVoidOwnerOnly(Base):
    def test_only_the_owner_role_voids_by_default(self):
        roles = [r["code"] for r in self.db.all("SELECT code, permissions FROM roles WHERE org_id=?",
                                                 (self.ctx.org_id,)) if "sales.void" in json.loads(r["permissions"])]
        self.assertEqual(set(roles), {"owner"})


class TestNotificationsApi(APICase):
    def test_today_block_for_the_owner_only(self):
        self.http_login()
        r = self.c.get("/api/notifications").json()
        self.assertEqual(r["today"], {"voids": 0, "voids_total": 0, "cash_refunds": 0, "cash_refunds_total": 0})


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.evaluate(async () => {
    const { organization, user } = await (await fetch('/api/me')).json();
    localStorage.setItem(`tour:${organization.id}:${user.id}`, JSON.stringify({ all: 1 }));
  });
  const out = {};
  await p.goto(base + '/#/shift');
  await p.click('#shHist .shSum');
  await p.waitForSelector('#shSumEv tbody tr');
  out.rows = await p.$$eval('#shSumEv tbody tr', (trs) => trs.map((tr) => [...tr.cells].map((c) => c.textContent.trim())));
  out.text = await p.inputValue('#shSumT');
  await p.goto(base + '/#/notifications');
  await p.waitForSelector('#nToday');
  out.today = await p.textContent('#nToday');
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestScreens(Base):
    def setUp(self):
        super().setUp()
        from dataclasses import replace as dc_replace
        from app import config
        from app.api.app import create_app
        from app.purchasing.service import Purchasing
        st = dc_replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.tmp,
                        backup_dir=self.tmp / "backups")
        self.app = create_app(self.db, st, Purchasing(self.db, st))

    def test_shift_summary_rows_and_today_line(self):
        shift = self.shift()
        v, dv = self.sell(units=2)
        self.s.void(self.ctx, v, "العميل رجع في كلامه")
        r, dr = self.sell(units=4)
        self.s.return_items(self.ctx, r, {dr["lines"][0]["id"]: 1}, refund_method="cash", reason="علبة مفتوحة")
        cd = CashDrawer(self.db)
        cd.close(self.ctx, counted_cash=cd.report(self.ctx, shift)["expected_cash"], note="عدّ", float_left=0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        kinds = [row[0] for row in out["rows"]]
        self.assertEqual(kinds, ["إلغاء", "مرتجع نقدي"])
        self.assertEqual(out["rows"][0][1], str(dv["sale_no"]))
        self.assertEqual(out["rows"][0][6], "العميل رجع في كلامه")
        self.assertEqual(out["rows"][1][6], "علبة مفتوحة")
        self.assertIn("إجمالي اليوم", out["text"])
        self.assertIn("إلغاء 1 فاتورة", out["today"])
        self.assertIn("مرتجع نقدي 1", out["today"])


class TestDayTotalsPlan(Base):
    def test_old_shifts_movements_are_not_read(self):
        # review P1: cash_movements has no index on ref_id; the day total goes from the day's shifts to their
        # movements, so years of old shifts cost nothing. Counted in SQLite VM steps (no clock involved).
        sid, d = self.sell(units=4)
        self.s.return_items(self.ctx, sid, {d["lines"][0]["id"]: 1}, refund_method="cash", reason="علبة مفتوحة")
        with self.db.transaction():
            for n in range(300):
                sh = f"old-shift-{n}"
                self.db.execute("INSERT INTO cash_shifts (id, org_id, branch_id, status, opening_cash, opened_by,"
                                " opened_at, closed_at) VALUES (?,?,?,'closed',0,?,?,?)",
                                (sh, self.ctx.org_id, self.ctx.branch_id, self.ctx.user_id,
                                 f"2024-01-01T{n % 24:02d}:00:00.000000Z", f"2024-01-01T{n % 24:02d}:30:00.000000Z"))
                self.db.conn.executemany(
                    "INSERT INTO cash_movements (id, org_id, shift_id, amount, kind, ref_id, created_by)"
                    " VALUES (?,?,?,?,?,?,?)", [(f"{sh}-{k}", self.ctx.org_id, sh, -100, "refund", f"old-ret-{n}-{k}",
                                                 self.ctx.user_id) for k in range(20)])
            self.db.execute("ANALYZE")
        steps = [0]

        def tick():
            steps[0] += 1
            return 0
        self.db.conn.set_progress_handler(tick, 100)
        try:
            out = day_totals(self.db, self.ctx)
        finally:
            self.db.conn.set_progress_handler(None, 0)
        self.assertEqual(out["cash_refunds"], 1)
        self.assertLess(steps[0], 60, steps[0])     # 6,000 old movements read would be several hundred
