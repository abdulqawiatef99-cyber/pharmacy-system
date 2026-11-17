"""Similar items (same ingredient/strength/form), owner-defined alternatives, ranking, quick-sale buttons."""
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import alternatives as alts
from app.catalog import service as cat
from app.catalog.normalize import ingredient_key
from app.core import people
from app.core.errors import Forbidden, ValidationFailed
from app.inventory import service as inv
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"
T = date.today()


class TestIngredientKey(Base):
    def test_order_case_strength_ignored(self):
        self.assertEqual(ingredient_key("Amoxicillin 875mg + Clavulanic Acid 125mg"), ingredient_key("clavulanic acid / AMOXICILLIN"))
        self.assertEqual(ingredient_key("Paracetamol 500 MG"), "paracetamol")
        self.assertNotEqual(ingredient_key("Vitamin B12"), ingredient_key("Vitamin B6"))
        self.assertNotEqual(ingredient_key("paracetamol"), ingredient_key("acetaminophen"))    # no synonyms guessed
        self.assertIsNone(ingredient_key("500 mg"))


class TestSimilar(Base):
    def item(self, name, ing, strength, form, price, qty=0, expiry_days=400, cost="10", **kw):
        i = cat.create_item(self.db, self.ctx, {"name_en": name, "active_ingredient": ing, "strength": strength,
                                                "dosage_form": form, "public_price": price, "pack_size": 1, **kw}, force=True)
        if qty:
            with self.db.transaction():
                b = inv.get_or_create_batch(self.db, self.ctx, i, f"B{name}", (T + timedelta(days=expiry_days)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=i, qty_delta=qty, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal(cost))
        return i

    def test_similar_ranked_and_kept_apart(self):
        a = self.item("Brand A", "Paracetamol 500mg", "500 mg", "tab", 3000, qty=5)
        far_dear = self.item("Brand B", "paracetamol", "500mg", "cap", 5000, qty=5)            # cap = same form group
        soon = self.item("Brand C", "PARACETAMOL", "500 mg", "tab", 2000, qty=5, expiry_days=60)
        none = self.item("Brand D", "paracetamol", "500 mg", "tab", 9000)                      # no stock: last
        cheap_margin = self.item("Brand E", "paracetamol", "500 mg", "tab", 5000, qty=5, cost="45")
        syrup = self.item("Brand Syrup", "paracetamol", "120mg/5ml", "syrup", 1500, qty=3)
        r = alts.for_item(self.db, self.ctx, a)
        self.assertEqual([x["id"] for x in r["similar"]], [soon, far_dear, cheap_margin, none])
        self.assertTrue(r["similar"][0]["expiry_soon"])
        self.assertGreater(r["similar"][1]["margin_bp"], r["similar"][2]["margin_bp"])
        self.assertEqual([x["id"] for x in r["other_strength"]], [syrup])
        self.assertEqual(r["missing"], [])

    def test_missing_ingredient_and_hidden(self):
        x = cat.create_item(self.db, self.ctx, {"name_en": "No Ingredient 5mg", "public_price": 100}, force=True)
        self.assertEqual(alts.for_item(self.db, self.ctx, x)["missing"], ["ingredient"])
        a = self.item("Omez", "omeprazole", "20 mg", "cap", 3000)
        h = self.item("Secret Omep", "omeprazole", "20 mg", "cap", 3000, hidden=True)
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        asst = self.login("asst", PW)
        self.assertEqual(alts.for_item(self.db, asst, a)["similar"], [])
        self.assertEqual([y["id"] for y in alts.for_item(self.db, self.ctx, a)["similar"]], [h])
        self.item("Omepral", "omeprazole", "20 mg", "cap", 3000)
        seen = alts.for_item(self.db, asst, a)["similar"]
        self.assertEqual(len(seen), 1)
        self.assertNotIn("margin_bp", seen[0])                       # cost-based numbers for costs.read only

    def test_edit_ingredient_updates_key(self):
        a = self.item("Zed", "omeprazole", "20 mg", "cap", 3000)
        cat.update_item(self.db, self.ctx, a, {"active_ingredient": "Esomeprazole 20mg"})
        self.assertEqual(self.db.one("SELECT ingredient_key k FROM items WHERE id=?", (a,))["k"], "esomeprazole")
        cat.update_item(self.db, self.ctx, a, {"active_ingredient": ""})
        self.assertIsNone(self.db.one("SELECT ingredient_key k FROM items WHERE id=?", (a,))["k"])


class TestGroups(TestSimilar):
    def test_alternatives_from_owner_groups_only(self):
        omez = self.item("Omez", "omeprazole", "20 mg", "cap", 3000, qty=2)
        nexium = self.item("Nexium", "esomeprazole", "40 mg", "tab", 9000, qty=2)
        panto = self.item("Controloc", "pantoprazole", "40 mg", "tab", 7000)
        antacid = self.item("Gaviscon", "sodium alginate", None, "susp", 4000, qty=1)
        self.assertEqual(alts.for_item(self.db, self.ctx, omez)["alternatives"], [])          # nothing guessed
        g = alts.save_group(self.db, self.ctx, {"name": "مثبطات مضخة البروتون", "members": [
            {"kind": "ingredient", "label": "Omeprazole"}, {"kind": "ingredient", "label": "esomeprazole 40mg"},
            {"kind": "ingredient", "label": "Pantoprazole"}]})
        self.assertEqual(len(g[0]["members"]), 3)
        r = alts.for_item(self.db, self.ctx, omez)
        self.assertEqual([x["id"] for x in r["alternatives"]], [nexium, panto])                 # in stock first
        self.assertEqual(r["alternatives"][0]["groups"], ["مثبطات مضخة البروتون"])
        # an item added by hand
        gid = g[0]["id"]
        alts.save_group(self.db, self.ctx, {"name": "مثبطات مضخة البروتون", "members": [
            {"kind": "ingredient", "label": "Omeprazole"}, {"kind": "item", "ref": antacid}]}, gid)
        self.assertEqual([x["id"] for x in alts.for_item(self.db, self.ctx, omez)["alternatives"]], [antacid])
        self.assertEqual([x["id"] for x in alts.for_item(self.db, self.ctx, antacid)["alternatives"]], [omez])
        alts.delete_group(self.db, self.ctx, gid)
        self.assertEqual(alts.for_item(self.db, self.ctx, omez)["alternatives"], [])

    def test_rules(self):
        with self.assertRaises(ValidationFailed):
            alts.save_group(self.db, self.ctx, {"name": "x1", "members": [{"kind": "ingredient", "label": "omeprazole"}]})
        with self.assertRaises(ValidationFailed):
            alts.save_group(self.db, self.ctx, {"name": "x1", "members": [{"kind": "ingredient", "label": "500 mg"},
                                                                         {"kind": "ingredient", "label": "omeprazole"}]})
        people.add_user(self.db, self.ctx, username="pharm1", full_name="P", password=PW, role_code="pharmacist")
        with self.assertRaises(Forbidden):
            alts.save_group(self.db, self.login("pharm1", PW), {"name": "g", "members": [
                {"kind": "ingredient", "label": "a"}, {"kind": "ingredient", "label": "b"}]})


class TestQuick(Base):
    def test_pinned_then_best_sellers(self):
        self.cd.open(self.ctx, 0)
        self.sell(units=2)
        self.sell(units=1)
        pin = cat.create_item(self.db, self.ctx, {"name_en": "Delivery", "public_price": 1000, "is_service": True, "quick_pin": True})
        r = alts.quick_items(self.db, self.ctx)
        self.assertEqual([x["id"] for x in r["items"]], [pin, self.pan])
        self.assertTrue(r["items"][0]["pinned"])
        self.assertEqual(r["items"][1]["stock_qty"], 17)


class TestApi(APICase):
    def test_routes(self):
        self.http_login()
        r = self.c.get(f"/api/items/{self.pan}/alternatives", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("similar", r.json())
        r = self.c.post("/api/therapeutic-groups", json={"name": "مسكنات", "members": [
            {"kind": "ingredient", "label": "paracetamol"}, {"kind": "ingredient", "label": "ibuprofen"}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        gid = r.json()["groups"][0]["id"]
        self.assertEqual(self.c.delete(f"/api/therapeutic-groups/{gid}", headers=H).json()["groups"], [])
        self.assertEqual(self.c.get("/api/pos/quick", headers=H).status_code, 200)
        self.assertIn("ingredients", self.c.get("/api/ingredients", headers=H).json())
        r = self.c.post("/api/items/flags", json={"item_ids": [self.pan], "quick_pin": True}, headers=H)
        self.assertEqual(r.json()["updated"], 1)


class TestSameItemOneLine(Base):
    def test_repeat_scans_merge_and_price_as_pack(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.pan, units=1, client_ref="r1")
        d = self.s.add_line(self.ctx, sid, self.pan, units=1, client_ref="r2")   # pack of 2 strips = 40.00
        self.assertEqual(len(d["lines"]), 1)
        self.assertEqual(d["lines"][0]["qty_base"], 2)
        self.assertEqual(d["total"], 4000)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1, client_ref="r1")   # retried old request: ignored
        self.assertEqual(d["lines"][0]["qty_base"], 2)
        d = self.s.add_line(self.ctx, sid, self.pan, units=1, discount_bp=500)  # a different discount: own line
        self.assertEqual(len(d["lines"]), 2)


class TestQuickButtonsRanking(Base):
    """#253: the best-seller ranking is kept for a few minutes; the items on the buttons are always read fresh."""

    def setUp(self):
        super().setUp()
        alts.forget_best_sellers()
        self.cd.open(self.ctx, 0)
        self.sell(units=1)

    def ids(self):
        return [x["id"] for x in alts.quick_items(self.db, self.ctx)["items"]]

    def test_second_call_does_not_count_the_sale_lines_again(self):
        self.assertEqual(self.ids(), [self.pan])
        real, seen = self.db.all, []
        self.db.all = lambda sql, *a, **k: (seen.append(sql), real(sql, *a, **k))[1]
        try:
            self.assertEqual(self.ids(), [self.pan])
        finally:
            del self.db.all
        self.assertFalse([q for q in seen if "sale_lines" in q], seen)

    def test_a_stopped_item_leaves_the_buttons_at_once(self):
        self.assertEqual(self.ids(), [self.pan])
        with self.db.transaction():
            self.db.execute("UPDATE items SET status='inactive' WHERE org_id=? AND id=?", (self.ctx.org_id, self.pan))
        self.assertEqual(self.ids(), [])

    def test_the_ranking_is_counted_again_after_its_time(self):
        self.assertEqual(self.ids(), [self.pan])
        with self.db.transaction():
            self.db.execute("UPDATE sales SET status='voided' WHERE org_id=?", (self.ctx.org_id,))
        self.assertEqual(self.ids(), [self.pan])                 # still the kept ranking
        old = alts._TOP_TTL
        alts._TOP_TTL = 0
        try:
            self.assertEqual(self.ids(), [])
        finally:
            alts._TOP_TTL = old
