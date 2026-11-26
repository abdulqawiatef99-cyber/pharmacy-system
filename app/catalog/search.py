"""Item search for the cashier, purchasing and everywhere else.

- English or Arabic name, internal code, barcode.
- Pieces of the name in any order: "asp 75" or "اسبو 75".
- Arabic spelling differences ignored (أ/إ/ا، ة/ه، ى/ي) by normalize_text.
- "%" in place of one or more letters you are unsure of: "asp%cid".
- Wrong keyboard language: "شسحخؤهي" (typed with the Arabic layout) also searches
  "aspocid", and the other way round. The caller is told so it can show it.
"""
from __future__ import annotations

import re

from ..core.permissions import Context
from ..db import Database
from . import gs1
from .normalize import normalize_barcode, normalize_text, spelling_key, spelling_sql

# Windows Arabic (101) layout: English key -> Arabic character
_EN_AR = {
    "q": "ض", "w": "ص", "e": "ث", "r": "ق", "t": "ف", "y": "غ", "u": "ع", "i": "ه", "o": "خ", "p": "ح",
    "[": "ج", "]": "د", "a": "ش", "s": "س", "d": "ي", "f": "ب", "g": "ل", "h": "ا", "j": "ت", "k": "ن",
    "l": "م", ";": "ك", "'": "ط", "z": "ئ", "x": "ء", "c": "ؤ", "v": "ر", "b": "لا", "n": "ى", "m": "ة",
    ",": "و", ".": "ز", "/": "ظ", "`": "ذ",
}
_AR_EN = {v: k for k, v in _EN_AR.items() if len(v) == 1}
_AR_EN.update({"ﻻ": "b", "ﻷ": "b", "ﻹ": "b", "ﻵ": "b", "أ": "h", "إ": "h", "آ": "h"})
FUZZY_MAX = 160
_ARABIC = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[a-z]", re.I)


def swap_layout(q: str) -> str | None:
    """The same keys typed with the other keyboard language, or None if mixed/not letters."""
    s = q.strip()
    if not s:
        return None
    if _ARABIC.search(s) and not _LATIN.search(s):
        out = "".join(_AR_EN.get(ch, ch) for ch in s)
        return out if _LATIN.search(out) else None
    if _LATIN.search(s) and not _ARABIC.search(s):
        return "".join(_EN_AR.get(ch, ch) for ch in s.lower())
    return None


def _patterns(q: str) -> list[str]:
    toks = []
    for t in normalize_text(q).replace("%", " % ").split():
        toks.append(t)
    # glue "%" back to its neighbours: "asp % cid" -> "asp%cid"
    glued = " ".join(toks).replace(" % ", "%").replace("% ", "%").replace(" %", "%")
    return [f"%{t.strip('%')}%" if t.strip("%") else "%" for t in glued.split() if t.strip("%")]


def _cat_sql(category: str | None) -> tuple[str, tuple]:
    if not category:
        return "", ()
    if category == "none":
        return " AND category_id IS NULL", ()
    return " AND category_id=?", (category,)


def _by_name(db: Database, org_id: str, q: str, limit: int, category: str | None = None) -> list[dict]:
    pats = _patterns(q)
    if not pats:
        return []
    csql, cparams = _cat_sql(category)
    where = " AND ".join("(name_norm LIKE ? OR coalesce(name_ar_norm,'') LIKE ?)" for _ in pats)
    params: list = []
    for p in pats:
        params += [p, p]
    first = pats[0].lstrip("%")
    # the name typed in full comes first, then names that start with what was typed: on the sale screen
    # Enter adds the first row, so «صنف ب» typed in full must not offer «صنف أ» above it
    whole = normalize_text(q)
    rows = db.all(f"SELECT * FROM items WHERE org_id=? AND status='active' AND {where}{csql}"
                  " ORDER BY (name_norm=? OR coalesce(name_ar_norm,'')=?) DESC,"
                  " (substr(name_norm,1,length(?))=? OR substr(coalesce(name_ar_norm,''),1,length(?))=?) DESC,"
                  " (name_norm LIKE ? OR coalesce(name_ar_norm,'') LIKE ?) DESC, length(name_norm)"
                  " LIMIT ?", (org_id, *params, *cparams, whole, whole, whole, whole, whole, whole, first, first, limit))
    return [dict(r) for r in rows]


def _by_spelling(db: Database, org_id: str, q: str, limit: int, category: str | None, skip: set) -> list[dict]:
    """Arabic spelling variants (update 1): بكتول / بيكتول, ى / ي, ة / ه, every word of the query in the name's
    spelling key (`spelling_key` / `spelling_sql`); only after the exact patterns found too few."""
    words = normalize_text(q).split()
    ar = [spelling_key(w) for w in words if _ARABIC.search(w)]
    ar = [w for w in ar if w]
    if not ar:
        return []
    other = [w for w in words if not _ARABIC.search(w)]          # a strength or a Latin word stays required (Codex #400)
    csql, cparams = _cat_sql(category)
    col = spelling_sql("coalesce(name_ar_norm,'')")
    # the spelling key of a column is ten replace() calls a row: on 30,000 names that alone was 60 ms (#461).
    # Cheap tests first, so it runs only on rows that can match: the other words, then the letters of each
    # Arabic word in their order with anything between them (what dropping the long vowels leaves).
    where = " AND ".join(["(name_norm LIKE ? OR coalesce(name_ar_norm,'') LIKE ?)"] * len(other)
                         + ["name_ar_norm LIKE ?"] * len(ar) + [f"{col} LIKE ?"] * len(ar))
    params = []
    for w in other:
        params += [f"%{w}%", f"%{w}%"]
    params += ["%" + "%".join(t) + "%" for t in ar] + [f"%{t}%" for t in ar]
    rows = db.all(f"SELECT * FROM items WHERE org_id=? AND status='active' AND {where}{csql}"
                  " ORDER BY length(coalesce(name_ar_norm,'')) LIMIT ?",
                  (org_id, *params, *cparams, limit + len(skip)))
    return [dict(r) for r in rows if r["id"] not in skip]


def _exact_code(db: Database, org_id: str, q: str) -> list[dict]:
    """The code typed in full comes first (launch day: «3796» found nothing): the item code as typed or in any
    letter case, and E<code> (an E-Stock item moved without a code of its own gets E + its E-Stock id)."""
    # read from the code index alone (a third of the time of reading every item row, #461); an item without
    # a code cannot match
    return [dict(r) for r in db.all("SELECT * FROM items INDEXED BY ux_items_org_code WHERE org_id=? AND code IS NOT NULL"
                                    " AND (lower(code)=lower(?) OR code=?) AND status='active'"
                                    " ORDER BY code=? DESC", (org_id, q, f"E{q}", q))]


def search(db: Database, ctx: Context, q: str, limit: int = 20, category: str | None = None) -> dict:
    """{'items': [...], 'hint': None | {'kind': 'layout', 'query': 'aspocid'}}.
    Hidden items are left out for people without items.hidden."""
    r = _search(db, ctx, q, limit, category)
    if not ctx.can("items.hidden"):
        r["items"] = [i for i in r["items"] if not i.get("hidden")]
    return r


def _search(db: Database, ctx: Context, q: str, limit: int = 20, category: str | None = None) -> dict:
    ctx.require("items.read")
    q = (q or "").strip()[:100]
    if not q:
        csql, cparams = _cat_sql(category)
        rows = db.all(f"SELECT * FROM items WHERE org_id=? AND status='active'{csql}"
                      " ORDER BY coalesce(name_en, name_ar) LIMIT ?", (ctx.org_id, *cparams, limit))
        return {"items": [dict(r) for r in rows], "hint": None}
    code = gs1.parse(q)
    if code:                                     # 2D code on the pack: GTIN + expiry + batch
        keys = gs1.gtin_keys(code["gtin"])
        rows = db.all(f"SELECT DISTINCT i.* FROM item_barcodes b JOIN items i ON i.id=b.item_id WHERE b.org_id=?"
                      f" AND b.barcode IN ({','.join('?' * len(keys))})", (ctx.org_id, *keys))
        return {"items": [dict(r) for r in rows], "hint": {"kind": "gs1", **code}}
    bc = normalize_barcode(q)
    if bc and len(bc) >= 6 and bc == q:
        keys = gs1.gtin_keys(bc)
        r = db.one(f"SELECT i.* FROM item_barcodes b JOIN items i ON i.id=b.item_id"
                   f" WHERE b.org_id=? AND b.barcode IN ({','.join('?' * len(keys))})", (ctx.org_id, *keys))
        if r:
            return {"items": [dict(r)], "hint": None}
    codes = _exact_code(db, ctx.org_id, q)
    if re.fullmatch(r"[0-9]{3,5}", q) and codes and codes[0]["code"] == q:
        # Printed short codes are exact identifiers, even if another item's name contains the digits.
        return {"items": [codes[0]], "hint": None}
    found: dict[str, dict] = {}
    for r in codes:
        found.setdefault(r["id"], r)
    for row in _by_name(db, ctx.org_id, q, limit, category):
        found.setdefault(row["id"], row)
    hint = None
    if len(found) < limit and "%" not in q:
        exact = bool(found)
        for row in _by_spelling(db, ctx.org_id, q, limit - len(found), category, set(found)):
            row["match"] = "spelling"            # per row: the hint is decided again after a stock filter (Codex #400)
            found.setdefault(row["id"], row)
        if found and not exact:
            hint = {"kind": "spelling"}          # a guess by spelling: Enter on the sale screen never adds it alone
    if not found:
        alt = swap_layout(q)
        if alt:
            for row in _by_name(db, ctx.org_id, alt, limit, category):
                found.setdefault(row["id"], row)
            if found:
                hint = {"kind": "layout", "query": alt}
    if not found and "%" not in q and not category:
        from . import matching
        # guesses: the sale screen asks for 500 rows to sort and filter by stock. Every guess that was compared
        # is handed over for that (FUZZY_MAX at most, so an in-stock guess is not cut before its balance is
        # read, Codex on #483), and they are read in one query (500 single reads cost more than the search)
        ids = [c.item_id for c in matching.fuzzy_candidates(db, ctx.org_id, q, min(limit, FUZZY_MAX),
                                                            compare=FUZZY_MAX)]
        rows = {r["id"]: dict(r) for r in db.all(
            f"SELECT * FROM items WHERE org_id=? AND id IN ({','.join('?' * len(ids))})", (ctx.org_id, *ids))} if ids else {}
        for i in ids:
            if i in rows:
                found.setdefault(i, rows[i])
        if found:
            hint = {"kind": "fuzzy"}
    return {"items": list(found.values())[:limit], "hint": hint}


def backfill_search_keys(db: Database) -> int:
    """Fill name_ar_norm for items saved before Arabic search existed (runs once, fast)."""
    rows = db.all("SELECT id, name_ar FROM items WHERE name_ar_norm IS NULL AND name_ar IS NOT NULL"
                  " AND name_ar <> ''")
    if not rows:
        return 0
    with db.transaction():
        for r in rows:
            db.execute("UPDATE items SET name_ar_norm=? WHERE id=?", (normalize_text(r["name_ar"]), r["id"]))
    return len(rows)
