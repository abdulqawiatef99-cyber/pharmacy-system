"""Independent review of the #461 search-speed change (26/11): four ways the shortlist and the in-place index
update could give a wrong purchase match or break a search. Each case failed before its fix.
Synthetic data only."""
import random
import sys
import threading
import time
import unittest

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


class TestNumbersInTheName(ReviewCase):
    """Codex P1 on #483: the shortlist ranked without score_item's name-number rule, so 200 names with another
    number (B1000..B1199) pushed out the one close alternative (Vitamins B12) and the line was matched alone."""

    def test_wrong_numbers_do_not_crowd_out_the_close_alternative(self):
        ids = add_items(self.db, self.ctx.org_id, [("Vitamin B12", ""), ("Vitamins B12", "")]
                        + [(f"Vitamin B{n}", "") for n in range(1000, 1200)])
        self.assertEqual(self.full_status("Vitamin B12"), "suggested")
        r = matching.match_line(self.db, self.ctx.org_id, raw_name="Vitamin B12")
        self.assertEqual((r.status, r.warnings, r.item_id), ("suggested", ["ambiguous"], ids[0]))
        self.assertEqual([c.score for c in r.candidates[:2]], [c.score for c in self.full("Vitamin B12")[:2]])

    def test_the_same_with_a_number_of_its_own_word_and_in_arabic(self):
        ids = add_items(self.db, self.ctx.org_id, [("Omega 3 Plus", ""), ("Omegas 3 Plus", ""), ("", "اوميجا 3 بلس")]
                        + [(f"Omega {n} Plus", "") for n in range(100, 400)])
        for line in ("Omega 3 Plus", "اوميجا 3 بلس"):
            want = self.full(line)
            got = matching.fuzzy_candidates(self.db, self.ctx.org_id, line, 5)
            self.assertEqual([c.score for c in got[:3]], [c.score for c in want[:3]], line)
            self.assertEqual(matching.match_line(self.db, self.ctx.org_id, raw_name=line).status, self.full_status(line))
        self.assertIn(ids[0], [c.item_id for c in matching.fuzzy_candidates(self.db, self.ctx.org_id, "Omega 3 Plus", 5)])


class TestNoCloseCandidateIsEverLeftOut(ReviewCase):
    """Codex P1 on #483 (second pass): the shortlist ranks by an approximation, so it can never prove that no
    close alternative was cut. A purchase match may only be automatic when that is proven: every candidate
    that can still reach the deciding score is compared in full (`sure_from`), using a real upper bound."""

    def test_a_dropped_spelling_twin_keeps_the_match_for_a_person(self):
        rng = random.Random(42)
        letters = "abcdefghijklmnopqrstuvwxyz"
        names = [("Paracetamol", ""), ("Paraceamol", "")] + [
            ("Paracetamol" + "".join(rng.sample(letters, 4)), "") for _ in range(200)]
        ids = add_items(self.db, self.ctx.org_id, list(dict.fromkeys(names)))
        self.assertEqual(self.full_status("Paracetamol"), "suggested")
        r = matching.match_line(self.db, self.ctx.org_id, raw_name="Paracetamol")
        self.assertEqual((r.status, r.warnings, r.item_id), ("suggested", ["ambiguous"], ids[0]))

    def test_an_import_still_stops_at_a_similar_item(self):
        # Codex P2 on #483: the import planner decides "create" from the same answer, and it asks without
        # naming a score: the proof is the default, only the guess-only search opts out
        from app.catalog.normalize import dedupe_key
        from app.dataimport.service import DataImport
        self.assertLessEqual(matching.SURE_FROM, 0.9)
        rng = random.Random(42)
        names = [("Paraceamol", "")] + [("Paracetamol" + "".join(rng.sample("bcdfghjklmnpqrstvwxz", 4)), "")
                                        for _ in range(200)]
        ids = add_items(self.db, self.ctx.org_id, list(dict.fromkeys(names)))
        key = dedupe_key("Paracetamol", None, None, 1)
        action, target, issues = DataImport(self.db)._plan_item(
            self.ctx, {"_name": "Paracetamol", "_dedupe": key, "_dedupe_base": key})
        self.assertEqual((action, target, [i["code"] for i in issues]), ("review", ids[0], ["similar_item_exists"]))
        dup = cat.find_duplicates(self.db, self.ctx, "Paracetamol")
        self.assertEqual([d["item_id"] for d in dup], [ids[0]])

    def test_every_candidate_above_the_deciding_score_is_found(self):
        rng = random.Random(11)
        cons, vow = "bcdfgklmnprstvz", "aeiou"
        word = lambda n: "".join(rng.choice(cons) + rng.choice(vow) for _ in range(n))     # noqa: E731
        tails = ("", " 500mg 20 tab", " 10mg 30 cap", " 120ml syrup", " Forte 20 tab", " B12 30 tab", " B6 30 tab")
        base = [word(rng.choice((2, 3))).capitalize() for _ in range(60)]
        names = set()
        while len(names) < 2500:                       # a few stems, many near-identical spellings and tails
            w = rng.choice(base)
            if rng.random() < 0.6:                     # one letter changed, dropped or doubled
                i = rng.randrange(1, len(w))
                w = rng.choice((w[:i] + rng.choice(cons + vow) + w[i + 1:], w[:i] + w[i + 1:], w[:i] + w[i] + w[i:]))
            names.add(f"Zedmaker {w}{rng.choice(tails)}" if rng.random() < 0.5 else f"{w}{rng.choice(tails)}")
        names = sorted(names)
        add_items(self.db, self.ctx.org_id, [(n, "") for n in names])
        idx = matching._index(self.db, self.ctx.org_id)
        sure, differ, truncated = matching.AUTO_FUZZY_MIN - 0.05, [], 0
        for line in rng.sample(names, 150):
            q = parse_name(line)
            ids = idx.candidates(q)
            truncated += len(ids) > matching.SHORTLIST_MIN
            want = sorted((c.item_id, c.score) for c in (matching.score_item(q, idx.items[i]) for i in ids) if c.score >= sure)
            got = sorted((c.item_id, c.score) for c in matching.fuzzy_candidates(
                self.db, self.ctx.org_id, line, 500, compare=matching.SHORTLIST_MIN, sure_from=sure) if c.score >= sure)
            if got != want:
                differ.append((line, len(want), len(got)))
            r = matching.match_line(self.db, self.ctx.org_id, raw_name=line)
            mine = "auto" if r.status == "auto" else "suggested"
            if mine != self.full_status(line):
                differ.append((line, "status", r.status, self.full_status(line)))
        self.assertGreater(truncated, 100)               # the shortlist did cut candidates in most questions
        self.assertEqual(differ, [])

    def test_the_bound_is_never_below_the_score(self):
        rng = random.Random(12)
        names = [("Vitamin B12 30 tab", "فيتامين ب12 30 قرص"), ("Panadol Extra 500mg 24 tab", ""), ("", "بانادول اكسترا 24 قرص"),
                 ("Omega 3 Plus 30 cap", ""), ("Zinc", ""), ("A", ""), ("Cream X 15g", "كريم 15 جم")]
        names += [("".join(rng.choice("abcdeiklmnoprstuz") for _ in range(rng.randint(2, 12))).capitalize()
                   + rng.choice(("", " 10mg", " 20 tab", " Forte", " 5 3")), "") for _ in range(300)]
        add_items(self.db, self.ctx.org_id, names)
        idx = matching._index(self.db, self.ctx.org_id)
        items = list(idx.items.values())
        for line in [n or a for n, a in names[:60]] + ["Panadol", "بانادول", "B12", "Vitamin B 12", "x"]:
            q = parse_name(line)
            for it in items:
                real = matching.score_item(q, it).score
                sim = max(matching.name_similarity(q, p) for p in it.parsed)
                factor = matching._factor(matching._asked(q), it)
                # the same product in another order: equal up to the last rounded place
                self.assertAlmostEqual(real, sim * factor, delta=0.0001, msg=(line, it.label))
                self.assertGreaterEqual(matching._bound(q, it) + 1e-9, sim, (line, it.label))


class TestOtherScriptInALargeCatalog(ReviewCase):
    """Second review: at 12,000 items every skeleton letter group is common, so a name in the other script was
    cut before it was ranked. 60 planted pairs: an English item spelt one vowel short and an Arabic-only item
    with the exact name; the invoice line is the full English spelling, so two items are within 0.05."""
    SYL = ("pan", "dol", "cet", "am", "ox", "il", "vor", "tra", "max", "zen", "lor", "fen", "bru", "cal", "dex",
           "mox", "ator", "glu", "cor", "neo", "flu", "ket", "rin", "sol", "vit", "omep", "raz", "tor", "lin", "gab")
    AR = ("بان", "دول", "سيت", "ام", "وكس", "يل", "فور", "ترا", "ماكس", "زين", "لور", "فين", "برو", "كال", "ديكس",
          "موكس", "اتور", "جلو", "كور", "نيو", "فلو", "كيت", "رين", "سول", "فيت", "اوميب", "راز", "تور", "لين", "جاب")
    TAILS = {"none": ("", ""), "form": (" cream", " كريم"), "full": (" 500mg 20 tab", " 500 مجم 20 قرص")}

    def brand(self, rng, words=None, syllables=None):
        en, ar = [], []
        for _ in range(words or rng.choice((1, 2, 2))):
            k = [rng.randrange(len(self.SYL)) for _ in range(syllables or rng.choice((2, 3, 3, 4)))]
            en.append("".join(self.SYL[i] for i in k).capitalize())
            ar.append("".join(self.AR[i] for i in k))
        return " ".join(en), " ".join(ar)

    def test_an_ambiguous_line_is_never_matched_without_a_person(self):
        rng = random.Random(2)
        names, seen = [], set()
        while len(names) < 12000:
            en, ar = self.brand(rng)
            te, ta = self.TAILS[rng.choice(("none", "form", "full", "full"))]
            if en + te in seen:
                continue
            seen.add(en + te)
            script = rng.choice(("en", "ar", "both"))
            names.append((en + te if script != "ar" else "", ar + ta if script != "en" else ""))
        lines = []
        for p in range(60):
            en, ar = self.brand(rng, 2, 3)
            te, ta = self.TAILS[("none", "form", "full")[p % 3]]
            first, second = en.split()
            vowels = [i for i, c in enumerate(first) if c in "aeiou" and 0 < i < len(first) - 1]
            i = rng.choice(vowels)
            names += [(first[:i] + first[i + 1:] + " " + second + te, ""), ("", ar + ta)]
            lines.append(en + te)
        add_items(self.db, self.ctx.org_id, names)
        wrong, differ, ambiguous = [], [], 0
        for line in lines:
            want = self.full(line)
            status = self.full_status(line)
            r = matching.match_line(self.db, self.ctx.org_id, raw_name=line)
            ambiguous += status == "suggested"
            if r.status == "auto" and status != "auto":
                wrong.append((line, [(c.label, c.score) for c in want[:2]], [(c.label, c.score) for c in r.candidates[:2]]))
            if [c.score for c in r.candidates[:2]] != [c.score for c in want[:2]]:
                differ.append(line)
        self.assertGreater(ambiguous, 15)            # the catalog does hold ambiguous lines
        self.assertEqual(wrong, [])
        self.assertEqual(differ, [])

    def test_a_line_whose_only_match_is_in_the_other_script(self):
        rng = random.Random(4)
        names, seen = [], set()
        while len(names) < 12000:
            en, ar = self.brand(rng)
            if en in seen:
                continue
            seen.add(en)
            script = rng.choice(("en", "ar", "both"))
            names.append((en if script != "ar" else "", ar if script != "en" else ""))
        ids = add_items(self.db, self.ctx.org_id, names)
        one_script = [(i, en, ar) for i, (en, ar) in zip(ids, names) if not en or not ar]
        missed = []
        for i, en, ar in rng.sample(one_script, 80):
            line = self._other(en, ar)
            want = self.full(line)
            got = matching.fuzzy_candidates(self.db, self.ctx.org_id, line, 5)
            if want and (not got or got[0].score != want[0].score):
                missed.append((line, want[0].label, want[0].score, got[0].label if got else None))
        self.assertEqual(missed, [])

    def _other(self, en, ar):
        """The same brand written in the script the item does not have."""
        table = dict(zip(self.SYL, self.AR)) if en else dict(zip(self.AR, self.SYL))
        out = []
        for word in (en or ar).lower().split():
            parts, rest = [], word
            while rest:
                syl = next(s for s in sorted(table, key=len, reverse=True) if rest.startswith(s))
                parts.append(table[syl])
                rest = rest[len(syl):]
            out.append("".join(parts))
        return " ".join(out)


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

    def later_millisecond(self):
        """Wait until the database clock has passed the newest item write. The index version is
        `count|max(updated_at)` at millisecond resolution: an unannounced change inside the SAME millisecond
        as the newest write does not move it (known limit, reproduced below as an expected failure). On a
        fast runner the test's own writes fell in one millisecond and this scenario failed by that limit, not
        by the behaviour it checks."""
        newest = self.db.one("SELECT max(updated_at) u FROM items WHERE org_id=?", (self.ctx.org_id,))["u"]
        while self.db.one("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now') n")["n"] <= newest:
            time.sleep(0.001)

    @unittest.expectedFailure
    def test_known_limit_a_silent_change_in_the_same_millisecond_as_the_newest_write(self):
        # the correct behaviour, not met today: the version label cannot tell this change happened.
        # Only a writer that announces nothing (bulk SQL) can hit it; the program's own writers announce.
        self.status("Brandix0 50mg 20 tab")                              # the index is built
        with self.db.transaction():
            self.db.execute("UPDATE items SET name_en='Quietly 1mg 10 tab', name_norm='quietly 1 mg 10 tab', strength_norm='1mg',"
                            " updated_at=(SELECT max(updated_at) FROM items WHERE org_id=?) WHERE id=?",
                            (self.ctx.org_id, self.ids[5]))
        self.assertEqual(self.status("Quietly 1mg 10 tab"), ("auto", self.ids[5]))

    def test_a_silent_change_followed_by_an_ordinary_edit_is_not_lost(self):
        # a writer that told nobody (bulk SQL), then an edit of another item before any search
        self.later_millisecond()
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

    def test_an_edit_that_changes_no_name_never_hides_the_item(self):
        idx = matching._index(self.db, self.ctx.org_id)
        entry = idx.items[self.ids[1]]
        seen_missing = []
        real_get = dict.get

        class Watch(dict):
            def pop(self, *a):
                seen_missing.append(a[0])
                return dict.pop(self, *a)
        idx.items = Watch(idx.items)
        cat.update_item(self.db, self.ctx, self.ids[1], {"min_stock": 3})
        self.assertIs(matching._index(self.db, self.ctx.org_id), idx)
        self.assertIs(idx.items[self.ids[1]], entry)
        self.assertEqual(seen_missing, [])
        cat.update_item(self.db, self.ctx, self.ids[1], {"name_en": "Renamed 75mg 20 tab"})
        self.assertEqual(seen_missing, [])                                 # replaced, never taken out first
        self.assertEqual(idx.items[self.ids[1]].label, "Renamed 75mg 20 tab")
        matching.forget(self.ctx.org_id)
        self.assertEqual(shape(idx), shape(matching._index(self.db, self.ctx.org_id)))
        del real_get

    def test_an_index_built_inside_a_write_is_not_kept_for_others(self):
        # a build that saw uncommitted rows must never be the shared index under a committed label
        matching.invalidate(self.ctx.org_id)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.execute("UPDATE items SET name_en='Uncommitted 9mg 10 tab', name_norm='uncommitted 9 mg 10 tab',"
                                " strength_norm='9mg' WHERE id=?", (self.ids[0],))      # updated_at untouched
                self.assertEqual(self.status("Uncommitted 9mg 10 tab"), ("auto", self.ids[0]))
                self.assertEqual(self.status("Uncommitted 9mg 10 tab"), ("auto", self.ids[0]))
                raise RuntimeError
        self.assertEqual(self.status("Uncommitted 9mg 10 tab")[0], "unmatched")
        self.assertEqual(self.elsewhere(lambda: self.status("Uncommitted 9mg 10 tab"))[0], "unmatched")
        self.assertEqual(self.elsewhere(lambda: self.status("Brandix0 50mg 20 tab")), ("auto", self.ids[0]))

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
