"""The name index (purchase matching, look-alike names at the sale screen) is rebuilt after any item change.
Parsing every item's name again was the whole cost: 0.3-0.4 s for 4,000 items and 1.5 s for 15,000, paid by
the first safety check after a purchase approval or an item edit (#304 B4, measured again on 19/11). The
parsed names of items that did not change are kept, so a rebuild parses only what changed. Same index as a
build from nothing. Synthetic data only."""
from unittest import mock

from app.catalog import matching, service as cat
from tests.helpers import DBTestCase


class TestNameIndexReuse(DBTestCase):
    def setUp(self):
        super().setUp()
        matching.forget(self.ctx.org_id)
        self.ids = [cat.create_item(self.db, self.ctx, {"name_en": f"Brandix{i} {50 * (i + 1)}mg 20 tab", "public_price": 1000},
                                    force=True) for i in range(6)]

    def parses(self, fn):
        with mock.patch.object(matching, "parse_name", wraps=matching.parse_name) as p:
            out = fn()
        return p.call_count, out

    def shape(self, idx):
        return ({k: sorted(v) for k, v in idx.by_key.items()},
                {i: (it.label, it.strength, it.form, it.content, it.pack_count, sorted(it.modifiers),
                     [p.__dict__ for p in it.parsed]) for i, it in idx.items.items()})

    def test_a_rebuild_parses_only_the_item_that_changed(self):
        first, _ = self.parses(lambda: matching._index(self.db, self.ctx.org_id))
        self.assertGreaterEqual(first, 6)
        cat.update_item(self.db, self.ctx, self.ids[0], {"name_en": "Renamed 75mg 20 tab"})      # invalidates
        again, idx = self.parses(lambda: matching._index(self.db, self.ctx.org_id))
        self.assertLessEqual(again, 2)
        self.assertEqual(idx.items[self.ids[0]].label, "Renamed 75mg 20 tab")

    def test_a_change_that_is_not_a_name_parses_nothing(self):
        matching._index(self.db, self.ctx.org_id)
        cat.update_item(self.db, self.ctx, self.ids[1], {"public_price": 1500})
        again, _ = self.parses(lambda: matching._index(self.db, self.ctx.org_id))
        self.assertEqual(again, 0)

    def test_the_reused_index_equals_one_built_from_nothing(self):
        matching._index(self.db, self.ctx.org_id)
        cat.update_item(self.db, self.ctx, self.ids[2], {"name_en": "Otherol 10mg 30 cap", "strength": "10 mg"})
        cat.create_item(self.db, self.ctx, {"name_en": "Newcomer 5mg 10 tab", "public_price": 500}, force=True)
        reused = self.shape(matching._index(self.db, self.ctx.org_id))
        matching.forget(self.ctx.org_id)
        fresh = self.shape(matching._index(self.db, self.ctx.org_id))
        self.assertEqual(reused, fresh)

    def test_an_item_that_stopped_leaves_the_index_and_the_memory(self):
        matching._index(self.db, self.ctx.org_id)
        with self.db.transaction():
            self.db.execute("UPDATE items SET status='inactive', updated_at='2031-01-01T00:00:00.000000Z'"
                            " WHERE id=? AND org_id=?", (self.ids[3], self.ctx.org_id))
        matching.invalidate(self.ctx.org_id)
        idx = matching._index(self.db, self.ctx.org_id)
        self.assertNotIn(self.ids[3], idx.items)
        self.assertNotIn(self.ids[3], matching._parsed[self.ctx.org_id])

    def test_answers_follow_the_rebuilt_index(self):
        before = [c.item_id for c in matching.fuzzy_candidates(self.db, self.ctx.org_id, "Brandix4 250mg 20 tab", 3)]
        self.assertEqual(before[0], self.ids[4])
        cat.update_item(self.db, self.ctx, self.ids[4], {"name_en": "Zzzolam 1mg 10 tab"})
        after = [c.item_id for c in matching.fuzzy_candidates(self.db, self.ctx.org_id, "Zzzolam 1mg 10 tab", 3)]
        self.assertEqual(after[0], self.ids[4])

    def test_one_pharmacys_parsed_names_are_not_used_for_another(self):
        matching._index(self.db, self.ctx.org_id)
        _, other = self.second_tenant()
        cat.create_item(self.db, other, {"name_en": "Brandix0 50mg 20 tab", "public_price": 1000}, force=True)
        idx = matching._index(self.db, other.org_id)
        self.assertEqual(len(idx.items), 1)
        self.assertNotIn(self.ids[0], idx.items)


if __name__ == "__main__":
    import unittest
    unittest.main()
