#!/usr/bin/env python3
"""Item-search latency at catalog scale (#461 QA-012). Synthetic data only, in a temporary folder.

  python scripts/perf/search_bench.py                 # both catalog shapes, 30,000 items + 500,000 archive rows
  python scripts/perf/search_bench.py --shape prefix --json out.json

Shapes: `prefix` = every name starts with the same word (the worst case for fuzzy matching, the catalog of
tests/test_u4_5_checkout.py); `varied` = generated brand names, strengths and forms, with Arabic names.
Every request goes through the HTTP app (TestClient), so routing, session and JSON are in the numbers.
Fuzzy queries are all different: nothing is answered from the per-query memo unless the row says "repeat".
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.catalog import matching, service as cat  # noqa: E402
from app.catalog.normalize import normalize_text  # noqa: E402
from app.dataimport import estock_archive  # noqa: E402
from app.db import new_id  # noqa: E402
from tests.test_owner_plan_priority_1_2 import PlanCase  # noqa: E402

ITEMS, ARCHIVE_ROWS = 30_000, 500_000
_SYL = ("pan", "dol", "cet", "am", "ox", "il", "vor", "tra", "max", "zen", "lor", "fen", "bru", "cal", "dex",
        "mox", "ator", "glu", "cor", "neo", "flu", "ket", "rin", "sol", "vit", "omep", "raz", "tor", "lin", "gab")
_AR = ("بان", "دول", "سيت", "ام", "وكس", "يل", "فور", "ترا", "ماكس", "زين", "لور", "فين", "برو", "كال", "ديكس",
       "موكس", "اتور", "جلو", "كور", "نيو", "فلو", "كيت", "رين", "سول", "فيت", "اوميب", "راز", "تور", "لين", "جاب")
_FORMS = ("tab", "cap", "syrup", "cream", "amp", "drops", "sachets", "supp")
_STRENGTHS = ("5mg", "10mg", "20mg", "25mg", "50mg", "100mg", "250mg", "500mg", "1g", "120ml")


def catalog(shape: str, rng: random.Random) -> list[tuple[str, str]]:
    """(English name, Arabic name) x ITEMS, unique."""
    if shape == "prefix":
        return [(f"Panacatalog Brand {n} 10mg tab", f"صنف صناعي {n}") for n in range(ITEMS)]
    out, seen = [], set()
    while len(out) < ITEMS:
        k = [rng.randrange(len(_SYL)) for _ in range(rng.choice((2, 3, 3, 4)))]
        en = "".join(_SYL[i] for i in k).capitalize()
        ar = "".join(_AR[i] for i in k)
        if rng.random() < 0.25:
            en, ar = f"{en} {rng.choice(('Plus', 'Forte', 'Extra', 'XR'))}", ar + " " + rng.choice(("بلس", "فورت", "اكسترا"))
        tail = f"{rng.choice(_STRENGTHS)} {rng.choice((10, 12, 20, 24, 30))} {rng.choice(_FORMS)}"
        name = f"{en} {tail}"
        if name in seen:
            continue
        seen.add(name)
        out.append((name, f"{ar} {tail}"))
    return out


def typo(word: str, rng: random.Random) -> str:
    """One wrong letter inside the word: no item name contains the result, so the search falls to fuzzy."""
    i = rng.randrange(1, len(word) - 1)
    return word[:i] + rng.choice([c for c in "qzxjkw" if c != word[i]]) + word[i + 1:]


def stats(ms: list[float]) -> dict:
    s = sorted(ms)
    return {"n": len(s), "median": round(statistics.median(s), 1), "p95": round(s[max(0, -(-len(s) * 95 // 100) - 1)], 1),
            "max": round(s[-1], 1)}


class Bench(PlanCase):
    shape = "prefix"
    n = 40
    out: dict = {}

    def timed(self, fn) -> float:
        t = time.perf_counter()
        fn()
        return (time.perf_counter() - t) * 1000

    def runTest(self):
        rng = random.Random(461)
        names = catalog(self.shape, rng)
        with self.db.transaction():
            self.db.conn.executemany(
                "INSERT INTO items (id, org_id, code, name_en, name_ar, name_norm, name_ar_norm,"
                " pack_size, public_price, status, dedupe_key) VALUES (?,?,?,?,?,?,?,1,1000,'active',?)",
                ((i, self.ctx.org_id, f"S{n}", en, ar, normalize_text(en), normalize_text(ar), i)
                 for n, (en, ar) in enumerate(names) for i in (new_id(),)))
        with closing(sqlite3.connect(self.tmp / estock_archive.NAME)) as archive, archive:
            archive.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            archive.execute("INSERT INTO meta VALUES ('org_id', ?)", (self.ctx.org_id,))
            archive.execute("CREATE TABLE e_sales_details (sales_id TEXT, product_id TEXT, amount TEXT)")
            archive.execute("INSERT INTO e_sales_details WITH RECURSIVE n(x) AS (VALUES(1)"
                            f" UNION ALL SELECT x+1 FROM n WHERE x<{ARCHIVE_ROWS}) SELECT x, x % 30000, '1' FROM n")
            archive.execute("CREATE INDEX e_sales_details_product ON e_sales_details(product_id)")
        matching.forget(self.ctx.org_id)
        self.addCleanup(matching.forget, self.ctx.org_id)
        res: dict = {}
        get = lambda url: self.call("GET", url)                                   # noqa: E731
        picks = [names[rng.randrange(ITEMS)] for _ in range(self.n)]
        words = [en.split()[0] for en, _ in picks]
        typos = [f"{typo(w, rng)} {en.split()[1]}" if self.shape == "prefix" else typo(w, rng)
                 for w, (en, _) in zip(words, picks)]

        res["index build from nothing (first search after a start, no warm-up)"] = {
            "n": 1, "once": round(self.timed(lambda: matching.prewarm(self.db)), 1)}
        res["POS exact name start"] = stats([self.timed(lambda w=w: get(f"/api/items?q={w[:5]}&stock=first&meta=1"))
                                             for w in words])
        res["POS full name"] = stats([self.timed(lambda en=en: get("/api/items?stock=first&meta=1&q=" + en))
                                      for en, _ in picks])
        res["POS Arabic name"] = stats([self.timed(lambda ar=ar: get("/api/items?stock=first&meta=1&q=" + ar.split()[0]
                                                                     + (" " + ar.split()[2] if self.shape == "prefix" else "")))
                                        for _, ar in picks])
        res["POS item code"] = stats([self.timed(lambda k=k: get(f"/api/items?q=S{k}&stock=first&meta=1"))
                                      for k in range(100, 100 + self.n)])
        first, kinds = [], []
        for q in typos:
            first.append(self.timed(lambda q=q: kinds.append(
                (get("/api/items?stock=first&meta=1&q=" + q)["hint"] or {}).get("kind"))))
        self.assertGreater(kinds.count("fuzzy"), len(typos) // 2, kinds)       # most typos must reach the fuzzy path
        res["POS typo, fuzzy, first time (warm index)"] = stats(first)
        res["POS typo, fuzzy, repeat"] = stats([self.timed(lambda q=q: get("/api/items?stock=first&meta=1&q=" + q))
                                                for q in typos])
        res["purchase item search, typo (no stock filter)"] = stats(
            [self.timed(lambda q=q: get("/api/items?q=" + q + "x")) for q in typos])

        # an item is edited (price change, new item): the next fuzzy search pays for the rebuild
        after = []
        for k, q in enumerate(typos[:10]):
            cat.update_item(self.db, self.ctx, self.pan, {"public_price": 4100 + k})
            after.append(self.timed(lambda q=q: get("/api/items?stock=first&meta=1&q=" + q + "y")))
        res["POS typo, fuzzy, right after an item was edited"] = stats(after)

        # purchase invoice: add a known item (the picker), a typed name, and open the invoice with its candidates
        sup = self.call("POST", "/api/suppliers", 201, json={"name": "مورد صناعي"})["id"]
        inv = self.call("POST", "/api/purchases", 201, json={"supplier_id": sup, "invoice_no": "B-1"})
        inv_id = inv["id"] if "id" in inv else inv["invoice"]["id"]
        ids = [r["id"] for r in self.db.all("SELECT id FROM items WHERE org_id=? AND code LIKE 'S%' LIMIT ?",
                                            (self.ctx.org_id, self.n))]
        ver = lambda: self.call("GET", f"/api/purchases/{inv_id}")["invoice"]["version"]      # noqa: E731
        picked, typed, opened = [], [], []
        for k, iid in enumerate(ids[:15]):
            v = ver()
            picked.append(self.timed(lambda: self.call("POST", f"/api/purchases/{inv_id}/lines", json={
                "line": {"item_id": iid, "qty": 1}, "picked": True, "version": v})))
        cat.update_item(self.db, self.ctx, self.pan, {"public_price": 4990})
        v = ver()
        res["purchase add known item, right after an item was edited"] = {"n": 1, "once": round(self.timed(
            lambda: self.call("POST", f"/api/purchases/{inv_id}/lines", json={
                "line": {"item_id": ids[20], "qty": 1}, "picked": True, "version": v})), 1)}
        for en, _ in picks[:15]:
            v = ver()
            typed.append(self.timed(lambda: self.call("POST", f"/api/purchases/{inv_id}/lines", json={
                "line": {"raw_name": en.replace("a", "e", 1), "qty": 1}, "version": v})))
        for _ in range(10):
            opened.append(self.timed(lambda: get(f"/api/purchases/{inv_id}")))
        res["purchase add known item (picker)"] = stats(picked)
        res["purchase add typed name (matched to the catalog)"] = stats(typed)
        res["purchase invoice open, 31 lines"] = stats(opened)
        type(self).out = res


def run(shape: str, n: int) -> dict:
    Bench.shape, Bench.n = shape, n
    result = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w") if sys.platform != "win32" else None
                                     ).run(unittest.TestSuite([Bench()]))
    if not result.wasSuccessful():
        for _, tb in result.errors + result.failures:
            print(tb, file=sys.stderr)
        raise SystemExit(1)
    return Bench.out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shape", choices=("prefix", "varied", "both"), default="both")
    ap.add_argument("-n", type=int, default=40, help="distinct queries per row")
    ap.add_argument("--json", help="write the numbers here too")
    a = ap.parse_args()
    out = {}
    for shape in (("prefix", "varied") if a.shape == "both" else (a.shape,)):
        out[shape] = run(shape, a.n)
        print(f"\n{shape} catalog: {ITEMS:,} items, {ARCHIVE_ROWS:,} archive rows (ms)")
        for k, v in out[shape].items():
            print(f"  {k:<62}" + (f"{v['once']:>9}" if "once" in v else
                                  f"median {v['median']:>8}  p95 {v['p95']:>8}  max {v['max']:>8}  (n={v['n']})"))
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
