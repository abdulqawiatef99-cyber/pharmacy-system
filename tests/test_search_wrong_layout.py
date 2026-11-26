"""Owner, 26/11: a name typed with the keyboard on the other language must still find the item. The keys are
mapped by position (Windows Arabic 101 <-> US QWERTY): «شةخ» is "amo", "fhkh],g" is «بانادول». The query as
typed always goes first; the mapped one is used when the typed one finds nothing or only a weaker guess.
Synthetic data only."""
from unittest import mock

from app.catalog import matching, service as cat
from app.catalog.search import layout_variants, search, swap_layout
from tests.helpers import DBTestCase


class TestWrongLayout(DBTestCase):
    def setUp(self):
        super().setUp()
        matching.forget(self.ctx.org_id)
        self.addCleanup(matching.forget, self.ctx.org_id)
        mk = lambda **d: cat.create_item(self.db, self.ctx, {"public_price": 1000, **d}, force=True)   # noqa: E731
        self.amoxil = mk(name_en="Amoxil 500mg 16 cap", name_ar="اموكسيل 500 مجم 16 كبسولة")
        self.brufen = mk(name_en="Brufen 400mg 30 tab")
        self.night = mk(name_en="Night Cold 10 tab")
        self.panadol = mk(name_ar="بانادول اكسترا 24 قرص")                       # Arabic name only
        self.pectol = mk(name_en="Pectol syrup 100ml", name_ar="بيكتول شراب")
        self.fuji = mk(name_en="Fuji Gel 30g")                                    # has f, j and g in it
        self.decoy = mk(name_en="Zzdecoy 5mg 10 tab", name_ar="شاهوخء تجريبي")   # spelling key holds «شهخء»

    def find(self, q, **kw):
        r = search(self.db, self.ctx, q, **kw)
        return [i["id"] for i in r["items"]], r["hint"]

    def test_key_maps_both_ways(self):
        self.assertEqual(layout_variants("شةخ"), ["amo"])
        self.assertEqual(layout_variants("fhkh],g"), ["بانادول"])
        self.assertEqual(layout_variants("لاقعبثى"), ["brufen", "ghrufen"])     # the B key types «لا»
        self.assertEqual(layout_variants("ﻻقعبثى"), ["brufen", "ghrufen"])      # ... or its one-sign form
        self.assertEqual(layout_variants("BRUFEN"), ["لاقعبثى"])
        self.assertEqual(swap_layout("شةخ"), "amo")
        for same in ("asp اسب", "12345", "6221025012345", "", "   ", "%"):
            self.assertEqual(layout_variants(same), [], same)

    def test_english_name_typed_on_the_arabic_layout(self):
        self.assertEqual(self.find("شةخ"), ([self.amoxil], {"kind": "layout", "query": "amo"}))
        self.assertEqual(self.find("شةخءهم 500"), ([self.amoxil], {"kind": "layout", "query": "amoxil 500"}))
        self.assertEqual(self.find("لاقعبثى"), ([self.brufen], {"kind": "layout", "query": "brufen"}))
        self.assertEqual(self.find("ىهلاف"), ([self.night], {"kind": "layout", "query": "night"}))      # G then H

    def test_arabic_name_typed_on_the_english_layout(self):
        self.assertEqual(self.find("fhkh],g"), ([self.panadol], {"kind": "layout", "query": "بانادول"}))
        self.assertEqual(self.find("FHKH],G h;SJVH"), ([self.panadol], {"kind": "layout", "query": "بانادول اكسترا"}))
        self.assertEqual(self.find("hl,;sdg"), ([self.amoxil], {"kind": "layout", "query": "اموكسيل"}))

    def test_the_query_as_typed_goes_first(self):
        self.assertEqual(self.find("amo"), ([self.amoxil], None))
        self.assertEqual(self.find("بانادول"), ([self.panadol], None))
        # «اموكسيل» is a real name: its keys on the English layout are never tried
        with mock.patch("app.catalog.search.layout_variants", wraps=layout_variants) as mapped, \
                mock.patch("app.catalog.search._by_name", wraps=__import__("app.catalog.search", fromlist=["x"])._by_name) as by_name:
            self.assertEqual(self.find("اموكسيل"), ([self.amoxil], None))
        self.assertEqual(by_name.call_count, 1)
        self.assertLessEqual(mapped.call_count, 1)

    def test_a_mapped_name_beats_a_guess_by_spelling(self):
        # as typed, «شةخء» only resembles the decoy's Arabic name by spelling; as keys it is "amox"
        self.assertEqual(self.find("شةخء"), ([self.amoxil], {"kind": "layout", "query": "amox"}))

    def test_an_arabic_word_whose_keys_are_punctuation_keeps_its_spelling_guess(self):
        # «بكتول» on the English layout is "f;j,g": never read as the letters f, j, g of "Fuji Gel"
        ids, hint = self.find("بكتول")
        self.assertEqual((ids, hint), ([self.pectol], {"kind": "spelling"}))

    def test_a_misspelt_name_on_the_wrong_layout_is_still_guessed(self):
        ids, hint = self.find("شةخءثم")                                          # "amoxel"
        self.assertEqual(ids[0], self.amoxil)
        self.assertEqual(hint, {"kind": "layout", "query": "amoxel", "guess": True})
        ids, hint = self.find("fhkh]hg")                                         # «بانادال»
        self.assertEqual(ids[0], self.panadol)
        self.assertEqual((hint["kind"], hint.get("guess")), ("layout", True))

    def test_a_close_guess_as_typed_is_kept_without_a_second_search(self):
        with mock.patch.object(matching, "fuzzy_candidates", wraps=matching.fuzzy_candidates) as asked:
            ids, hint = self.find("Amoxel 500mg 16 cap")
        self.assertEqual((ids[0], hint), (self.amoxil, {"kind": "fuzzy"}))
        self.assertEqual(asked.call_count, 1)

    def test_nothing_found_either_way(self):
        self.assertEqual(self.find("qqqqzzzz"), ([], None))
        self.assertEqual(self.find("ضضضضصصصص"), ([], None))

    def test_every_mapped_answer_carries_a_hint(self):
        # the sale screen adds a single result on Enter only when there is no hint
        for q in ("شةخ", "fhkh],g", "شةخءثم", "لاقعبثى"):
            self.assertIsNotNone(self.find(q)[1], q)

    def test_a_category_filter_and_the_hidden_items_rule_still_apply(self):
        self.assertEqual(self.find("شةخ", category="none"), ([self.amoxil], {"kind": "layout", "query": "amo"}))
        cat.update_item(self.db, self.ctx, self.amoxil, {"hidden": True})
        self.assertEqual(self.find("شةخ")[0], [self.amoxil])                     # the owner sees hidden items
        with mock.patch.object(type(self.ctx), "can", lambda ctx, perm: perm != "items.hidden"):
            self.assertEqual(self.find("شةخ"), ([], {"kind": "layout", "query": "amo"}))


if __name__ == "__main__":
    import unittest
    unittest.main()
