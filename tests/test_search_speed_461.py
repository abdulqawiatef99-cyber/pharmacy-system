"""#461 QA-012: a misspelt name on the sale screen or in a purchase invoice took 1.3-1.8 s on a 30,000-item
catalog whose names start alike: every item was a candidate and each one was compared in full. Only a
shortlist is compared now, and one item change updates the name index in place instead of rebuilding it.
The answers must stay what a full comparison gives. Synthetic data only."""
import random
import threading
import time
from unittest import mock

from app.catalog import matching, search, service as cat
from app.catalog.normalize import normalize_text, parse_name
from app.db import Database, new_id
from scripts.perf.search_bench import catalog, typo
from tests.helpers import DBTestCase
from tests.test_owner_plan_priority_1_2 import PlanCase


def add_items(db, org_id, names, code="Z"):
    ids = [new_id() for _ in names]
    with db.transaction():
        db.conn.executemany(
            "INSERT INTO items (id, org_id, code, name_en, name_ar, name_norm, name_ar_norm,"
            " pack_size, public_price, status, dedupe_key) VALUES (?,?,?,?,?,?,?,1,1000,'active',?)",
            ((i, org_id, f"{code}{n}", en, ar, normalize_text(en), normalize_text(ar), i)
             for n, ((en, ar), i) in enumerate(zip(names, ids))))
    return ids


def shape(idx):
    return ({k: sorted(v) for k, v in idx.by_key.items()}, {k: sorted(v) for k, v in idx.by_gram.items()},
            {k: sorted(v) for k, v in idx.by_size.items()}, {k: sorted(v) for k, v in idx.by_attr.items()},
            {i: (it.label, it.strength, it.form, it.pack_count, it.grams) for i, it in idx.items.items()})


class TestShortlistKeepsTheAnswers(DBTestCase):
    """900 names that start with the same two words (every one a candidate) among 1,500 varied ones."""

    def setUp(self):
        super().setUp()
        matching.forget(self.ctx.org_id)
        self.addCleanup(matching.forget, self.ctx.org_id)
        rng = random.Random(7)
        self.alike = [(f"Zedmaker Line {w.split()[0]} {rng.choice(('5mg', '10mg', '500mg'))} {rng.choice((10, 20, 30))}"
                       f" {rng.choice(('tab', 'cap', 'syrup'))}", "") for w, _ in catalog("varied", rng)[:900]]
        self.varied = catalog("varied", random.Random(8))[:1500]
        add_items(self.db, self.ctx.org_id, self.alike, "A")
        add_items(self.db, self.ctx.org_id, self.varied, "V")

    def full(self, name, limit):
        """What comparing every candidate gives (the behaviour before the shortlist)."""
        idx = matching._index(self.db, self.ctx.org_id)
        q = parse_name(name)
        scored = sorted((matching.score_item(q, idx.items[i]) for i in idx.candidates(q)),
                        key=lambda c: c.score, reverse=True)
        return [c for c in scored if c.score >= matching.SUGGEST_MIN * 0.6][:limit]

    def test_same_best_answers_as_a_full_comparison(self):
        rng = random.Random(9)
        asked = []
        for en, _ in rng.sample(self.alike, 12):
            w = en.split()
            asked += [en, f"Zedmaker Line {typo(w[2], rng)} {' '.join(w[3:])}", f"Zedmaker {typo(w[2], rng)}"]
        for en, ar in rng.sample(self.varied, 12):
            asked += [en, typo(en.split()[0], rng) + " " + " ".join(en.split()[1:]), ar]
        self.assertGreater(len(matching._index(self.db, self.ctx.org_id).candidates(parse_name(asked[1]))), 900)
        for name in asked:
            for limit in (5, 60):
                with self.subTest(name=name, limit=limit):
                    got = matching.fuzzy_candidates(self.db, self.ctx.org_id, name, limit)
                    want = self.full(name, limit)
                    # the best answer is the one a full comparison gives; below it a guess may be another
                    # name of nearly the same score (measured on 640 questions: first always equal, the
                    # first three equal in 99.7%, never more than 0.05 lower in the first ten)
                    self.assertGreaterEqual(len(got), min(len(want), 3))
                    if want:
                        self.assertEqual(got[0].score, want[0].score)
                        self.assertIn(got[0].item_id, [c.item_id for c in want if c.score == want[0].score])
                    for a, b in zip(got[:10], want[:10]):
                        self.assertGreaterEqual(a.score, b.score - 0.05)

    def test_only_a_shortlist_is_compared(self):
        with mock.patch.object(matching, "score_item", wraps=matching.score_item) as scored:
            matching.fuzzy_candidates(self.db, self.ctx.org_id, "Zedmaker Line Qqqq 10mg 20 tab", 5)
        self.assertLessEqual(scored.call_count, matching.SHORTLIST_MAX)

    def test_the_right_strength_of_one_name_is_never_left_out(self):
        # 450 items with one name and different strengths: all equal by name, the asked strength must be found
        same = [(f"Samename {s}mg 20 tab", "") for s in range(1, 451)]
        ids = add_items(self.db, self.ctx.org_id, same, "S")
        matching.invalidate(self.ctx.org_id)
        got = matching.fuzzy_candidates(self.db, self.ctx.org_id, "Samename 317mg 20 tab", 5)
        self.assertEqual(got[0].item_id, ids[316])
        self.assertEqual(got[0].score, 1.0)

    def test_sale_screen_guesses_are_capped_and_read_in_one_query(self):
        with mock.patch.object(matching, "fuzzy_candidates", wraps=matching.fuzzy_candidates) as asked:
            r = search.search(self.db, self.ctx, "Zedmakr Lien", 500)
        self.assertEqual(r["hint"], {"kind": "fuzzy"})
        self.assertEqual((asked.call_args.args[-1], asked.call_args.kwargs), (search.FUZZY_MAX, {"compare": search.FUZZY_MAX}))
        self.assertTrue(0 < len(r["items"]) <= search.FUZZY_MAX)
        # every guess that was compared reaches the caller's stock filter, not only the first few (Codex on #483)
        self.assertGreater(len(r["items"]), 100)
        self.assertEqual(len(search.search(self.db, self.ctx, "Zedmakr Lien", 20)["items"]), 20)
        self.assertTrue(all(i["org_id"] == self.ctx.org_id for i in r["items"]))


class TestIndexFollowsOneItem(DBTestCase):
    def setUp(self):
        super().setUp()
        matching.forget(self.ctx.org_id)
        self.addCleanup(matching.forget, self.ctx.org_id)
        self.ids = [cat.create_item(self.db, self.ctx, {"name_en": f"Brandix{i} {50 * (i + 1)}mg 20 tab",
                                                        "name_ar": f"برانديكس{i} 20 قرص", "public_price": 1000},
                                    force=True) for i in range(6)]
        self.idx = matching._index(self.db, self.ctx.org_id)

    def first(self, name):
        got = matching.fuzzy_candidates(self.db, self.ctx.org_id, name, 3)
        return got[0].item_id if got else None

    def test_an_edit_and_a_new_item_do_not_rebuild_the_index(self):
        with mock.patch.object(matching, "_OrgIndex", side_effect=AssertionError("index built again")):
            cat.update_item(self.db, self.ctx, self.ids[0], {"name_en": "Renamed 75mg 20 tab"})
            new = cat.create_item(self.db, self.ctx, {"name_en": "Newcomer 5mg 10 tab", "public_price": 500}, force=True)
            cat.update_item(self.db, self.ctx, self.ids[1], {"public_price": 1500})
            self.assertIs(matching._index(self.db, self.ctx.org_id), self.idx)
            self.assertEqual(self.first("Renamed 75mg 20 tab"), self.ids[0])
            self.assertEqual(self.first("Newcomer 5mg 10 tab"), new)
            self.assertEqual(self.idx.items[self.ids[0]].label, "Renamed 75mg 20 tab")

    def test_the_followed_index_equals_one_built_from_nothing(self):
        cat.update_item(self.db, self.ctx, self.ids[2], {"name_en": "Otherol 10mg 30 cap", "strength": "10 mg"})
        cat.update_item(self.db, self.ctx, self.ids[3], {"name_ar": "اسم تاني 20 قرص"})
        cat.create_item(self.db, self.ctx, {"name_en": "Newcomer 5mg 10 tab", "public_price": 500}, force=True)
        followed = shape(matching._index(self.db, self.ctx.org_id))
        matching.forget(self.ctx.org_id)
        self.assertEqual(followed, shape(matching._index(self.db, self.ctx.org_id)))

    def test_a_stopped_item_or_one_that_is_gone_drops_the_index(self):
        with self.db.transaction():
            self.db.execute("UPDATE items SET status='inactive' WHERE id=? AND org_id=?", (self.ids[4], self.ctx.org_id))
        matching.item_changed(self.db, self.ctx.org_id, self.ids[4], matching._cache[self.ctx.org_id][0])
        self.assertNotIn(self.ctx.org_id, matching._cache)
        idx = matching._index(self.db, self.ctx.org_id)
        self.assertNotIn(self.ids[4], idx.items)
        self.assertNotIn(self.ids[4], matching._parsed[self.ctx.org_id])
        matching.item_changed(self.db, self.ctx.org_id, "no-such-item", matching._cache[self.ctx.org_id][0])
        self.assertNotIn(self.ctx.org_id, matching._cache)

    def test_another_pharmacys_item_never_enters_this_index(self):
        _, other = self.second_tenant()
        theirs = cat.create_item(self.db, other, {"name_en": "Brandix0 50mg 20 tab", "public_price": 1000}, force=True)
        matching.item_changed(self.db, self.ctx.org_id, theirs, matching._cache[self.ctx.org_id][0])   # wrong pharmacy
        self.assertNotIn(theirs, matching._index(self.db, self.ctx.org_id).items)
        self.assertEqual(self.first("Brandix0 50mg 20 tab"), self.ids[0])

    def test_a_change_without_a_notice_is_still_seen(self):
        # this connection writes without telling the index (a bulk import), then another connection does
        self.first("Brandix5 300mg 20 tab")
        with self.db.transaction():
            self.db.execute("UPDATE items SET name_en='Quietly 1mg 10 tab', name_norm='quietly 1 mg 10 tab',"
                            " updated_at='2031-01-01T00:00:00.000000Z' WHERE id=?", (self.ids[5],))
        self.assertEqual(self.first("Quietly 1mg 10 tab"), self.ids[5])

        def elsewhere():
            other = Database(self.db.path)
            with other.transaction():
                other.execute("UPDATE items SET name_en='Elsewhere 2mg 10 tab', name_norm='elsewhere 2 mg 10 tab',"
                              " updated_at='2032-01-01T00:00:00.000000Z' WHERE id=?", (self.ids[5],))
            other.close()
        t = threading.Thread(target=elsewhere)
        t.start()
        t.join()
        self.assertEqual(self.first("Elsewhere 2mg 10 tab"), self.ids[5])

    def test_a_built_index_is_kept_out_of_garbage_collection_passes(self):
        import gc
        gc.unfreeze()
        matching.forget(self.ctx.org_id)
        matching._index(self.db, self.ctx.org_id)
        self.assertGreater(gc.get_freeze_count(), 0)
        gc.unfreeze()

    def test_the_version_is_not_read_again_while_nothing_changed(self):
        self.first("Brandix1 100mg 20 tab")
        with mock.patch.object(self.db, "one", wraps=self.db.one) as one:
            for i in range(5):
                self.first(f"Brandix{i} 100mg 20 tab")
        self.assertEqual([c for c in one.call_args_list if "max(updated_at)" in c.args[0]], [])

    def test_an_answer_worked_out_across_a_change_is_not_remembered(self):
        real = matching._shortlist

        def change_meanwhile(idx, q, ids, keep):
            out = real(idx, q, ids, keep)
            cat.update_item(self.db, self.ctx, self.ids[0], {"name_en": "Changed 75mg 20 tab"})
            return out
        with mock.patch.object(matching, "_shortlist", side_effect=change_meanwhile):
            stale = matching.fuzzy_candidates(self.db, self.ctx.org_id, "Brandix0 50mg 20 tab", 3)
        self.assertEqual(stale[0].label, "Brandix0 50mg 20 tab")            # worked out before the change
        fresh = matching.fuzzy_candidates(self.db, self.ctx.org_id, "Brandix0 50mg 20 tab", 3)
        self.assertNotIn("Brandix0 50mg 20 tab", [c.label for c in fresh])


class TestSearchAtCatalogScale(PlanCase):
    """30,000 names with one prefix. Bounds are several times the measured Linux numbers (docs/PERFORMANCE.md)
    and a fraction of what they were (1.3-1.8 s), so a slow CI machine passes and a return of the scan fails."""

    def setUp(self):
        super().setUp()
        self.addCleanup(matching.forget, self.ctx.org_id)
        add_items(self.db, self.ctx.org_id, catalog("prefix", random.Random(1)), "S")
        matching.forget(self.ctx.org_id)
        matching.prewarm(self.db)

    def ms(self, fn):
        t = time.perf_counter()
        out = fn()
        return (time.perf_counter() - t) * 1000, out

    def test_misspelt_names_on_the_sale_screen_and_after_an_item_edit(self):
        rng = random.Random(2)
        slow = []
        for k in range(12):
            q = f"{typo('Panacatalog', rng)} Brand"
            took, r = self.ms(lambda: self.call("GET", "/api/items?stock=first&meta=1&q=" + q))
            self.assertEqual(r["hint"]["kind"], "fuzzy", q)
            self.assertTrue(r["items"])
            slow.append(took)
        self.assertLess(sorted(slow)[-2], 400, slow)
        cat.update_item(self.db, self.ctx, self.pan, {"public_price": 4100})
        took, r = self.ms(lambda: self.call("GET", "/api/items?stock=first&meta=1&q=Panacatalgo Brnad"))
        self.assertEqual(r["hint"]["kind"], "fuzzy")
        self.assertLess(took, 400)

    def test_a_typed_purchase_line_and_an_arabic_name(self):
        sup = self.call("POST", "/api/suppliers", 201, json={"name": "مورد صناعي"})["id"]
        inv = self.call("POST", "/api/purchases", 201, json={"supplier_id": sup, "invoice_no": "S-1"})
        inv_id = inv["id"] if "id" in inv else inv["invoice"]["id"]
        slow = []
        for n in (17, 4242, 29990):
            v = self.call("GET", f"/api/purchases/{inv_id}")["invoice"]["version"]
            took, r = self.ms(lambda: self.call("POST", f"/api/purchases/{inv_id}/lines", json={
                "line": {"raw_name": f"Panacatelog Brand {n} 10mg tab", "qty": 1}, "version": v}))
            line = r["lines"][-1]
            self.assertEqual(line["item"]["name_en"], f"Panacatalog Brand {n} 10mg tab")
            self.assertEqual(line["match_status"], "suggested")
            slow.append(took)
        self.assertLess(max(slow), 500, slow)
        took, r = self.ms(lambda: self.call("GET", "/api/items?stock=first&meta=1&q=صنف 4242"))
        self.assertEqual(r["items"][0]["name_ar"], "صنف صناعي 4242")
        self.assertLess(took, 400)


if __name__ == "__main__":
    import unittest
    unittest.main()
