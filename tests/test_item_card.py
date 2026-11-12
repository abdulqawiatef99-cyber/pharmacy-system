"""Item card, sections with no-return, shortage mark, 2D codes on packs."""
from datetime import timedelta
from decimal import Decimal

from app.catalog import card, gs1, service as cat
from app.catalog.search import search
from app.core import accounts, notify, people
from app.core.errors import Forbidden
from app.inventory import service as inv
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base, T

PW = "Staff-Pass-2026"


class TestGS1(Base):
    def test_parse_shapes(self):
        self.assertEqual(gs1.parse("0106221043010359172806301012345\x1d21ABC123"),
                         {"gtin": "06221043010359", "expiry": "2028-06-30", "batch": "12345", "serial": "ABC123"})
        self.assertEqual(gs1.parse("(01)06221043010359(17)280600(10)XY9")["expiry"], "2028-06-30")   # day 00 = month end
        self.assertEqual(gs1.parse("https://id.gs1.org/01/06221043010359/10/B7?17=290131")["batch"], "B7")
        self.assertIsNone(gs1.parse("6221043010359"))
        self.assertIsNone(gs1.parse("panadol"))

    def test_scan_2d_code_finds_item_by_its_normal_barcode(self):
        r = search(self.db, self.ctx, "0106221025012345172806301012345")      # pan has EAN 6221025012345
        self.assertEqual([i["id"] for i in r["items"]], [self.pan])
        self.assertEqual((r["hint"]["kind"], r["hint"]["expiry"], r["hint"]["batch"]), ("gs1", "2028-06-30", "12345"))
        self.assertEqual(search(self.db, self.ctx, "06221025012345")["items"][0]["id"], self.pan)
        r = search(self.db, self.ctx, "0109999999999999172806301012345")
        self.assertEqual((r["items"], r["hint"]["kind"]), ([], "gs1"))


class TestCard(Base):
    def setUp(self):
        super().setUp()
        card.ensure_default_categories(self.db)
        self.cats = {c["name"]: c for c in card.list_categories(self.db, self.ctx)}
        people.add_user(self.db, self.ctx, username="pharm", full_name="Ph", password=PW, role_code="pharmacist")
        self.ph = self.login("pharm", PW)

    def test_defaults_and_card(self):
        self.assertTrue(self.cats["ثلاجة"]["no_return"] and self.cats["ثلاجة"]["mark"] == "fridge")
        self.assertEqual(self.cats["حقن"]["mark"], "injection")
        c = card.card(self.db, self.ctx, self.pan)
        self.assertEqual(c["stock_qty"], 20)
        self.assertEqual(c["barcodes"], [{"code": "6221025012345", "gtin": False}])   # test fixture has a wrong check digit
        self.assertTrue(card.valid_gtin("6221043010359"))
        self.assertEqual(len(c["monthly"]), 12)
        self.assertEqual(c["avg_cost_pack"], 3000)            # 15/unit x 2 units, piasters
        self.assertEqual(c["margin_pct"], 25.0)
        self.assertNotIn("avg_cost_pack", card.card(self.db, self.ph, self.pan))   # cost: owner only

    def test_average_cost_is_exact_half_up(self):
        # 0.145/unit: float gives 14.499999... and round() gave 14; 0.125: round() half to even gave 12
        for unit_cost, pack_cost, margin in (("0.145", 15, 98.6), ("0.125", 13, 98.8), ("10.0005", 1000, 0.0)):
            with self.subTest(unit_cost=unit_cost):
                it = cat.create_item(self.db, self.ctx, {"name_en": f"Cost {unit_cost}", "pack_size": 1,
                                                         "public_price": 1000})
                with self.db.transaction():
                    b = inv.get_or_create_batch(self.db, self.ctx, it, "C1", (T + timedelta(days=400)).isoformat())
                    inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=1, batch_id=b,
                                      movement_type="purchase", unit_cost=Decimal(unit_cost))
                c = card.card(self.db, self.ctx, it)
                self.assertEqual((c["avg_cost_pack"], c["margin_pct"]), (pack_cost, margin))
                self.assertNotEqual(str(c["margin_pct"]), "-0.0")

    def test_sections_need_owner_for_no_return(self):
        with self.assertRaises(Forbidden):                     # into a no-return section
            card.set_category(self.db, self.ph, [self.pan], self.cats["ثلاجة"]["id"])
        card.set_category(self.db, self.ph, [self.pan], self.cats["أدوية"]["id"])      # ordinary: fine
        with self.assertRaises(Forbidden):
            card.save_category(self.db, self.ph, {"no_return": 1}, self.cats["أدوية"]["id"])
        card.save_category(self.db, self.ph, {"name": "أدوية عامة"}, self.cats["أدوية"]["id"])
        card.save_category(self.db, self.ctx, {"no_return": 1}, self.cats["مستحضرات تجميل"]["id"])
        kinds = [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]]
        self.assertIn("no_return_change", kinds)

    def test_no_return_blocks_returns_even_for_the_seller(self):
        card.set_category(self.db, self.ctx, [self.pan], self.cats["ثلاجة"]["id"])
        self.cd.open(self.ctx, 0)
        sale = self.sell(ctx=self.ph)
        line = {sale["lines"][0]["id"]: 1}
        with self.assertRaises(Forbidden) as e:
            self.s.return_items(self.ph, sale["id"], line, refund_method="cash", reason="مش عايزه")
        self.assertEqual(e.exception.details["permission"], "sales.return_restricted")
        self.s.return_items(self.ctx, sale["id"], line, refund_method="cash", reason="استثناء الصاحب")
        n = notify.listing(self.db, self.ctx)["items"][0]
        self.assertEqual((n["kind"], n["severity"]), ("return", "error"))

    def test_shortage_mark(self):
        card.set_shortage(self.db, self.ph, self.pan, True)
        c = card.card(self.db, self.ctx, self.pan)
        self.assertTrue(c["shortage_at"])
        self.assertEqual(c["shortage_by_name"], "Ph")
        card.set_shortage(self.db, self.ph, self.pan, False)
        self.assertIsNone(card.card(self.db, self.ctx, self.pan)["shortage_at"])


class TestCardAPI(APICase):
    def test_card_categories_barcode(self):
        card.ensure_default_categories(self.db)
        self.http_login()
        cats = self.c.get("/api/categories").json()["categories"]
        fridge = next(c for c in cats if c["name"] == "ثلاجة")
        r = self.c.post(f"/api/items/{self.pan}/category", json={"category_id": fridge["id"]}, headers=H)
        self.assertEqual(r.json()["category"], "ثلاجة")
        r = self.c.post(f"/api/items/{self.pan}/barcode", json={"barcode": "6221025099998"}, headers=H)
        self.assertEqual(len(r.json()["barcodes"]), 1)
        r = self.c.post(f"/api/items/{self.pan}/barcode", json={"barcode": "6221025099998", "remove": True}, headers=H)
        self.assertEqual(r.json()["barcodes"], [])
        r = self.c.post("/api/categories", json={"name": "مكملات غذائية"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.get("/api/items", params={"q": "0106221025012345", "meta": "1"}).json()
        self.assertEqual(r["hint"]["kind"], "gs1")


class TestMovements(Base):
    def test_period_opening_rows_closing(self):
        from app.sales.service import local_today
        self.cd.open(self.ctx, 0)
        sale = self.sell(units=2)
        today = local_today().isoformat()
        m = card.movements(self.db, self.ctx, self.pan, today, today)
        self.assertEqual(m["opening"] + sum(r["qty_delta"] for r in m["rows"]), m["closing"])
        self.assertEqual(m["closing"], 18)
        self.assertEqual(m["rows"][-1]["sale_no"], sale["sale_no"])
        self.assertIn("بيع", [t["label"] for t in m["totals"]])
        nxt = (local_today() + timedelta(days=1)).isoformat()
        self.assertEqual(card.movements(self.db, self.ctx, self.pan, nxt, nxt)["opening"], 18)
