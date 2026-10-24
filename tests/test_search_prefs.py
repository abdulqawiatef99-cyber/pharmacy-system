"""Search by English/Arabic name, pieces, %, wrong keyboard language; per-person screen settings."""
from app.catalog import service as cat
from app.catalog.search import backfill_search_keys, search, swap_layout
from app.core import people
from app.core.errors import ValidationFailed
from tests.helpers import DBTestCase
from tests.test_api import APICase, H


class TestSearch(DBTestCase):
    def setUp(self):
        super().setUp()
        self.asp = cat.create_item(self.db, self.ctx, {"name_en": "ASPOCID 75MG 30TAB", "name_ar": "اسبوسيد 75 مجم 30 قرص",
                                                       "public_price": 3300})
        self.inf = cat.create_item(self.db, self.ctx, {"name_en": "ASPOCID INF 75mg 30TAB", "name_ar": "اسبوسيد أطفال",
                                                       "public_price": 3500})
        self.cetal = cat.create_item(self.db, self.ctx, {"name_en": "CETAL 500mg 20TAB", "name_ar": "سيتال 500 مجم",
                                                         "code": "4411", "public_price": 2000}, barcodes=["6221025012345"])

    def names(self, q):
        return [i["name_en"] for i in search(self.db, self.ctx, q)["items"]]

    def test_english_arabic_pieces_code_barcode(self):
        self.assertEqual(self.names("asp 75")[0], "ASPOCID 75MG 30TAB")
        self.assertEqual(set(self.names("اسبوسيد")), {"ASPOCID 75MG 30TAB", "ASPOCID INF 75mg 30TAB"})
        self.assertEqual(set(self.names("اسبو 75")), {"ASPOCID 75MG 30TAB", "ASPOCID INF 75mg 30TAB"})
        self.assertEqual(self.names("اسبوسيد اطفال"), ["ASPOCID INF 75mg 30TAB"])   # أ/ا ignored
        self.assertEqual(self.names("4411"), ["CETAL 500mg 20TAB"])
        self.assertEqual(self.names("6221025012345"), ["CETAL 500mg 20TAB"])

    def test_wildcard(self):
        self.assertEqual(set(self.names("asp%cid")), {"ASPOCID 75MG 30TAB", "ASPOCID INF 75mg 30TAB"})
        self.assertEqual(self.names("c%tal"), ["CETAL 500mg 20TAB"])

    def test_wrong_keyboard_language(self):
        self.assertEqual(swap_layout("شسحخؤهي"), "aspocid")
        self.assertEqual(swap_layout("hsf,sd]"), "اسبوسيد")
        self.assertIsNone(swap_layout("asp اسب"))
        r = search(self.db, self.ctx, "شسحخؤهي")
        self.assertEqual(r["hint"], {"kind": "layout", "query": "aspocid"})
        self.assertEqual(len(r["items"]), 2)
        self.assertEqual(search(self.db, self.ctx, "hsf,sd]")["hint"]["kind"], "layout")
        self.assertIsNone(search(self.db, self.ctx, "aspocid")["hint"])

    def test_backfill_and_arabic_rename(self):
        with self.db.transaction():
            self.db.execute("UPDATE items SET name_ar_norm=NULL")
        self.assertNotEqual(search(self.db, self.ctx, "سيتال")["hint"], None)   # only a fuzzy guess before
        self.assertEqual(backfill_search_keys(self.db), 3)
        self.assertEqual(self.names("سيتال"), ["CETAL 500mg 20TAB"])
        cat.update_item(self.db, self.ctx, self.cetal, {"name_ar": "ستال"})
        self.assertEqual(self.names("ستال"), ["CETAL 500mg 20TAB"])


class TestPrefs(DBTestCase):
    def test_defaults_validation_contrast_and_reset(self):
        self.assertEqual(people.get_prefs(self.db, self.ctx.user_id), people.PREF_DEFAULTS)
        p = people.set_prefs(self.db, self.ctx, {"theme": "dark", "font": "l", "names": "ar", "accent": "#1f4e8c"})
        self.assertEqual((p["theme"], p["font"], p["names"], p["accent"]), ("dark", "l", "ar", "#1F4E8C"))
        with self.assertRaises(ValidationFailed) as e:
            people.set_prefs(self.db, self.ctx, {"accent": "#8FD3F4"})     # light blue: white text unreadable
        self.assertEqual(e.exception.code, "low_contrast")
        for bad in ({"theme": "pink"}, {"font": "xxl"}, {"accent": "red"}):
            with self.assertRaises(ValidationFailed):
                people.set_prefs(self.db, self.ctx, bad)
        self.assertEqual(people.set_prefs(self.db, self.ctx, {"reset": True}), people.PREF_DEFAULTS)


class TestSearchAPI(APICase):
    def test_meta_hint_and_prefs_in_me(self):
        self.http_login()
        r = self.c.get("/api/items", params={"q": "زشىشيخم", "meta": "1"}).json()   # "panadol" on the Arabic layout
        self.assertEqual(r["hint"]["kind"], "layout")
        self.assertEqual(r["items"][0]["name_en"], "Panadol 500mg 24 tab")
        self.assertIsInstance(self.c.get("/api/items", params={"q": "pan"}).json(), list)   # old clients unchanged
        r = self.c.put("/api/me/prefs", json={"font": "xl"}, headers=H)
        self.assertEqual(r.json()["font"], "xl")
        self.assertEqual(self.c.get("/api/me").json()["prefs"]["font"], "xl")
