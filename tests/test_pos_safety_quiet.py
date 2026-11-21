"""Launch day 21/11 (owner): selling one of the item's own sale units (a strip of a box of 3 strips) is a normal
sale, and «no data» notes (no active ingredient, no interaction list) never stop it for a click. Only a real risk
asks for a confirmation."""
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import unittest

from app.catalog import service as cat
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestRule(unittest.TestCase):
    def test_only_blocking_findings_stop_the_sale(self):
        body = re.search(r"\nasync function complete\(findings\) \{.*?\n\}\n", JS, re.S).group(0)
        self.assertIn('if (blocking.length || r.findings.some((f) => f.code === "item_recall_pending"))', body)
        self.assertNotIn('f.severity !== "insufficient"', body)


SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.goto(base + '/#/pos');
  await p.waitForSelector('#posQ');
  await p.locator('#posQ').fill('Teststrip');
  await p.waitForSelector('#posRes [data-kind=unit]');
  await p.locator('#posRes [data-kind=unit]').first().click();
  await p.waitForSelector('table.cart [data-minus]');
  await p.keyboard.press('F9');
  await p.waitForFunction(() => document.querySelectorAll('table.cart [data-minus]').length === 0, null, { timeout: 10000 });
  console.log(JSON.stringify({ panel: await p.locator('#posConfirm').count() }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_browser
class TestStripSaleInBrowser(APICase):
    def test_a_strip_with_no_data_completes_without_a_click(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Teststrip 10 tab", "pack_size": 3, "public_price": 9000})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "S1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=9, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("20"))
        CashDrawer(self.db).open(self.ctx, 0)
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertEqual(out["panel"], 0)
        self.assertEqual(self.db.one("SELECT count(*) n FROM sales WHERE status='completed'")["n"], 1)
        self.assertEqual(self.db.one("SELECT qty_base FROM sale_lines")["qty_base"], 1)


if __name__ == "__main__":
    unittest.main()


class TestPaidBoxOnAnEmptyInvoice(unittest.TestCase):
    def test_disabled_until_there_is_a_line(self):
        # launch day 21/11: «العميل دفع» could be typed in on an empty invoice
        self.assertIn('placeholder="${money(s.total)}" ${s.lines.length ? "" : \'disabled title="ضيف صنف الأول"\'}', JS)
