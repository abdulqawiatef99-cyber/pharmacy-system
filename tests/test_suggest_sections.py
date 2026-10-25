"""Section suggestions from names: only a suggestion, nothing filed by itself."""
from app.catalog import card, service as cat, suggest_sections as sg
from tests.helpers import DBTestCase


class TestSuggest(DBTestCase):
    def test_rules(self):
        card.ensure_default_categories(self.db)
        mk = lambda en, ar=None: cat.create_item(self.db, self.ctx, {"name_en": en, "name_ar": ar, "public_price": 100})  # noqa: E731
        ids = {"ins": mk("LANTUS SOLOSTAR 100IU PEN"), "amp": mk("VOLTAREN 75MG 5 AMP"), "milk": mk("BEBELAC 1 400GM"),
               "sh": mk("ANTI DANDRUFF SHAMPOO 200ML"), "tab": mk("PANADOL 500MG 24 TAB"), "imod": mk("IMODIUM 2MG"),
               "x": mk("ZZZ"), "ar": mk(None, "سرنجة 5 سم")}
        r = sg.suggest(self.db, self.ctx)
        where = {it["id"]: g["category"] for g in r["groups"] for it in g["items"]}
        self.assertEqual(where[ids["ins"]], "ثلاجة")
        self.assertEqual(where[ids["amp"]], "حقن")
        self.assertEqual(where[ids["milk"]], "ألبان وأغذية أطفال")
        self.assertEqual(where[ids["sh"]], "عناية واستعمال شخصي")
        self.assertEqual(where[ids["tab"]], "أدوية")
        self.assertEqual(where[ids["imod"]], "أدوية")          # "IM" inside IMODIUM is not "IM" injection
        self.assertEqual(where[ids["ar"]], "مستلزمات طبية")
        self.assertNotIn(ids["x"], where)
        self.assertEqual(r["unmatched"], 1)
        self.assertIsNone(self.db.one("SELECT category_id FROM items WHERE id=?", (ids["amp"],))["category_id"])   # nothing filed
