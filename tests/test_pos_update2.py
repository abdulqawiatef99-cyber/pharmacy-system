"""Update 2 sale screen: U2-12 stock on the line, U2-13 change the unit of a line, U2-35 nearest expiry in the
search, U2-34 services group, U2-18 quick sale from 14 days, U2-11 full new-customer form, U2-32 sign-out
confirmation preference. Synthetic data only."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import alternatives
from app.catalog import service as cat
from app.core import people
from app.core.errors import DomainError
from app.inventory import service as inv
from app.sales.service import local_today
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_owner_plan_priority_1_2 import PlanCase
from tests.test_sales import SalesCase


class TestLine(SalesCase):
    def test_line_shows_stock(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.assertEqual(d["lines"][0]["item"]["stock"], 13)

    def test_unit_change_rechecks_stock(self):
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=5)           # 5 strips
        lid = d["lines"][0]["id"]
        d = self.s.update_line(self.ctx, sid, lid, qty_base=6)           # 5 -> 3 boxes of 2? the screen sends n*ps
        self.assertEqual(d["lines"][0]["qty_base"], 6)
        with self.assertRaises(DomainError):                           # 7 boxes = 14 strips > 13 in stock
            self.s.update_line(self.ctx, sid, lid, qty_base=14)

    def test_quick_services_group(self):
        q = alternatives.quick_items(self.db, self.ctx)
        self.assertEqual(q["days"], 14)
        self.assertEqual(q["services"], [])
        self.assertIn("قياس ضغط", q["suggested_services"])
        cat.create_item(self.db, self.ctx, {"name_ar": "قياس ضغط", "is_service": True, "public_price": 1000})
        q = alternatives.quick_items(self.db, self.ctx)
        self.assertEqual([x["name_ar"] for x in q["services"]], ["قياس ضغط"])
        self.assertEqual(q["suggested_services"], [])

    def test_logout_confirm_preference(self):
        self.assertEqual(people.get_prefs(self.db, self.ctx.user_id)["logout_confirm"], "on")
        people.set_prefs(self.db, self.ctx, {"logout_confirm": "off"})
        self.assertEqual(people.get_prefs(self.db, self.ctx.user_id)["logout_confirm"], "off")


class TestSearchExpiry(PlanCase):
    def test_nearest_expiry_in_search(self):
        soon = (local_today() + timedelta(days=40)).isoformat()
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.pan, "SOON", soon)
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=2, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("15"))
        r = self.call("GET", "/api/items?q=Panadol&stock=first")
        row = next(x for x in (r["items"] if isinstance(r, dict) else r) if x["id"] == self.pan)
        self.assertEqual(row["nearest_expiry"], soon)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.fill('#posQ', 'Trial Box');
  await p.waitForSelector('#posRes [data-add]');
  const out = { res: await p.textContent('#posRes') };
  await p.click('#posRes [data-add][data-kind=pack]');
  await p.waitForSelector('#posCart [data-unit]');
  out.stock = await p.textContent('#posCart tbody tr td:first-child');
  out.before = await p.textContent('#posCart .qty b');
  await p.selectOption('#posCart [data-unit]', 'unit');
  await p.waitForFunction(() => !/علبة/.test(document.querySelector('#posCart .qty b').textContent));
  out.after = await p.textContent('#posCart .qty b');
  await p.click('#posCustNew');
  await p.waitForSelector('#custNewDlg');
  out.fields = await p.$$eval('#custNewDlg input', (i) => i.map((x) => x.name));
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestScreen(PlanCase):
    def setUp(self):
        super().setUp()
        from app.sales.cash import CashDrawer
        CashDrawer(self.db).open(self.ctx, 10000)

    def test_unit_switch_stock_and_customer_form(self):
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertIn("أقرب صلاحية", out["res"])
        self.assertIn("الرصيد: 10 علبة", out["stock"])               # the shelf: 30 strips (a draft reserves none)
        self.assertIn("1 علبة", out["before"])
        self.assertIn("1 ", out["after"])
        self.assertEqual(out["fields"], ["name", "phone", "phone2", "address1"])


class TestCrashKeepsDraft(SalesCase):
    def test_sale_survives_a_killed_process(self):
        """U2-25: a process that dies mid-invoice (os._exit, no close, no cleanup) leaves the sale on disk as a
        draft; after the restart it is listed as «معلقة» with its lines."""
        import subprocess
        import sys
        import textwrap
        from app.core import accounts
        tok = accounts.login(self.db, "owner", "Owner-Pass-2026")["token"]
        self.db.close()
        code = textwrap.dedent(f"""
            import os
            from app.core import accounts
            from app.db import Database
            from app.sales.service import Sales
            db = Database({str(self.tmp / 'test.db')!r})
            ctx = accounts.resolve_session(db, {tok!r})
            s = Sales(db)
            sid = s.create(ctx)
            s.add_line(ctx, sid, {self.pan!r}, units=2)
            print(sid, flush=True)
            os._exit(9)                       # killed mid-invoice: nothing closed, nothing flushed by us
        """)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                           cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
        self.assertEqual(r.returncode, 9, r.stderr[-1500:])
        sid = r.stdout.strip().splitlines()[-1]
        from app.db import Database
        self.db = Database(self.tmp / "test.db")
        self.ctx = self.login("owner", "Owner-Pass-2026")
        drafts = {d["id"]: d for d in self.s.__class__(self.db).list_drafts(self.ctx)}
        self.assertEqual(drafts[sid]["lines"], 1)
