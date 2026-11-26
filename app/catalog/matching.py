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

import heapq
import re
from itertools import islice
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
    grams: tuple = ()          # per parsed name: its letter groups (`_grams`), for the shortlist


def _grams(p: ParsedName) -> frozenset:
    """Letter groups of a parsed name: three letters in a row of every name word (with its start and end), and
    two in a row of its consonant skeleton (the part an Arabic and an English spelling share). Two names that
    are close share most of them; counting the shared ones costs a fraction of comparing the names."""
    out = set()
    for t in p.core_tokens:
        w = f"^{t}$"
        out.update(w[i:i + 3] for i in range(len(w) - 2))
    for sk in p.skeleton.split():
        w = f"#{sk}#"
        out.update("~" + w[i:i + 2] for i in range(len(w) - 1))
    return frozenset(out)


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
        for it in items:
            self._add(it)

    def _add(self, it: _IndexedItem) -> None:
        for key in _keys(it):
            self.by_key.setdefault(key, set()).add(it.item_id)
        n = 0
        for grams in it.grams:
            n += len(grams)
            for g in grams:
                self.by_gram.setdefault(g, set()).add(it.item_id)
        self.by_size.setdefault(n, set()).add(it.item_id)
        self.by_attr.setdefault(_attrs(it), set()).add(it.item_id)

    def _drop(self, it: _IndexedItem) -> None:
        for table, keys in ((self.by_key, _keys(it)), (self.by_gram, set().union(*it.grams)),
                            (self.by_size, (sum(map(len, it.grams)),)), (self.by_attr, (_attrs(it),))):
            for key in keys:
                ids = table.get(key)
                if ids is not None:
                    ids.discard(it.item_id)
                    if not ids:
                        del table[key]

    def replace(self, item_id: str, it: "_IndexedItem | None") -> None:
        """One item was added, edited or stopped: the index follows without being built again (a rebuild of
        30,000 names cost the next search 0.3 s and more, #461)."""
        old = self.items.pop(item_id, None)
        if old is not None:
            self._drop(old)
        if it is not None:
            self.items[item_id] = it
            self._add(it)
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
        grams=tuple(_grams(p) for p in parsed),
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
    with _cache_lock:
        hit = _cache.get(org_id)
        if hit and hit[0] == ver:
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
        _cache[org_id] = (ver, idx)
        _parsed[org_id] = seen           # only the items of this build: a stopped or deleted item is dropped
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


def item_changed(db: Database, org_id: str, item_id: str) -> None:
    """One item was created or edited (after its transaction): the index is updated in place, so the next
    search does not pay for a rebuild. Without an index yet there is nothing to do; if anything else changed
    in the catalog meanwhile, the version no longer matches and the next question rebuilds as before."""
    with _cache_lock:                            # one change at a time, in order; a read of one row is short
        hit = _cache.get(org_id)
        if hit is None:
            return
        idx = hit[1]
        row = db.one("SELECT id, name_en, name_ar, strength_norm, dosage_form, status FROM items"
                     " WHERE id=? AND org_id=?", (item_id, org_id))
        known = _parsed.setdefault(org_id, {})
        if row is None or row["status"] != "active":
            idx.replace(item_id, None)
            known.pop(item_id, None)
        else:
            key = (row["name_en"], row["name_ar"], row["strength_norm"], row["dosage_form"])
            old = known.get(item_id)
            it = old[1] if old is not None and old[0] == key else _parse_item(row)
            idx.replace(item_id, it)
            known[item_id] = (key, it)
        _cache[org_id] = (_version(db, org_id), idx)


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


def name_similarity(q: ParsedName, p: ParsedName) -> float:
    direct = _ratio(q.core, p.core)
    # token coverage: every query token should find a close token in the item
    if q.core_tokens and p.core_tokens:
        cover = sum(max(_ratio(t, u) for u in p.core_tokens) for t in q.core_tokens) / len(q.core_tokens)
        back = sum(max(_ratio(u, t) for t in q.core_tokens) for u in p.core_tokens) / len(p.core_tokens)
        tokens = (cover + back) / 2
    else:
        tokens = 0.0
    skel = _ratio(q.skeleton, p.skeleton) * 0.95 if q.skeleton and p.skeleton else 0.0
    return max(direct, tokens, skel)


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


SHORTLIST_MIN, SHORTLIST_MAX = 200, 400
RANK_MAX = 2000        # at most this many candidates are ranked one by one (about 3 µs each)


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
    qg = _grams(q)
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
                short.update(islice(idx.by_size[n] & rest, RANK_MAX - len(short)))
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
        pool = set(heapq.nlargest(RANK_MAX, pool, key=count.__getitem__))
    # a name that shares only the common words but has the asked strength, form and pack scores above a
    # closer name without them (score_item's weights): those are ranked too, when they are few enough
    same = idx.by_attr.get(_attrs(q))
    if same and (q.strength or q.pack_count):
        same = same & ids
        if len(same) <= RANK_MAX:
            pool |= same
    found = [it for it in map(items.get, pool) if it is not None]
    if len(found) <= keep:
        return found
    nq = len(qg)
    qform = FORM_GROUP.get(q.form or "", q.form)

    def rank(it: _IndexedItem) -> float:
        best = 0.0
        for g in it.grams:
            n = len(g)
            if n:
                share = 2 * len(qg & g) / (nq + n)
                if share > best:
                    best = share
        # the same weights as score_item: a name a little further with the asked strength, form and pack
        # ends above a closer name without them, so it must not be cut here
        for qv, iv, miss in ((q.strength, it.strength, 0.35), (qform, FORM_GROUP.get(it.form or "", it.form), 0.6),
                             (q.content, it.content, 0.6), (q.pack_count, it.pack_count, 0.6)):
            if qv != iv:
                best *= miss if qv and iv else 0.94
        return best if q.modifiers == it.modifiers else best * 0.7
    return heapq.nlargest(keep, found, key=rank)


def fuzzy_candidates(db: Database, org_id: str, raw_name: str, limit: int = 5) -> list[Candidate]:
    idx = _index(db, org_id)
    key = (raw_name, limit)
    hit = idx.memo.get(key)
    if hit is None:
        memo, changes = idx.memo, idx.changes
        q = parse_name(raw_name)
        keep = min(max(SHORTLIST_MIN, 3 * limit), SHORTLIST_MAX)
        scored = [score_item(q, it) for it in _shortlist(idx, q, idx.candidates(q), keep)]
        scored.sort(key=lambda c: c.score, reverse=True)
        hit = [c for c in scored if c.score >= SUGGEST_MIN * 0.6][:limit]
        if idx.changes == changes:                 # an item changed while this was worked out: not remembered
            if len(memo) >= MEMO_MAX:              # bounded: a long import of new names must not grow it forever
                memo.clear()
            memo[key] = hit
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
