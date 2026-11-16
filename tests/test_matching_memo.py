"""Look-alike search is asked for the same item names on every sale-screen safety check. The answer is kept
per catalog version: same answer, worked out once, forgotten as soon as any item changes."""
from unittest import mock

from app.catalog import matching, service as cat
from tests.helpers import DBTestCase


class MatchingMemo(DBTestCase):
    def setUp(self):
        super().setUp()
        matching.invalidate(self.ctx.org_id)
        self.a = cat.create_item(self.db, self.ctx, {"name_en": "Memotest 500 mg 20 tab", "pack_size": 1, "public_price": 1000})
        self.b = cat.create_item(self.db, self.ctx, {"name_en": "Memotest 250 mg 20 tab", "pack_size": 1, "public_price": 900})

    def ask(self):
        return matching.fuzzy_candidates(self.db, self.ctx.org_id, "Memotest 500 mg 20 tab", 6)

    def test_second_ask_is_the_same_answer_without_scoring_again(self):
        first = self.ask()
        self.assertEqual({c.item_id for c in first}, {self.a, self.b})
        with mock.patch.object(matching, "score_item", side_effect=AssertionError("scored again")):
            second = self.ask()
        self.assertEqual([(c.item_id, c.label, c.score, c.reasons) for c in second],
                         [(c.item_id, c.label, c.score, c.reasons) for c in first])

    def test_a_caller_cannot_change_the_remembered_answer(self):
        first = self.ask()
        first[0].reasons.append("changed_by_caller")
        first.pop()
        again = self.ask()
        self.assertEqual(len(again), 2)
        self.assertNotIn("changed_by_caller", again[0].reasons)

    def test_a_new_item_is_seen_at_once(self):
        self.ask()
        c = cat.create_item(self.db, self.ctx, {"name_en": "Memotest 1000 mg 20 tab", "pack_size": 1, "public_price": 1500})
        self.assertIn(c, {x.item_id for x in self.ask()})

    def test_an_edited_item_is_seen_at_once(self):
        self.ask()
        cat.update_item(self.db, self.ctx, self.b, {"name_en": "Otherthing 250 mg 20 tab"})
        self.assertNotIn(self.b, {x.item_id for x in self.ask()})

    def test_the_memory_is_bounded(self):
        with mock.patch.object(matching, "MEMO_MAX", 3):
            for i in range(10):
                matching.fuzzy_candidates(self.db, self.ctx.org_id, f"Memotest {i} mg", 6)
            self.assertLessEqual(len(matching._index(self.db, self.ctx.org_id).memo), 3)
