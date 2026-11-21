"""Item search order: the name typed in full is the first row (Enter on the sale screen adds the first row).
Found on the trial-day rehearsal 17/11: «صنف تجربة ب» typed in full listed «صنف تجربة أ» first."""
from app.catalog import search, service as cat
from tests.helpers import DBTestCase


class ExactNameFirst(DBTestCase):
    def setUp(self):
        super().setUp()
        self.a = cat.create_item(self.db, self.ctx, {"name_en": "Trial A", "name_ar": "صنف تجربة أ", "pack_size": 1, "public_price": 1000})
        self.b = cat.create_item(self.db, self.ctx, {"name_en": "Trial B item", "name_ar": "صنف تجربة ب", "pack_size": 1, "public_price": 1000})
        self.c = cat.create_item(self.db, self.ctx, {"name_en": "Trial B item forte", "name_ar": "صنف تجربة ب فورت", "pack_size": 1, "public_price": 1000})

    def ids(self, q):
        return [i["id"] for i in search.search(self.db, self.ctx, q, 8)["items"]]

    def test_full_arabic_name_is_first(self):
        self.assertEqual(self.ids("صنف تجربة ب")[0], self.b)          # was A: its name also holds the letter ب

    def test_full_english_name_is_first_then_names_starting_with_it(self):
        self.assertEqual(self.ids("trial b item")[:2], [self.b, self.c])

    def test_a_partial_name_still_lists_all(self):
        self.assertEqual(set(self.ids("صنف تجربة")), {self.a, self.b, self.c})
