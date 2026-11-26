"""Item matching: map a raw invoice line to an item in the pharmacy catalog.

Order of evidence (strongest first):
  1. barcode            -> auto, unless the names clearly disagree
  2. supplier item code -> auto, same sanity check
  3. learned alias      -> auto (a human confirmed this exact spelling before)
  4. fuzzy name         -> 'suggested' (needs a human click) unless near-certain
A fuzzy match never becomes 'auto' if strength, form, pack count, pack content
or a modifier (Extra, XR, Forte...) disagrees or is missing.
"""
from __future__ import annotations

import gc
import heapq
import re
from collections import Counter
from itertools import islice
from operator import itemgetter
import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

from ..db import Database
from .normalize import FORM_GROUP, ParsedName, normalize_barcode, normalize_text, parse_name

AUTO_FUZZY_MIN = 0.97
SUGGEST_MIN = 0.55
NAME_CONFLICT_MAX = 0.45   # identifier hit but names this different -> ask the user


@dataclass
class Candidate:
    item_id: str
    label: str
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    item_id: str | None
    method: str | None
    score: float
    status: str                      # auto | suggested | unmatched
    candidates: list[Candidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _IndexedItem:
    item_id: str
    label: str
    parsed: list[ParsedName]
    strength: str | None
    form: str | None
    content: str | None
    pack_count: int | None
    modifiers: frozenset
    grams: tuple = ()          # per parsed name: (word groups, skeleton groups) of `_grams`, for the shortlist
    numbers: tuple = ()        # per parsed name: the numbers that belong to the name (`_numbers`)


def _grams(p: ParsedName) -> tuple[frozenset, frozenset]:
    """Letter groups of a parsed name: three letters in a row of every name word (with its start and end), and
    two in a row of its consonant skeleton (the part an Arabic and an English spelling share), kept apart: a
    name in the other script shares only the second kind. Two names that are close share most of them;
    counting the shared ones costs a fraction of comparing the names."""
    words, skel = set(), set()
    for t in p.core_tokens:
        w = f"^{t}$"
        words.update(w[i:i + 3] for i in range(len(w) - 2))
    for sk in p.skeleton.split():
        w = f"#{sk}#"
        skel.update("~" + w[i:i + 2] for i in range(len(w) - 1))
    return frozenset(words), frozenset(skel)


def _numbers(p: ParsedName) -> frozenset:
    """Numbers that belong to the name (B12, Omega 3): score_item requires them to agree exactly."""
    return frozenset(re.sub(r"\D", "", t) for t in p.core_tokens if re.search(r"\d", t))


def _all_grams(it) -> set[str]:
    return set().union(*(g for pair in it.grams for g in pair))


def _attrs(it) -> tuple:
    """Strength, form group and pack count of an item or of a parsed query."""
    return it.strength, FORM_GROUP.get(it.form or "", it.form), it.pack_count


def _keys(it: _IndexedItem) -> set[str]:
    keys = set()
    for p in it.parsed:
        keys.update(tok[:3] for tok in p.core_tokens)
        keys.update("#" + sk[:3] for sk in p.skeleton.split())
    keys.discard("")
    return keys


class _OrgIndex:
    def __init__(self, items: list[_IndexedItem]):
        self.items = {i.item_id: i for i in items}
        # answers already worked out for this catalog version (dropped as soon as any item changes, so an
        # answer here can never be stale). The sale screen asks for the same names all day.
        self.memo: dict[tuple[str, int], list[Candidate]] = {}
        self.changes = 0             # counts item changes: an answer worked out across a change is not remembered
        self.by_key: dict[str, set[str]] = {}
        self.by_gram: dict[str, set[str]] = {}       # letter group -> items that have it (the shortlist)
        self.by_size: dict[int, set[str]] = {}       # how many letter groups an item's names have -> items
        self.by_attr: dict[tuple, set[str]] = {}     # (strength, form group, pack count) -> items
        for it in items:                             # not shared yet: filled in place
            for table, keys in self._where(it):
                for key in keys:
                    ids = table.get(key)
                    if ids is None:
                        table[key] = {it.item_id}
                    else:
                        ids.add(it.item_id)

    def _where(self, it: _IndexedItem):
        return ((self.by_key, _keys(it)), (self.by_gram, _all_grams(it)),
                (self.by_size, (sum(len(g) for pair in it.grams for g in pair),)), (self.by_attr, (_attrs(it),)))

    def replace(self, item_id: str, it: "_IndexedItem | None") -> None:
        """One item was added, edited or stopped: the index follows without being built again (a rebuild of
        30,000 names cost the next search 0.3 s and more, #461). Searches on other threads read these sets
        while this runs, so a set is never changed in place: it is replaced by a new one."""
        old = self.items.get(item_id)
        if old is not it:
            # the new entry goes in before the old one's leftovers come out: a search running now finds the
            # item under its old or its new name, never neither (an edit of a price changes no name at all)
            new = self._where(it) if it is not None else ()
            for table, keys in new:
                for key in keys:
                    table[key] = table.get(key, frozenset()) | {item_id}
            if it is not None:
                self.items[item_id] = it
            else:
                self.items.pop(item_id, None)
            if old is not None:
                stay = {id(table): set(keys) for table, keys in new}
                for table, keys in self._where(old):
                    for key in set(keys) - stay.get(id(table), set()):
                        left = table.get(key, frozenset()) - {item_id}
                        if left:
                            table[key] = left
                        else:
                            table.pop(key, None)
        self.changes += 1
        self.memo = {}

    def candidates(self, q: ParsedName) -> set[str]:
        ids: set[str] = set()
        for tok in q.core_tokens:
            ids |= self.by_key.get(tok[:3], set())
        for sk in q.skeleton.split():
            ids |= self.by_key.get("#" + sk[:3], set())
        return ids


MEMO_MAX = 5000
_cache: dict[str, tuple[str, _OrgIndex]] = {}
# org -> item id -> ((name_en, name_ar, strength_norm, dosage_form), parsed entry or None when it has no name)
_parsed: dict[str, dict[str, tuple[tuple, "_IndexedItem | None"]]] = {}
_cache_lock = threading.Lock()


def _parse_item(row, primary: ParsedName | None = None) -> _IndexedItem | None:
    label = row["name_en"] or row["name_ar"]
    if not label:
        return None
    parsed = [primary or parse_name(label)]
    if row["name_en"] and row["name_ar"]:
        parsed.append(parse_name(row["name_ar"]))
    return _IndexedItem(
        item_id=row["id"], label=label, parsed=parsed,
        strength=row["strength_norm"] or next((p.strength for p in parsed if p.strength), None),
        form=row["dosage_form"] or next((p.form for p in parsed if p.form), None),
        content=next((p.content for p in parsed if p.content), None),
        pack_count=next((p.pack_count for p in parsed if p.pack_count), None),
        modifiers=frozenset().union(*(p.modifiers for p in parsed)),
        grams=tuple(_grams(p) for p in parsed), numbers=tuple(_numbers(p) for p in parsed),
    )


# connection -> pharmacy -> (what the connection had seen, the version read then). Bounded: a connection is
# one per server thread; the tests open thousands.
_seen: dict = {}
SEEN_MAX = 64


def _version(db: Database, org_id: str) -> str:
    """Count and newest change of the active items. Reading it scans every item (6 ms for 30,000), and it is
    asked on every question (31 times to open one purchase invoice), so a connection asks again only after
    the database changed: SQLite's data_version moves when any other connection commits, total_changes when
    this one writes. Both are read before the items, so a change in between is only ever seen as new."""
    conn = db.conn
    stamp = (conn.execute("PRAGMA data_version").fetchone()[0], conn.total_changes)
    if conn.in_transaction and not db.in_snapshot:
        # inside a write, what this connection sees may be rolled back, and neither counter moves then: what
        # is read here is good for this transaction only (nobody else can commit while it holds the write)
        stamp = (*stamp, db.write_serial)
    per = _seen.get(conn)
    if per is None:
        if len(_seen) >= SEEN_MAX:
            _seen.clear()
        per = _seen.setdefault(conn, {})
    hit = per.get(org_id)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    r = db.one("SELECT count(*) c, max(updated_at) u FROM items WHERE org_id=? AND status='active'",
               (org_id,))
    per[org_id] = (stamp, f"{r['c']}|{r['u']}")
    return per[org_id][1]


def _index(db: Database, org_id: str) -> _OrgIndex:
    ver = _version(db, org_id)
    # an index built inside a write may hold rows that are rolled back later: it is kept under a label only
    # this transaction asks for, so no other thread (and no later transaction) ever takes it for the catalog
    own = f"{ver}|w{id(db.conn)}.{db.write_serial}" if db.conn.in_transaction and not db.in_snapshot else None
    with _cache_lock:
        hit = _cache.get(org_id)
        if hit and hit[0] in (ver, own):
            return hit[1]
        known = _parsed.get(org_id, {})
    rows = db.all("SELECT id, name_en, name_ar, strength_norm, dosage_form FROM items"
                  " WHERE org_id=? AND status='active'", (org_id,))
    items, seen = [], {}
    for r in rows:
        # parsing a name is the whole cost of a rebuild (0.8 s for 15,000 names), and a rebuild follows every
        # item change: an item whose names and fields are what they were keeps its parsed entry (#304 B4)
        key = (r["name_en"], r["name_ar"], r["strength_norm"], r["dosage_form"])
        old = known.get(r["id"])
        if old is not None and old[0] == key:
            it = old[1]
        else:
            it = _parse_item(r)
        seen[r["id"]] = (key, it)
        if it is not None:
            items.append(it)
    idx = _OrgIndex(items)
    with _cache_lock:
        _cache[org_id] = (own or ver, idx)
        if own is None:
            _parsed[org_id] = seen       # only the items of this build: a stopped or deleted item is dropped
    # 30,000 names are some 600,000 small objects that live as long as the program: left where they are,
    # every full garbage collection walks them all (0.3 s, in the middle of some cashier's search). Frozen,
    # they are skipped; they are still freed normally when a newer index replaces them (no reference cycles).
    gc.freeze()
    return idx


def prewarm(db: Database) -> None:
    """Build every pharmacy's index once, in the background at start (update 1: the first fuzzy search after a
    start took ~3 s on 30,000 items, on the cashier's keystroke)."""
    for r in db.all("SELECT id FROM organizations"):
        try:
            _index(db, r["id"])
        except Exception:                       # a warm-up only: the first search builds it anyway
            pass


def invalidate(org_id: str) -> None:
    """The catalog changed: the next question builds the index again (the parsed names are kept, see _index)."""
    with _cache_lock:
        _cache.pop(org_id, None)


def before_change(db: Database, org_id: str, outer: bool) -> str | None:
    """Called by the writer of one item inside its own transaction, before it writes: the catalog version the
    change starts from, or None when the write is part of a bigger operation (`outer`: a transaction was
    already open), which may still be rolled back."""
    return None if outer else _version(db, org_id)


def item_changed(db: Database, org_id: str, item_id: str, seen: str | None) -> None:
    """One item was created or edited and its transaction committed: the index is updated in place, so the
    next search does not pay for a rebuild. Only when that is certain to give the index a build would give:
    the index must be at the version the write started from (`seen`, read inside the write transaction, when
    nothing else can commit) and the item must be active. Anything else (a write inside a bigger operation
    that may roll back, a change nobody announced, an index built meanwhile) drops the index, and the next
    question builds it as before."""
    if seen is None or db.conn.in_transaction:
        return invalidate(org_id)
    with _cache_lock:                            # one change at a time, in order; one row read by its key
        hit = _cache.get(org_id)
        if hit is None:
            return
        row = db.one("SELECT id, name_en, name_ar, strength_norm, dosage_form, status, updated_at FROM items"
                     " WHERE id=? AND org_id=?", (item_id, org_id))
        if hit[0] != seen or row is None or row["status"] != "active":
            _cache.pop(org_id, None)
            return
        idx, known = hit[1], _parsed.setdefault(org_id, {})
        key = (row["name_en"], row["name_ar"], row["strength_norm"], row["dosage_form"])
        old = known.get(item_id)
        it = old[1] if old is not None and old[0] == key else _parse_item(row)
        idx.replace(item_id, it)
        known[item_id] = (key, it)
        # the version this one change leads to (`_version`: count and newest change of the active items);
        # if anything else changed, the real one differs and the next question rebuilds
        count, _, newest = seen.partition("|")
        newest = row["updated_at"] if newest == "None" else max(newest, row["updated_at"])
        _cache[org_id] = (f"{int(count) + (old is None)}|{newest}", idx)


def forget(org_id: str) -> None:
    """Drop the index and the parsed names of a pharmacy (tests; a build from nothing)."""
    with _cache_lock:
        _cache.pop(org_id, None)
        _parsed.pop(org_id, None)


# ---------------------------------------------------------------------------
@lru_cache(maxsize=16384)
def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if set(a).isdisjoint(b):
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def spelling_similarity(q: ParsedName, p: ParsedName) -> float:
    """How close two names are as written (no consonant skeleton): the whole name or its words."""
    direct = _ratio(q.core, p.core)
    # token coverage: every query token should find a close token in the item
    if q.core_tokens and p.core_tokens:
        cover = sum(max(_ratio(t, u) for u in p.core_tokens) for t in q.core_tokens) / len(q.core_tokens)
        back = sum(max(_ratio(u, t) for t in q.core_tokens) for u in p.core_tokens) / len(p.core_tokens)
        tokens = (cover + back) / 2
    else:
        tokens = 0.0
    return max(direct, tokens)


def name_similarity(q: ParsedName, p: ParsedName) -> float:
    skel = _ratio(q.skeleton, p.skeleton) * 0.95 if q.skeleton and p.skeleton else 0.0
    return max(spelling_similarity(q, p), skel)


_ARABIC_LETTER = re.compile(r"[\u0600-\u06ff]")


def same_script_similarity(db: Database, org_id: str, raw_name: str, item_id: str) -> float:
    """How close `raw_name` is, as written, to the item's name in the same script (0 when the item has no
    name in that script). The consonant skeleton lets an Arabic and an English spelling meet; for text that
    may be keyboard gibberish that is exactly the wrong test (search: a query typed on the other layout)."""
    it = _index(db, org_id).items.get(item_id)
    if it is None:
        return 0.0
    q = parse_name(raw_name)
    arabic = bool(_ARABIC_LETTER.search(q.core))
    return max((spelling_similarity(q, p) for p in it.parsed if bool(_ARABIC_LETTER.search(p.core)) == arabic),
               default=0.0)


def score_item(q: ParsedName, it: _IndexedItem) -> Candidate:
    sim = max(name_similarity(q, p) for p in it.parsed)
    score, reasons = sim, []

    def attr(qv, iv, code, mismatch_factor):
        nonlocal score
        if qv and iv:
            if qv != iv:
                score *= mismatch_factor
                reasons.append(f"{code}_mismatch")
        elif qv or iv:
            score *= 0.94
            reasons.append(f"{code}_missing")

    attr(q.strength, it.strength, "strength", 0.35)
    qf, itf = FORM_GROUP.get(q.form or "", q.form), FORM_GROUP.get(it.form or "", it.form)
    attr(qf, itf, "form", 0.6)
    attr(q.content, it.content, "content", 0.6)
    attr(q.pack_count, it.pack_count, "pack", 0.6)
    if q.modifiers != it.modifiers:
        score *= 0.7
        reasons.append("modifier_mismatch")
    # numbers that belong to the name (B12 vs B6, Omega 3 vs 6) must agree exactly
    qn = {re.sub(r"\D", "", t) for t in q.core_tokens if re.search(r"\d", t)}
    inn = [{re.sub(r"\D", "", t) for t in p.core_tokens if re.search(r"\d", t)} for p in it.parsed]
    if (qn or any(inn)) and qn not in inn:
        score *= 0.35
        reasons.append("name_number_mismatch")
    return Candidate(it.item_id, it.label, round(score, 4), reasons)


def _asked(q: ParsedName) -> tuple:
    """What `_factor` needs of a query, worked out once."""
    return q.strength, FORM_GROUP.get(q.form or "", q.form), q.content, q.pack_count, q.modifiers, _numbers(q)


def _factor(asked: tuple, it: _IndexedItem) -> float:
    """What score_item multiplies the name similarity by for this item: exactly its strength, form, pack
    content, pack count, modifier and name-number rules, without the name comparison (which is the cost)."""
    qs, qf, qc, qp, qm, qn = asked
    f = 1.0
    for qv, iv, miss in ((qs, it.strength, 0.35), (qf, FORM_GROUP.get(it.form or "", it.form), 0.6),
                         (qc, it.content, 0.6), (qp, it.pack_count, 0.6)):
        if qv and iv:
            if qv != iv:
                f *= miss
        elif qv or iv:
            f *= 0.94
    if qm != it.modifiers:
        f *= 0.7
    if (qn or any(it.numbers)) and qn not in it.numbers:
        f *= 0.35
    return f


def _closest(a: int, b: int) -> float:
    """The most SequenceMatcher.ratio can be for two strings of these lengths (all of the shorter one matches)."""
    return 2 * min(a, b) / (a + b) if a and b else 0.0


def _bound(q: ParsedName, it: _IndexedItem) -> float:
    """An upper bound of name_similarity(q, p) over the item's names, from lengths alone: never below the
    real value, so a candidate whose bound is too low can be left out with certainty."""
    best = 0.0
    qt = [len(t) for t in q.core_tokens]
    for p in it.parsed:
        best = max(best, _closest(len(q.core), len(p.core)), 0.95 * _closest(len(q.skeleton), len(p.skeleton)))
        pt = [len(t) for t in p.core_tokens]
        if qt and pt:
            cover = sum(max(_closest(a, b) for b in pt) for a in qt) / len(qt)
            back = sum(max(_closest(b, a) for a in qt) for b in pt) / len(pt)
            best = max(best, (cover + back) / 2)
    return best


def _complete(idx: _OrgIndex, q: ParsedName, left_out, sure_from: float) -> list[Candidate]:
    """Every candidate the shortlist left out that scores `sure_from` or more. The shortlist ranks by an
    approximation, so it cannot prove that nothing close was cut; a decision taken without a person (an
    automatic purchase match, "no similar item exists") needs that proof. Each left-out candidate gets its
    exact attribute factor and a real upper bound of its name similarity, and is compared in full only when
    the two together can still reach the score."""
    asked, floor, out = _asked(q), sure_from - 0.0001, []          # 0.0001: scores are rounded to 4 places
    qn, numbers_decide = asked[5], floor > 0.35
    for it in map(idx.items.get, left_out):
        if it is None:
            continue
        if numbers_decide and (qn or any(it.numbers)) and qn not in it.numbers:
            continue                       # the name-number rule alone (x0.35) puts it below the score
        f = _factor(asked, it)
        if f < floor or f * _bound(q, it) < floor:
            continue
        c = score_item(q, it)
        if c.score >= sure_from:
            out.append(c)
    return out


# No candidate scoring this or more is ever missing from an answer (unless the caller opts out): below the
# lowest score anything is decided from without a person (import "similar item" 0.90, purchase match 0.92,
# duplicate check 0.93).
SURE_FROM = 0.9
SHORTLIST_MIN, SHORTLIST_MAX = 120, 400
SKEL_BUDGET, SKEL_KEEP = 60000, 400      # skeleton-group postings counted per question; candidates they add
RANK_MAX = 1500        # at most this many candidates are ranked one by one (about 3 µs each)


def _shortlist(idx: _OrgIndex, q: ParsedName, ids: set[str], keep: int) -> list[_IndexedItem]:
    """The `keep` candidates whose names share the most letter groups with the query; only these are compared
    in full. A catalog where thousands of names start alike (one maker's prefix, «صنف ...») made every one of
    them a candidate, and comparing 30,000 names took 1.3 s per search (#461). Ties go to the candidate whose
    strength, form and pack agree with the query, so the right variant of one name is never the one left out.

    Ranking 30,000 candidates one by one is itself too slow, so the pool is cut first with set operations:
    candidates that share a letter group few items have (the telling ones) come first; when those are not
    enough, the rest is narrowed by the common groups, the least common first."""
    items = idx.items
    if len(ids) <= keep:
        return [it for it in map(items.get, ids) if it is not None]
    qw, qs = _grams(q)
    qg = qw | qs
    posts = sorted(((len(p), g, p) for g in qg if (p := idx.by_gram.get(g))), key=lambda x: x[0])
    rare_max = max(500, len(items) // 20)
    pool: set[str] = set()
    for n, _, post in posts:
        if n > rare_max:
            break
        pool |= post
    pool &= ids
    if len(pool) < keep:
        rest = ids - pool
        need = keep - len(pool)
        for n, _, post in posts:
            if len(rest) <= RANK_MAX:
                break
            if rare_max < n < len(rest):         # a group at least as common as what is left tells nothing
                cut = rest & post
                if len(cut) >= need:
                    rest = cut
        if len(rest) > RANK_MAX:                 # still alike as far as the groups tell: the shortest names
            short: set[str] = set()
            for n in sorted(idx.by_size):
                short.update(islice(idx.by_size.get(n, frozenset()) & rest, RANK_MAX - len(short)))
                if len(short) >= RANK_MAX:
                    break
            rest = short
        pool = pool.union(rest)
    elif len(pool) > RANK_MAX:
        # too many share a telling group: keep those sharing the most of them
        count: dict[str, int] = {}
        for n, _, post in posts:
            if n > rare_max:
                break
            for i in post:
                count[i] = count.get(i, 0) + 1
        pool = set(heapq.nlargest(RANK_MAX, pool, key=lambda i: count.get(i, 0)))
    # a name that shares only the common words but has the asked strength, form and pack scores above a
    # closer name without them (score_item's weights): those are ranked too, when they are few enough
    same = idx.by_attr.get(_attrs(q))
    if same and (q.strength or q.pack_count):
        same = same & ids
        if len(same) <= RANK_MAX:
            pool |= same
    # a name in the other script shares only skeleton groups with the query, and in a large catalog every one
    # of those is common, so nothing above brings it in: the candidates sharing the most skeleton groups are
    # ranked too. Groups nearly every item has tell nothing and are skipped; the work is bounded.
    if qs:
        shared: Counter = Counter()
        budget, most = SKEL_BUDGET, 0.9 * len(items)
        for n, g, post in posts:
            if g[0] != "~" or n >= most:
                continue
            if n > budget:
                break
            shared.update(post & ids)
            budget -= n
        pool = pool.union(i for i, _ in heapq.nlargest(SKEL_KEEP, shared.items(), key=itemgetter(1)))
    found = [it for it in map(items.get, pool) if it is not None]
    if len(found) <= keep:
        return found
    nw, ns = len(qw), len(qs)
    asked = _asked(q)

    def rank(it: _IndexedItem) -> float:
        # like name_similarity: the closer of the spelling and (at 0.95) the consonant skeleton, so a name in
        # the other script, which shares no letters with the query, is ranked by what it does share
        best = 0.0
        for words, skel in it.grams:
            if words and nw:
                share = 2 * len(qw & words) / (nw + len(words))
                if share > best:
                    best = share
            if skel and ns:
                share = 1.9 * len(qs & skel) / (ns + len(skel))
                if share > best:
                    best = share
        # times score_item's own weights: a name a little further with the asked strength, form and pack ends
        # above a closer name without them, and B12 against B1000 ends far below B12's twin
        return best * _factor(asked, it)
    return heapq.nlargest(keep, found, key=rank)


def fuzzy_candidates(db: Database, org_id: str, raw_name: str, limit: int = 5, *,
                     compare: int | None = None, sure_from: float | None = SURE_FROM) -> list[Candidate]:
    """The closest `limit` items. `compare`: how many shortlisted candidates are compared in full (default
    three for every one asked, 120 to 400); the sale screen asks for more answers than that ratio gives.
    `sure_from`: a score from which no candidate may be missing (`_complete`). It is the default, because
    callers decide things from the answer without a person (an automatic purchase match, "no similar item,
    create it" in an import, the duplicate check); only a search that offers guesses to a person passes None."""
    idx = _index(db, org_id)
    key = (raw_name, limit, compare, sure_from)
    hit = idx.memo.get(key)
    if hit is None:
        memo, changes = idx.memo, idx.changes
        q = parse_name(raw_name)
        keep = max(limit, compare) if compare else min(max(SHORTLIST_MIN, 3 * limit), SHORTLIST_MAX)
        ids = idx.candidates(q)
        short = _shortlist(idx, q, ids, keep)
        scored = [score_item(q, it) for it in short]
        if sure_from is not None and len(ids) > len(short):
            scored += _complete(idx, q, ids.difference(it.item_id for it in short), sure_from)
        scored.sort(key=lambda c: c.score, reverse=True)
        hit = [c for c in scored if c.score >= SUGGEST_MIN * 0.6][:limit]
        if idx.changes == changes:                 # an item changed while this was worked out: not remembered
            if len(memo) >= MEMO_MAX:              # bounded: a long import of new names must not grow it forever
                memo.clear()
            memo[key] = hit
            if sure_from is not None and len(memo) < MEMO_MAX:      # also the answer for a caller that needs
                memo.setdefault((raw_name, limit, compare, None), hit)                         # no proof
    # copies: a caller may change what it gets without changing the remembered answer
    return [Candidate(c.item_id, c.label, c.score, list(c.reasons)) for c in hit]


def lookalike_candidates(db: Database, org_id: str, label: str, exclude: set[str],
                         limit: int = 6) -> list[Candidate]:
    """A sale warning requires an identical parsed core and a different attribute."""
    query = parse_name(label)
    if not query.core:
        return []
    # Parsing can join dotted release modifiers (e.g. X.R. becomes xr) and F.C.
    # All other core tokens occur literally in the normalized name before parsing.
    anchors = sorted(set(query.core_tokens) - {"sr", "xr", "er", "mr", "cr", "fc"})
    where = " AND ".join("instr(coalesce(name_norm,''), ?) > 0" for _ in anchors)
    rows = db.all("SELECT id, name_en, name_ar, strength_norm, dosage_form FROM items"
                  " WHERE org_id=? AND status='active'" + (" AND " + where if where else ""),
                  (org_id, *anchors))
    candidates = []
    for row in rows:
        if row["id"] in exclude:
            continue
        primary = parse_name(row["name_en"] or row["name_ar"] or "")
        if primary.core != query.core:
            continue
        item = _parse_item(row, primary)
        candidate = score_item(query, item)
        if candidate.score >= SUGGEST_MIN * 0.6 and any(r.endswith("_mismatch") for r in candidate.reasons):
            candidates.append(candidate)
    return sorted(candidates, key=lambda c: (-c.score, c.item_id))[:limit]


def _label_score(db: Database, org_id: str, item_id: str, raw_name: str) -> float:
    idx = _index(db, org_id)
    it = idx.items.get(item_id)
    if not it or not raw_name:
        return 1.0
    q = parse_name(raw_name)
    return max(name_similarity(q, p) for p in it.parsed)


def match_line(db: Database, org_id: str, *, raw_name: str, raw_code: str | None = None,
               raw_barcode: str | None = None, supplier_id: str | None = None,
               limit: int = 5) -> MatchResult:
    warnings: list[str] = []
    # automatic only when the best is 0.97 or more and nothing else is within 0.05 of it: so no candidate from
    # 0.92 up may be missing from what the decision looks at (SURE_FROM, the default, is below that)
    cands = fuzzy_candidates(db, org_id, raw_name, limit) if raw_name else []

    def identified(item_id: str, method: str, score: float) -> MatchResult:
        sim = _label_score(db, org_id, item_id, raw_name)
        status = "auto"
        if raw_name and sim < NAME_CONFLICT_MAX:
            status = "suggested"
            warnings.append(f"{method}_name_conflict")
        top = [c for c in cands if c.item_id != item_id]
        label = db.one("SELECT coalesce(name_en, name_ar) n FROM items WHERE id=?", (item_id,))["n"]
        return MatchResult(item_id, method, score, status,
                           [Candidate(item_id, label, score, [method])] + top[:limit - 1], warnings)

    bc = normalize_barcode(raw_barcode)
    if bc:
        r = db.one("SELECT b.item_id FROM item_barcodes b JOIN items i ON i.id=b.item_id"
                   " WHERE b.org_id=? AND b.barcode=? AND i.status='active'", (org_id, bc))
        if r:
            return identified(r["item_id"], "barcode", 1.0)
    if raw_code and supplier_id:
        r = db.one("SELECT c.item_id FROM supplier_item_codes c JOIN items i ON i.id=c.item_id"
                   " WHERE c.org_id=? AND c.supplier_id=? AND c.code=? AND i.status='active'",
                   (org_id, supplier_id, str(raw_code).strip()))
        if r:
            return identified(r["item_id"], "supplier_code", 0.99)
    norm = normalize_text(raw_name)
    if norm:
        r = db.one("SELECT a.item_id FROM item_aliases a JOIN items i ON i.id=a.item_id"
                   " WHERE a.org_id=? AND a.alias_norm=? AND a.supplier_key IN (?, '')"
                   " AND i.status='active' ORDER BY a.supplier_key DESC LIMIT 1",
                   (org_id, norm, supplier_id or ""))
        if r:
            return identified(r["item_id"], "alias", 0.97)

    if not cands or cands[0].score < SUGGEST_MIN:
        return MatchResult(None, None, cands[0].score if cands else 0.0, "unmatched", cands)
    best = cands[0]
    second = cands[1].score if len(cands) > 1 else 0.0
    clean = not best.reasons
    if best.score >= AUTO_FUZZY_MIN and clean and best.score - second >= 0.05:
        return MatchResult(best.item_id, "fuzzy", best.score, "auto", cands)
    if second and best.score - second < 0.05:
        warnings.append("ambiguous")
    return MatchResult(best.item_id, "fuzzy", best.score, "suggested", cands, warnings)
