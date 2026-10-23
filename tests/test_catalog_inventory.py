import sqlite3
import unittest
from decimal import Decimal

from app.catalog import matching, service as cat
from app.catalog.normalize import normalize_barcode, parse_name, valid_gtin
from app.core.errors import Conflict, Forbidden, NotFound
from app.inventory import service as inv
from tests.helpers import DBTestCase


class TestNormalize(unittest.TestCase):
    def test_same_item_different_spellings(self):
        a = parse_name("AUGMENTIN 1GM 14 F.C.TAB")
        b = parse_name("Augmentin 1 g tab 14")
        c = parse_name("اوجمنتين ١ جم ١٤ قرص")
        for p in (a, b, c):
            self.assertEqual((p.strength, p.form, p.pack_count), ("1000mg", "tab", 14))
        self.assertEqual(a.core, b.core)
        self.assertEqual(a.skeleton, c.skeleton)   # Arabic meets English

    def test_strength_vs_content(self):
        p = parse_name("Brufen 100mg/5ml syrup 100ml")
        self.assertEqual((p.strength, p.content, p.form), ("100mg/5ml", "100ml", "syrup"))
        p = parse_name("Fucidin 2% cream 15gm")
        self.assertEqual((p.strength, p.content), ("2%", "15g"))

    def test_modifiers_canonical_across_scripts(self):
        self.assertEqual(parse_name("Panadol Extra 24 tab").modifiers,
                         parse_name("بانادول اكسترا ٢٤ قرص").modifiers)

    def test_barcode(self):
        self.assertEqual(normalize_barcode("6221025012345.0"), "62210250123450")
        self.assertEqual(normalize_barcode(" 6221-025 "), "6221025")
        self.assertTrue(valid_gtin("4006381333931"))
        self.assertFalse(valid_gtin("4006381333932"))


class CatalogCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.sup = cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما",
                                       supplier_type="distributor", base_discount_local_bp=2000)
        self.aug = cat.create_item(self.db, self.ctx, {
            "name_en": "Augmentin 1gm 14 tab", "name_ar": "اوجمنتين 1 جم 14 قرص",
            "pack_size": 2, "base_unit_name": "strip", "public_price": 14500},
            barcodes=["6221025012345"])
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab",
                                                       "pack_size": 2, "public_price": 4000})
        self.panx = cat.create_item(self.db, self.ctx, {"name_en": "Panadol Extra 24 tab",
                                                        "pack_size": 2, "public_price": 5600})
        self.cat50 = cat.create_item(self.db, self.ctx, {"name_en": "Cataflam 50mg 20 tab",
                                                         "public_price": 6000})
        self.cat25 = cat.create_item(self.db, self.ctx, {"name_en": "Cataflam 25mg 20 tab",
                                                         "public_price": 4000}, force=True)


class TestCatalog(CatalogCase):
    def test_duplicate_blocked_across_spelling(self):
        for name in ("AUGMENTIN 1 G 14 F.C.TAB", "Augmentin 1000 mg tab 14"):
            with self.assertRaises(Conflict) as e:
                cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": 2})
            self.assertIn(e.exception.code, ("possible_duplicate", "duplicate_item"))

    def test_hard_duplicate_even_with_force(self):
        with self.assertRaises(Conflict):
            cat.create_item(self.db, self.ctx, {"name_en": "augmentin 1 gm 14 tab", "pack_size": 2},
                            force=True)

    def test_different_strength_is_not_duplicate(self):
        cat.create_item(self.db, self.ctx, {"name_en": "Augmentin 625mg 14 tab"})

    def test_barcode_unique_per_org(self):
        with self.assertRaises(Conflict):
            cat.add_barcode(self.db, self.ctx, self.pan, "6221025012345")

    def test_price_history_on_update(self):
        cat.update_item(self.db, self.ctx, self.pan, {"public_price": 4500})
        rows = self.db.all("SELECT public_price, previous_price FROM item_price_history"
                           " WHERE item_id=? ORDER BY observed_at", (self.pan,))
        self.assertEqual([(r[0], r[1]) for r in rows], [(4000, None), (4500, 4000)])

    def test_search(self):
        self.assertEqual(cat.search_items(self.db, self.ctx, "6221025012345")[0]["id"], self.aug)
        ids = [r["id"] for r in cat.search_items(self.db, self.ctx, "panadol")]
        self.assertIn(self.pan, ids)
        ids = [r["id"] for r in cat.search_items(self.db, self.ctx, "بانادول")]
        self.assertIn(self.pan, ids)

    def test_tenant_isolation(self):
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            cat.get_item(self.db, other, self.aug)
        self.assertEqual(cat.search_items(self.db, other, "augmentin"), [])
        # same barcode is allowed in another pharmacy's catalog
        oid = cat.create_item(self.db, other, {"name_en": "Augmentin 1gm 14 tab"})
        cat.add_barcode(self.db, other, oid, "6221025012345")
        with self.assertRaises(NotFound):
            cat.add_barcode(self.db, other, self.aug, "1234567")

    def test_permission_enforced_in_service(self):
        from app.core import accounts
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        with self.assertRaises(Forbidden):
            cat.create_item(self.db, actx, {"name_en": "Something 10mg tab"})


class TestMatching(CatalogCase):
    def m(self, name, **kw):
        return matching.match_line(self.db, self.ctx.org_id, raw_name=name,
                                   supplier_id=self.sup, **kw)

    def test_barcode_auto(self):
        r = self.m("AUGMENTIN 1G TAB", raw_barcode="6221025012345")
        self.assertEqual((r.item_id, r.status, r.method), (self.aug, "auto", "barcode"))

    def test_barcode_name_conflict_needs_review(self):
        r = self.m("Cataflam 50mg 20 tab", raw_barcode="6221025012345")
        self.assertEqual(r.status, "suggested")
        self.assertIn("barcode_name_conflict", r.warnings)

    def test_fuzzy_exactish_auto(self):
        r = self.m("AUGMENTIN 1 GM 14 F.C.TAB")
        self.assertEqual((r.item_id, r.status), (self.aug, "auto"))

    def test_arabic_invoice_line(self):
        r = self.m("اوجمنتين ١ جم ١٤ قرص")
        self.assertEqual(r.item_id, self.aug)

    def test_extra_is_not_plain(self):
        r = self.m("PANADOL EXTRA 24 TAB")
        self.assertEqual(r.item_id, self.panx)
        r = self.m("PANADOL 500 MG 24 TAB")
        self.assertEqual(r.item_id, self.pan)

    def test_strength_mismatch_never_auto(self):
        r = self.m("CATAFLAM 75MG 20 TAB")
        self.assertNotEqual(r.status, "auto")

    def test_missing_strength_is_only_suggested(self):
        r = self.m("CATAFLAM 20 TAB")
        self.assertNotEqual(r.status, "auto")

    def test_unknown_item_unmatched(self):
        r = self.m("XYZZY 10MG TAB")
        self.assertEqual(r.status, "unmatched")

    def test_learned_alias_and_code(self):
        cat.add_alias(self.db, self.ctx, self.cat50, "CATAFLAM 50 TB/20", supplier_id=self.sup,
                      source="learned")
        r = self.m("CATAFLAM 50 TB/20")
        self.assertEqual((r.item_id, r.status, r.method), (self.cat50, "auto", "alias"))
        cat.set_supplier_code(self.db, self.ctx, self.sup, "C-50", self.cat50)
        r = self.m("weird text", raw_code="C-50")
        self.assertEqual(r.item_id, self.cat50)


class TestInventory(CatalogCase):
    def test_purchase_cost_and_balance(self):
        with self.db.transaction():
            b1 = inv.get_or_create_batch(self.db, self.ctx, self.pan, "A1", "2027-05-31", "month")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, batch_id=b1,
                              movement_type="purchase", unit_cost=Decimal("15"))
            b2 = inv.get_or_create_batch(self.db, self.ctx, self.pan, "B2", "2028-01-31", "month")
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=30, batch_id=b2,
                              movement_type="purchase", unit_cost=Decimal("17"))
        s = inv.stock_for_item(self.db, self.ctx, self.pan)
        self.assertEqual(s["total_qty"], 40)
        self.assertEqual([b["batch_no"] for b in s["batches"]], ["A1", "B2"])  # FEFO order
        self.assertEqual(Decimal(s["avg_cost"]), Decimal("16.5"))
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_no_negative_stock(self):
        with self.assertRaises(Conflict):
            with self.db.transaction():
                inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=-1,
                                  movement_type="adjustment")
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_ledger_append_only(self):
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=5,
                              movement_type="opening", unit_cost=Decimal("10"))
        with self.assertRaises(sqlite3.DatabaseError):
            with self.db.transaction():
                self.db.execute("UPDATE stock_movements SET qty_delta=50")

    def test_failed_transaction_leaves_nothing(self):
        try:
            with self.db.transaction():
                inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=5,
                                  movement_type="opening", unit_cost=Decimal("10"))
                raise RuntimeError("power cut")
        except RuntimeError:
            pass
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.pan)["total_qty"], 0)
        self.assertEqual(self.db.one("SELECT count(*) c FROM stock_movements")["c"], 0)

    def test_cross_tenant_movement_rejected(self):
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            with self.db.transaction():
                inv.post_movement(self.db, other, item_id=self.pan, qty_delta=5,
                                  movement_type="opening")


if __name__ == "__main__":
    unittest.main()
