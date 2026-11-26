"""Independent review of the #461 search-speed change (26/11): four ways the shortlist and the in-place index
update could give a wrong purchase match or break a search. Each case failed before its fix.
Synthetic data only."""
import random
import sys
import threading

from app.catalog import matching, service as cat
from app.catalog.normalize import parse_name
from tests.helpers import DBTestCase
from tests.test_search_speed_461 import add_items, shape


class ReviewCase(DBTestCase):
    def setUp(self):
        super().setUp()
        matching.forget(self.ctx.org_id)
        self.addCleanup(matching.forget, self.ctx.org_id)

    def full(self, name, limit=5):
        idx = matching._index(self.db, self.ctx.org_id)
        q = parse_name(name)
        scored = sorted((matching.score_item(q, idx.items[i]) for i in idx.candidates(q)),
                        key=lambda c: c.score, reverse=True)
        return [c for c in scored if c.score >= matching.SUGGEST_MIN * 0.6][:limit]

    def full_status(self, name):
        cands = self.full(name)
        if not cands or cands[0].score < matching.SUGGEST_MIN:
            return "unmatched"
        second = cands[1].score if len(cands) > 1 else 0.0
        if cands[0].score >= matching.AUTO_FUZZY_MIN and not cands[0].reasons and cands[0].score - second >= 0.05:
            return "auto"
        return "suggested"

    def elsewhere(self, fn):
        """Run on another server thread (its own connection)."""
        out = []
        t = threading.Thread(target=lambda: out.append(fn()))
        t.start()
        t.join()
        return out[0]


class TestNameInTheOtherScript(ReviewCase):
    """A candidate that matches only through the consonant skeleton (an Arabic name against an English
    query) shares no letters with the query: it must not be cut by same-script names that share one word."""

    def test_a_close_second_in_arabic_keeps_the_match_for_a_person(self):
        rng = random.Random(3)
        decoys = [("Vitamin Forton " + "".join(rng.choice("bcdfgklmnprst") + rng.choice("aeiou")
                                               for _ in range(rng.choice((2, 3)))) + " 500mg 20 tab", "")
                  for _ in range(300)]
        add_items(self.db, self.ctx.org_id, [("Betalorin Forton Vitamin 500mg 20 tab", ""),
                                             ("", "بيتالورين فورتون فيتامين 500 مجم 20 قرص")] + decoys)
        q = "Betalorine Forton Vitamin 500mg 20 tab"
        self.assertEqual(self.full_status(q), "suggested")              # two items within 0.05: a person decides
        r = matching.match_line(self.db, self.ctx.org_id, raw_name=q)
        self.assertEqual((r.status, r.warnings), ("suggested", ["ambiguous"]))
        self.assertEqual([c.score for c in r.candidates[:2]], [c.score for c in self.full(q)[:2]])

    def test_the_only_real_match_is_in_the_other_script(self):
        letters = "بتثجحخدذرزسشصضطظعغفقكلمن"
        for tail_ar, tail_en in ((" 500 مجم 20 قرص", " 500mg 20 tab"), ("", "")):
            with self.subTest(tail=tail_en):
                matching.forget(self.ctx.org_id)
                with self.db.transaction():
                    self.db.execute("DELETE FROM items WHERE org_id=?", (self.ctx.org_id,))
                rng = random.Random(1)
                ids = add_items(self.db, self.ctx.org_id, [("Vitamin Zorbex" + tail_en, "")] + [
                    ("", "فيتامين " + "".join(rng.choice(letters) for _ in range(rng.choice((2, 3, 4)))) + tail_ar)
                    for _ in range(300)], "A")
                got = matching.fuzzy_candidates(self.db, self.ctx.org_id, "فيتامين زوربكس" + tail_ar, 5)
                self.assertEqual((got[0].item_id, got[0].score), (ids[0], self.full("فيتامين زوربكس" + tail_ar)[0].score))
                # and the mirror: an English line against an item that has only its Arabic name
                with self.db.transaction():
                    self.db.execute("DELETE FROM items WHERE org_id=?", (self.ctx.org_id,))
                rng = random.Random(2)
                ids = add_items(self.db, self.ctx.org_id, [("", "فيتامين زوربكس" + tail_ar)] + [
                    ("Vitamin " + "".join(rng.choice("bcdfgklmnprst") + rng.choice("aeiou")
                                          for _ in range(rng.choice((1, 2)))) + tail_en, "") for _ in range(300)], "B")
                matching.invalidate(self.ctx.org_id)
                got = matching.fuzzy_candidates(self.db, self.ctx.org_id, "Vitamin Zorbex" + tail_en, 5)
                self.assertEqual((got[0].item_id, got[0].score), (ids[0], self.full("Vitamin Zorbex" + tail_en)[0].score))


class TestIndexAndTransactions(ReviewCase):
    def setUp(self):
        super().setUp()
        self.ids = [cat.create_item(self.db, self.ctx, {"name_en": f"Brandix{i} {50 * (i + 1)}mg 20 tab",
                                                        "public_price": 1000}, force=True) for i in range(6)]
        matching._index(self.db, self.ctx.org_id)

    def status(self, name):
        r = matching.match_line(self.db, self.ctx.org_id, raw_name=name)
        return r.status, r.item_id

    def test_a_rolled_back_item_write_never_reaches_a_purchase_match(self):
        # a new item from an invoice line and a rename, inside a bigger operation that fails
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                cat.create_item(self.db, self.ctx, {"name_en": "Phantomol 5mg 10 tab", "public_price": 500}, force=True)
                cat.update_item(self.db, self.ctx, self.ids[0], {"name_en": "Renamedol 50mg 20 tab"})
                raise RuntimeError("the bigger operation failed")
        for where in (lambda fn: fn(), self.elsewhere):                 # the writing thread, then another one
            self.assertEqual(where(lambda: self.status("Phantomol 5mg 10 tab"))[0], "unmatched")
            self.assertEqual(where(lambda: self.status("Renamedol 50mg 20 tab"))[0], "unmatched")
            self.assertEqual(where(lambda: self.status("Brandix0 50mg 20 tab")), ("auto", self.ids[0]))
        cat.update_item(self.db, self.ctx, self.ids[3], {"public_price": 1700})      # any later ordinary edit
        self.assertEqual(self.elsewhere(lambda: self.status("Phantomol 5mg 10 tab"))[0], "unmatched")
        self.assertEqual(self.elsewhere(lambda: self.status("Brandix0 50mg 20 tab")), ("auto", self.ids[0]))
        matching.forget(self.ctx.org_id)
        fresh = shape(matching._index(self.db, self.ctx.org_id))
        self.assertEqual(self.elsewhere(lambda: shape(matching._index(self.db, self.ctx.org_id))), fresh)

    def test_an_item_written_inside_a_bigger_operation_is_seen_once_it_commits(self):
        with self.db.transaction():
            new = cat.create_item(self.db, self.ctx, {"name_en": "Latecomer 5mg 10 tab", "public_price": 500}, force=True)
            self.assertEqual(self.elsewhere(lambda: self.status("Latecomer 5mg 10 tab"))[0], "unmatched")
        self.assertEqual(self.elsewhere(lambda: self.status("Latecomer 5mg 10 tab")), ("auto", new))
        self.assertEqual(self.status("Latecomer 5mg 10 tab"), ("auto", new))

    def test_a_silent_change_followed_by_an_ordinary_edit_is_not_lost(self):
        # a writer that told nobody (bulk SQL), then an edit of another item before any search
        with self.db.transaction():
            self.db.execute("UPDATE items SET name_en='Quietly 1mg 10 tab', name_norm='quietly 1 mg 10 tab', strength_norm='1mg',"
                            " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (self.ids[5],))
        cat.update_item(self.db, self.ctx, self.ids[1], {"public_price": 1900})
        self.assertEqual(self.status("Quietly 1mg 10 tab"), ("auto", self.ids[5]))
        self.assertEqual(self.elsewhere(lambda: self.status("Quietly 1mg 10 tab")), ("auto", self.ids[5]))

    def test_the_version_follows_a_rollback_on_the_same_connection(self):
        before = matching._version(self.db, self.ctx.org_id)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.execute("UPDATE items SET status='inactive' WHERE id=?", (self.ids[2],))
                self.assertNotEqual(matching._version(self.db, self.ctx.org_id), before)
                raise RuntimeError
        self.assertEqual(matching._version(self.db, self.ctx.org_id), before)

    def test_the_followed_version_is_the_real_one(self):
        cat.update_item(self.db, self.ctx, self.ids[2], {"name_en": "Otherol 10mg 30 cap"})
        cat.create_item(self.db, self.ctx, {"name_en": "Newcomer 5mg 10 tab", "public_price": 500}, force=True)
        kept = matching._cache[self.ctx.org_id][0]
        matching._seen.clear()
        self.assertEqual(kept, matching._version(self.db, self.ctx.org_id))


class TestSearchWhileItemsChange(ReviewCase):
    def test_searches_survive_item_edits_on_another_thread(self):
        rng = random.Random(5)
        words = ("Alphaword", "Betaword", "Gammaword", "Deltaword")
        names = [(f"{w} " + "".join(rng.choice("bcdfgklmnprst") + rng.choice("aeiou") for _ in range(3))
                  + f" {n} 10mg 20 tab", "") for w in words for n in range(450)]
        ids = add_items(self.db, self.ctx.org_id, names)
        matching.invalidate(self.ctx.org_id)
        matching._index(self.db, self.ctx.org_id)
        errors, done, stop = [], [0], threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    matching.fuzzy_candidates(self.db, self.ctx.org_id, f"Alphaword Betaword Gammaword Deltaword {done[0]}", 5)
                    done[0] += 1
                except Exception as e:      # noqa: BLE001
                    errors.append(repr(e))

        def writer():
            for k in range(150):
                try:
                    cat.update_item(self.db, self.ctx, ids[k * 7 % len(ids)], {"name_en": f"{words[k % 4]} Changed{k} 10mg 20 tab"})
                except Exception as e:      # noqa: BLE001
                    errors.append(repr(e))
            stop.set()
        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)
        try:
            threads = [threading.Thread(target=reader) for _ in range(2)] + [threading.Thread(target=writer)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(120)
        finally:
            sys.setswitchinterval(old)
            stop.set()
        self.assertEqual(errors[:3], [])
        self.assertGreater(done[0], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
