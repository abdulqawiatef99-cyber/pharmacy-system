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

import re
import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher

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


class _OrgIndex:
    def __init__(self, items: list[_IndexedItem]):
        self.items = {i.item_id: i for i in items}
        # answers already worked out for this catalog version (the index is rebuilt when any item changes,
        # so an answer here can never be stale). The sale screen asks for the same names all day.
        self.memo: dict[tuple[str, int], list[Candidate]] = {}
        self.by_key: dict[str, set[str]] = {}
        for it in items:
            for p in it.parsed:
                for tok in p.core_tokens:
                    self._add(tok[:3], it.item_id)
                for sk in p.skeleton.split():
                    self._add("#" + sk[:3], it.item_id)

    def _add(self, key: str, item_id: str):
        if key:
            self.by_key.setdefault(key, set()).add(item_id)

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


def _version(db: Database, org_id: str) -> str:
    r = db.one("SELECT count(*) c, max(updated_at) u FROM items WHERE org_id=? AND status='active'",
               (org_id,))
    return f"{r['c']}|{r['u']}"


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
            parsed = [parse_name(n) for n in (r["name_en"], r["name_ar"]) if n]
            it = _IndexedItem(
                item_id=r["id"], label=r["name_en"] or r["name_ar"], parsed=parsed,
                strength=r["strength_norm"] or next((p.strength for p in parsed if p.strength), None),
                form=r["dosage_form"] or next((p.form for p in parsed if p.form), None),
                content=next((p.content for p in parsed if p.content), None),
                pack_count=next((p.pack_count for p in parsed if p.pack_count), None),
                modifiers=frozenset().union(*(p.modifiers for p in parsed)),
            ) if parsed else None
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


def forget(org_id: str) -> None:
    """Drop the index and the parsed names of a pharmacy (tests; a build from nothing)."""
    with _cache_lock:
        _cache.pop(org_id, None)
        _parsed.pop(org_id, None)


# ---------------------------------------------------------------------------
def _ratio(a: str, b: str) -> float:
    if not a or not b:
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


def fuzzy_candidates(db: Database, org_id: str, raw_name: str, limit: int = 5) -> list[Candidate]:
    idx = _index(db, org_id)
    key = (raw_name, limit)
    hit = idx.memo.get(key)
    if hit is None:
        q = parse_name(raw_name)
        scored = [score_item(q, idx.items[i]) for i in idx.candidates(q)]
        scored.sort(key=lambda c: c.score, reverse=True)
        hit = [c for c in scored if c.score >= SUGGEST_MIN * 0.6][:limit]
        if len(idx.memo) >= MEMO_MAX:              # bounded: a long import of new names must not grow it forever
            idx.memo.clear()
        idx.memo[key] = hit
    # copies: a caller may change what it gets without changing the remembered answer
    return [Candidate(c.item_id, c.label, c.score, list(c.reasons)) for c in hit]


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
