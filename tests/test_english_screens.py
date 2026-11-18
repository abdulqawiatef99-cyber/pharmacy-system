"""English mode shows no Arabic on the main screens (owner/manager list 18/11): every visible text node and
placeholder/title on home, sale, invoices, purchases, items, counts, shift, reports, the stock balance check,
customers, expenses, suppliers, users, the program page (receipt / label / QR settings) and «me» is English.
Synthetic data with English names only, so whatever Arabic is left is the program's own text."""
from datetime import date, timedelta
from pathlib import Path
from decimal import Decimal

from app.catalog import service as cat
from app.inventory import service as inv
from tests.helpers import OWNER_PW, needs_browser
from tests.pos_browser import LOGIN, run_node
from tests.test_api import APICase

SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw] = process.argv.slice(1);
""" + LOGIN + r"""
  await p.setViewportSize({ width: 1366, height: 768 });
  // the program page asks GitHub for updates; on CI that can hit GitHub's rate limit. The test reads the page's
  // own text, so the update check answers a fixed "up to date" instead of depending on the network.
  await p.route('**/api/system/update', (r) => r.request().method() === 'GET'
    ? r.fulfill({ json: { version: '1.0', installed: null, latest: null, channel: 'develop', available: false } }) : r.continue());
  await p.evaluate(async () => fetch('/api/me/prefs', { method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'pharm' }, body: JSON.stringify({ lang: 'en' }) }));
  await p.goto(base + '/#/home'); await p.waitForSelector('#topbar:not([hidden])');
  const left = {};
  await p.goto(base + '/#/home'); await p.waitForSelector('#app h1');
  const greetEn = (await p.locator('#app h1').innerText()).trim();
  const screens = ['home', 'pos', 'sales', 'purchases', 'items', 'counts', 'shift', 'reports', 'stock-check', 'customers',
                   'expenses', 'suppliers', 'users', 'system', 'me'];
  for (const s of screens) {
    await p.goto(base + '/#/' + s);
    await p.waitForTimeout(1500);
    await p.evaluate(() => { document.querySelector('#tourBub [data-tour-go="skip"]')?.click(); });
    await p.waitForTimeout(300);
    left[s] = await p.evaluate(() => {
      const AR = /[؀-ۿ]/, out = new Set();
      const seen = (el) => el && el.getClientRects().length > 0;
      const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        const el = n.parentElement;
        if (!el || el.closest('script,style,[data-raw],.iname-ar,#toast') || !seen(el)) continue;
        if (AR.test(n.nodeValue)) out.add(n.nodeValue.trim().slice(0, 120));
      }
      for (const el of document.querySelectorAll('[placeholder],[title],[aria-label]')) {
        if (!seen(el)) continue;
        for (const a of ['placeholder', 'title', 'aria-label']) { const v = el.getAttribute(a); if (v && AR.test(v)) out.add(a + ': ' + v.slice(0, 120)); }
      }
      return [...out];
    });
  }
  console.log(JSON.stringify({ left, greetEn }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""

# what may stay Arabic on purpose: the search example shows an Arabic item name, the language picker its own name
ALLOWED = {"placeholder: مثال: panadol أو بنادول أو asp%cid أو 622...", "Example: panadol or بنادول or asp%cid or 622...",
           "placeholder: Example: panadol or بنادول or asp%cid or 622...", "العربية", "العربية (الافتراضي)"}


@needs_browser
class EnglishScreens(APICase):
    def test_no_arabic_left_in_english_mode(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Test Tablets", "public_price": 1000, "pack_size": 3})
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, it, "B1", (date.today() + timedelta(days=400)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=20, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("5"))
        with self.db.transaction():                    # English names: what stays Arabic is the program's own text
            self.db.execute("UPDATE organizations SET name='Test Pharmacy' WHERE id=?", (self.ctx.org_id,))
            self.db.execute("UPDATE users SET full_name='Test Owner' WHERE id=?", (self.ctx.user_id,))
            self.db.execute("UPDATE suppliers SET name='Test Supplier' WHERE org_id=?", (self.ctx.org_id,))
        out = run_node(self, SCRIPT, "owner", OWNER_PW)
        self.assertEqual(out["greetEn"], "Hello, Test Owner")          # owner 18/11: «السلام عليكم يا …» at any hour
        left = {s: [t for t in v if t not in ALLOWED] for s, v in out["left"].items()}
        self.maxDiff = None
        self.assertEqual({s: v for s, v in left.items() if v}, {})


class Greeting(__import__("unittest").TestCase):
    def test_home_greets_with_salam_at_any_hour(self):
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("<h1>السلام عليكم يا ${esc(ME.user.full_name)}</h1>", js)
        self.assertNotIn("صباح الخير", js)
        self.assertNotIn("مساء الخير", js)
