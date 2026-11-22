"""B3: a purchase line with the distributor's fixed margin per pack (Pharma Overseas papers, owner 22/11).

The paper shows a margin «m+m» per pack: the pharmacist price is (public - 2m) x (1 - discount), the paper's line is
qty x pharmacist price (VAT already inside a taxable item's public price), and the margins are added once at the
bottom: paper total = sum(lines) + sum(qty x m). The program must reach the paper's total to the piaster, and the
cost of a pack is the pharmacist price + m. Item names are synthetic; the figures are the owner's paper of 22/11
(lines 2,217.80 + margins 21.00 = 2,238.80; the other lines of that paper are replaced by synthetic lines of the
same value)."""
import tempfile
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app import config
from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.db import MIGRATIONS_DIR, Database
from app.procurement import returns
from app.procurement.smart_order import real_discount_bp
from app.purchasing import validation
from app.purchasing.service import Purchasing
from tests.helpers import DBTestCase

FUT = (date.today() + timedelta(days=500)).isoformat()


class TestDeriveWithMargin(DBTestCase):
    def derive(self, **kw):
        line = {"qty": kw.pop("qty", 1), "field_sources": {}, "field_confidence": {}, "public_price": None,
                "discount_bp": None, "unit_net_price": None, "tax_amount": None, "line_total": None,
                "distributor_margin": 0, **kw}
        return validation.derive_line(line)

    def test_margin_4_plus_4(self):
        l = self.derive(public_price=10800, discount_bp=2000, distributor_margin=400, qty=5)
        self.assertEqual((l["unit_net_price"], l["line_total"]), (8000, 40000))       # 80/pack, line 400
        self.assertEqual(validation.effective_amount(l), 42000)                       # + 5 x 4 = 420

    def test_margin_1_plus_1(self):
        l = self.derive(public_price=15300, discount_bp=2000, distributor_margin=100, qty=1)
        self.assertEqual((l["unit_net_price"], l["line_total"]), (12080, 12080))
        self.assertEqual(validation.effective_amount(l), 12180)

    def test_taxable_item_vat_inside_the_public_price(self):
        l = self.derive(public_price=17500, discount_bp=2000, qty=1)
        self.assertEqual((l["unit_net_price"], l["line_total"]), (14000, 14000))
        self.assertEqual(validation.effective_amount(l), 14000)

    def test_discount_read_back_from_the_pharmacist_price(self):
        l = self.derive(public_price=10800, unit_net_price=8000, distributor_margin=400, qty=5)
        self.assertEqual(l["discount_bp"], 2000)          # not 25.93% (the owner had to fake it before B3)

    def test_no_margin_is_the_old_rule(self):
        l = self.derive(public_price=1070, discount_bp=2500, qty=10)
        self.assertEqual((l["unit_net_price"], l["line_total"]), (803, 8025))

    def test_margin_larger_than_half_the_public_price_is_refused(self):
        line = self.derive(public_price=1000, discount_bp=0, distributor_margin=600, qty=1)
        codes = [i.code for i in validation.validate({"supplier_id": "s", "declared_total": None}, [{**line, "id": "l1",
                 "item_id": "i", "match_status": "confirmed"}], items={}, today=date.today(), short_expiry_days=0,
                 total_tolerance=0) if i.level == "error"]
        self.assertIn("margin_too_large", codes)

    def test_net_price_check_uses_the_margin(self):
        line = {"id": "l1", "item_id": "i", "match_status": "confirmed", "qty": 5, "public_price": 10800,
                "discount_bp": 2000, "distributor_margin": 400, "unit_net_price": 8000, "line_total": 40000,
                "field_sources": {"unit_net_price": "user"}, "field_confidence": {}}
        codes = [i.code for i in validation.validate({"supplier_id": "s", "declared_total": 42000}, [line], items={},
                                                     today=date.today(), short_expiry_days=0, total_tolerance=0)]
        self.assertNotIn("net_price_mismatch", codes)
        self.assertNotIn("total_mismatch", codes)

    def test_smart_order_real_discount_counts_the_margin(self):
        # cost/pack 84.00 of 108.00 public = 22.22% real, never 25.93%
        self.assertEqual(real_discount_bp(10800, 8000 + 400, 5, 0, 2000), 2222)


class Paper(DBTestCase):
    """The 22/11 Pharma Overseas paper: 2,217.80 of lines + 21.00 margins = 2,238.80."""
    LINES = [  # (name, public, discount, margin, qty, pack_size)
        ("Margin four 40", 10800, 2000, 400, 5, 1),
        ("Margin one plus", 15300, 2000, 100, 1, 1),
        ("Taxed ACE", 17500, 2000, 0, 1, 1),
        ("Plain fifteen", 15000, 2000, 0, 10, 2),
        ("Plain eightynine", 8925, 2000, 0, 5, 1),
    ]

    def setUp(self):
        super().setUp()
        self.sup = cat.create_supplier(self.db, self.ctx, name="موزع بهامش")
        self.p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        self.inv = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, self.inv)["invoice"]["version"]
        self.p.update_header(self.ctx, self.inv, {"invoice_no": "PO-2211", "invoice_date": date.today().isoformat(),
                                                  "declared_total": 223880}, v)
        self.items = {}
        for i, (name, pub, disc, m, qty, ps) in enumerate(self.LINES):
            it = cat.create_item(self.db, self.ctx, {"name_en": name, "public_price": pub, "pack_size": ps})
            self.items[name] = it
            v = self.p.get(self.ctx, self.inv)["invoice"]["version"]
            self.p.add_line(self.ctx, self.inv, {"item_id": it, "qty": qty, "public_price": pub, "discount_bp": disc,
                                                 "distributor_margin": m, "batch_no": f"B{i}", "expiry_date": FUT}, v)

    def doc(self):
        return self.p.get(self.ctx, self.inv)

    def line(self, name):
        return next(l for l in self.doc()["lines"] if l["item_id"] == self.items[name])

    def balance(self):
        return self.db.one("SELECT coalesce(sum(amount),0) b FROM supplier_ledger WHERE supplier_id=?", (self.sup,))["b"]


class TestThePaper(Paper):
    def test_lines_and_total_match_the_paper_to_the_piaster(self):
        d = self.doc()
        self.assertEqual(sum(l["line_total"] for l in d["lines"]), 221780)          # the paper's lines
        self.assertEqual(sum(l["qty"] * l["distributor_margin"] for l in d["lines"]), 2100)
        self.assertEqual(d["invoice"]["computed_total"], 223880)
        self.assertEqual(d["invoice"]["declared_total"] - d["invoice"]["computed_total"], 0)
        self.assertEqual([i["code"] for i in d["issues"] if i["level"] == "error"], [])

    def test_approval_charges_the_supplier_the_paper_total_and_the_pack_cost_includes_the_margin(self):
        before = self.balance()
        self.p.approve(self.ctx, self.inv, self.doc()["invoice"]["version"])
        self.assertEqual(self.balance() - before, 223880)
        cost = lambda name: Decimal(self.db.one(                         # noqa: E731
            "SELECT unit_cost FROM stock_movements WHERE item_id=? AND movement_type='purchase'",
            (self.items[name],))["unit_cost"])
        self.assertEqual(cost("Margin four 40"), Decimal("84"))           # 80 + 4 a pack
        self.assertEqual(cost("Margin one plus"), Decimal("121.8"))       # 120.80 + 1
        self.assertEqual(cost("Taxed ACE"), Decimal("140"))               # VAT inside
        self.assertEqual(cost("Plain fifteen"), Decimal("60"))            # 120 a pack of 2

    def test_changing_the_margin_recomputes_the_line(self):
        l = self.line("Margin four 40")
        d = self.p.update_line(self.ctx, self.inv, l["id"], {"distributor_margin": 0}, self.doc()["invoice"]["version"])
        l = next(x for x in d["lines"] if x["id"] == l["id"])
        self.assertEqual((l["unit_net_price"], l["line_total"]), (8640, 43200))
        self.assertEqual(d["invoice"]["computed_total"], 223880 - 42000 + 43200)

    def test_a_negative_or_fractional_margin_is_refused(self):
        l = self.line("Margin four 40")
        for bad in (-100, 12.5, "x", "4.005"):
            with self.assertRaises(ValidationFailed):
                self.p.update_line(self.ctx, self.inv, l["id"], {"distributor_margin": bad},
                                   self.doc()["invoice"]["version"])
        self.assertEqual(self.line("Margin four 40")["distributor_margin"], 400)

    def test_margin_above_half_the_public_price_blocks_approval(self):
        l = self.line("Margin one plus")
        d = self.p.update_line(self.ctx, self.inv, l["id"], {"distributor_margin": 8000}, self.doc()["invoice"]["version"])
        self.assertIn("margin_too_large", [i["code"] for i in d["issues"] if i["level"] == "error"])

    def test_split_keeps_the_total(self):
        l = self.line("Margin four 40")
        d = self.p.split_line(self.ctx, self.inv, l["id"], 2, "12/2031", None, self.doc()["invoice"]["version"])
        parts = [x for x in d["lines"] if x["item_id"] == self.items["Margin four 40"]]
        self.assertEqual(sorted(x["qty"] for x in parts), [2, 3])
        self.assertEqual(sum(validation.effective_amount(x) for x in parts), 42000)
        self.assertEqual(d["invoice"]["computed_total"], 223880)

    def test_merge_keeps_the_total_and_refuses_another_margin(self):
        it = self.items["Margin four 40"]
        v = self.doc()["invoice"]["version"]
        d = self.p.add_line(self.ctx, self.inv, {"item_id": it, "qty": 1, "public_price": 10800, "discount_bp": 2000,
                                                 "distributor_margin": 400, "batch_no": "B0", "expiry_date": FUT}, v)
        a, b = [x for x in d["lines"] if x["item_id"] == it]
        d = self.p.merge_lines(self.ctx, self.inv, a["id"], b["id"], d["invoice"]["version"])
        m = next(x for x in d["lines"] if x["id"] == a["id"])
        self.assertEqual((m["qty"], validation.effective_amount(m)), (6, 50400))
        self.assertEqual(d["invoice"]["computed_total"], 223880 + 8400)
        d = self.p.add_line(self.ctx, self.inv, {"item_id": it, "qty": 1, "public_price": 10800, "discount_bp": 2000,
                                                 "distributor_margin": 300, "batch_no": "B0", "expiry_date": FUT},
                            d["invoice"]["version"])
        a, b = [x for x in d["lines"] if x["item_id"] == it]
        with self.assertRaises(ValidationFailed):
            self.p.merge_lines(self.ctx, self.inv, a["id"], b["id"], d["invoice"]["version"])

    def test_return_of_the_whole_line_credits_what_was_charged(self):
        self.p.approve(self.ctx, self.inv, self.doc()["invoice"]["version"])
        loaded = returns.from_invoice(self.db, self.ctx, self.sup, "PO-2211")
        l = next(x for x in loaded["lines"] if x["item_id"] == self.items["Margin four 40"])
        self.assertEqual(l["amount"], 42000)
        before = self.balance()
        r = returns.create_from_invoice(self.db, self.ctx, self.sup, self.inv,
                                        [{"purchase_line_id": l["id"], "paid_units": 5, "bonus_units": 0,
                                          "reason": "expired"}])
        self.assertEqual((r["total"], self.balance() - before), (42000, -42000))

    def test_pack_cost_above_the_public_price_warns(self):
        l = self.line("Margin four 40")
        d = self.p.update_line(self.ctx, self.inv, l["id"], {"unit_net_price": 10500}, self.doc()["invoice"]["version"])
        self.assertIn(("net_above_public", l["id"]), [(i["code"], i["line_id"]) for i in d["issues"]])

    def test_duplicate_hash_counts_the_margin_only_when_there_is_one(self):
        h = lambda: self.db.one("SELECT content_hash FROM purchase_invoices WHERE id=?", (self.inv,))["content_hash"]  # noqa: E731
        with_margin = h()
        l = self.line("Margin four 40")
        d = self.p.update_line(self.ctx, self.inv, l["id"], {"distributor_margin": 300, "line_total": 40000},
                               self.doc()["invoice"]["version"])
        self.assertNotEqual(h(), with_margin)
        lines = [dict(r) for r in self.db.all("SELECT * FROM purchase_invoice_lines WHERE invoice_id=?", (self.inv,))]
        self.assertTrue(all(isinstance(x["distributor_margin"], int) for x in lines))

    def test_return_screen_shows_the_pack_cost(self):
        self.p.approve(self.ctx, self.inv, self.doc()["invoice"]["version"])
        loaded = returns.from_invoice(self.db, self.ctx, self.sup, "PO-2211")
        l = next(x for x in loaded["lines"] if x["item_id"] == self.items["Margin four 40"])
        self.assertEqual(l["unit_net_price"], 8400)

    def test_purchases_tool_real_discount_counts_the_margin(self):
        from app.procurement.purchase_plan import _purchases
        self.p.approve(self.ctx, self.inv, self.doc()["invoice"]["version"])
        sups = {r["id"]: dict(r) for r in self.db.all("SELECT id, name, is_active FROM suppliers WHERE org_id=?",
                                                      (self.ctx.org_id,))}
        last, _ = _purchases(self.db, self.ctx, date.today(), sups)
        self.assertEqual(last[self.items["Margin four 40"]]["discount_bp"], 2222)     # 84 of 108, never 25.93%
        self.assertEqual(last[self.items["Plain fifteen"]]["discount_bp"], 2000)

    def test_item_card_supplier_price_is_the_pack_cost(self):
        from app.catalog import card
        self.p.approve(self.ctx, self.inv, self.doc()["invoice"]["version"])
        c = card.card(self.db, self.ctx, self.items["Margin four 40"])
        self.assertEqual(c["suppliers"][0]["unit_net_price"], 8400)


class TestMigration(DBTestCase):
    def test_existing_lines_get_no_margin_and_keep_their_amount(self):
        old, tmp = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "live.db"
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.stem < "0043":
                (old / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        db = Database(tmp)
        db.migrate(old)
        with db.transaction():
            db.execute("INSERT INTO organizations (id, name, org_type) VALUES ('o1', 'x', 'pharmacy')")
            db.execute("INSERT INTO branches (id, org_id, name) VALUES ('b1', 'o1', 'b')")
            db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, source_type) VALUES ('p1','o1','b1','manual')")
            db.execute("INSERT INTO purchase_invoice_lines (id, invoice_id, org_id, line_no, raw_name, qty,"
                       " unit_net_price, line_total) VALUES ('l1','p1','o1',1,'x',3,1000,3000)")
        applied = db.migrate(MIGRATIONS_DIR)
        self.assertTrue(any(a.startswith("0043") for a in applied))
        row = dict(db.one("SELECT * FROM purchase_invoice_lines WHERE id='l1'"))
        self.assertEqual((row["distributor_margin"], validation.effective_amount(row)), (0, 3000))
        self.assertEqual(db.migrate(MIGRATIONS_DIR), [])
        with self.assertRaises(Exception):          # the database refuses a negative margin too
            with db.transaction():
                db.execute("UPDATE purchase_invoice_lines SET distributor_margin=-1 WHERE id='l1'")


BROWSER = r"""
const { chromium } = require('playwright');
(async () => {
  const [base, user, pw, inv] = process.argv.slice(1);
""" + __import__("tests.pos_browser", fromlist=["LOGIN"]).LOGIN + r"""
  await p.goto(base + '/#/purchases/' + inv);
  await p.waitForSelector('#qaQ');
  await p.fill('#qaQ', 'Margin four');
  await p.waitForSelector('[data-qa="0"]');
  await p.click('[data-qa="0"]');
  await p.fill('#qaQty', '5'); await p.fill('#qaPrice', '108'); await p.fill('#qaDisc', '20');
  await p.focus('#qaDisc'); await p.keyboard.press('Enter');
  const next = await p.evaluate(() => document.activeElement.id);        // Enter walks into the margin box
  await p.keyboard.type('4');
  await p.fill('#qaExp', '1230'); await p.fill('#qaBatch', 'M1');
  await p.click('#qaAdd');
  await p.waitForSelector('table.lines input[data-field="distributor_margin"]');
  const row = await p.$eval('table.lines input[data-field="distributor_margin"]', (i) => i.closest('tr').querySelector('input[data-field="unit_net_price"]').value);
  const margin = await p.$eval('table.lines input[data-field="distributor_margin"]', (i) => i.value);
  const totals = await p.$eval('.totals', (t) => t.textContent);
  console.log(JSON.stringify({ next, row, margin, totals }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


@__import__("tests.helpers", fromlist=["needs_browser"]).needs_browser
class TestScreen(__import__("tests.test_api", fromlist=["APICase"]).APICase):
    def test_quick_add_takes_the_margin_and_the_totals_show_it(self):
        from tests.helpers import OWNER_PW
        from tests.pos_browser import run_node
        cat.create_item(self.db, self.ctx, {"name_en": "Margin four 40", "public_price": 10800})
        p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        inv_id = p.create_manual(self.ctx)
        out = run_node(self, BROWSER, "owner", OWNER_PW, inv_id)
        self.assertEqual(out["next"], "qaMargin")
        self.assertEqual((out["margin"], out["row"]), ("4.00", "80.00"))
        self.assertIn("هامشالموزع20.00", "".join(out["totals"].split()))
        self.assertEqual(p.get(self.ctx, inv_id)["invoice"]["computed_total"], 42000)
