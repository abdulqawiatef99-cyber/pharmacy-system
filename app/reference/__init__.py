"""Pharmacist reference data (read-only).

`pharmacist-reference.json` is the pharmacy's own clinical/operational reference, written in its own
words with a cited source per safety item. EDA (Egyptian Drug Authority) sources come first when sources
disagree. Nothing here activates by itself: safety rules still go through the safety engine's
draft -> approval flow, and the two EDA lists (OTC, do-not-crush) are machine-extracted previews to be
re-imported from the official PDFs before use. `pharmacist-reference.md` is the human-readable copy.

This module only loads and validates the file. It does not touch the database.
"""
from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

from ..catalog.normalize import FORM_GROUP

PATH = Path(__file__).with_name("pharmacist-reference.json")

SECTIONS = ("meta", "categories", "cross_sell", "depletion", "safety_alerts", "pattern_alerts",
            "protocols", "supplements", "care", "counseling", "storage", "high_value",
            "program_features", "egypt_do_not_crush", "egypt_otc")
ALERT_TYPES = {"interaction", "spacing", "duplicate_ingredient", "duplicate_class", "age", "pregnancy",
               "breastfeeding", "condition", "dose_limit", "counsel"}
# alert types the current safety engine can run without new code (see app/safety/engine.py)
ENGINE_READY = {"interaction", "duplicate_ingredient"}
FORMS = set(FORM_GROUP) | set(FORM_GROUP.values())


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def validate(data: dict) -> list[str]:
    """Return a list of problems; empty means the file is consistent."""
    errs: list[str] = []
    missing = [s for s in SECTIONS if s not in data]
    if missing:
        return [f"missing sections: {missing}"]
    meta = data["meta"]
    sources = {s["id"] for s in meta.get("sources", [])}
    evidence = set(meta.get("evidence_scale", {}))
    severity = set(meta.get("severity_scale", {}))
    cats = data["categories"]
    for k, n in Counter(c["id"] for c in cats).items():
        if n > 1:
            errs.append(f"duplicate category {k}")
    known = {c["id"] for c in cats}
    for c in cats:
        if not c.get("match") and not c.get("name_kw"):
            errs.append(f"category {c['id']} has no match rule")
        for f in c.get("forms", []):
            if f not in FORMS:
                errs.append(f"category {c['id']}: unknown form {f}")

    def refs(where: str, ids) -> None:
        for i in ids or []:
            if i not in known:
                errs.append(f"{where}: unknown category {i}")

    entries = []
    for r in data["cross_sell"]:
        entries.append(r["id"])
        refs(r["id"], r["trigger"])
        for s in r["suggest"]:
            refs(r["id"], [s["cat"]])
            if s["evidence"] not in evidence:
                errs.append(f"{r['id']}: bad evidence {s['evidence']}")
    for r in data["depletion"]:
        entries.append(r["id"])
        refs(r["id"], r["drug"] + [r["nutrient"]])
    for r in data["safety_alerts"]:
        entries.append(r["id"])
        refs(r["id"], r["a"] + r.get("b", []))
        if r["type"] not in ALERT_TYPES:
            errs.append(f"{r['id']}: bad type {r['type']}")
        if r["severity"] not in severity:
            errs.append(f"{r['id']}: bad severity {r['severity']}")
        if not r.get("src") or any(s not in sources for s in r["src"]):
            errs.append(f"{r['id']}: missing or unknown source")
        ready = not r["engine"].startswith("new:")
        if ready != (r["type"] in ENGINE_READY):
            errs.append(f"{r['id']}: engine flag does not match type {r['type']}")
    for r in data["pattern_alerts"]:
        entries.append(r["id"])
        refs(r["id"], r["cats"] + r.get("requires_history", []))
    for p in data["protocols"]:
        entries.append(p["id"])
        refs(p["id"], [o["cat"] for o in p["opt"]])
    for s in data["supplements"]:
        entries.append(s["id"])
        refs(s["id"], [s["cat"]])
    for r in data["care"]["routines"]:
        entries.append(r["id"])
        for step in r.get("am", []) + r.get("pm", []) + r.get("weekly", []):
            refs(r["id"], step["cats"])
    for key in ("counseling", "storage"):
        for r in data[key]:
            entries.append(r["id"])
            refs(r["id"], r["cats"])
    entries += [r["id"] for r in data["high_value"]] + [r["id"] for r in data["program_features"]]
    cs_ids = {r["id"] for r in data["cross_sell"]}
    for p in data["protocols"]:
        for c in p.get("cs", []):
            if c not in cs_ids:
                errs.append(f"{p['id']}: unknown cross-sell {c}")
    for key in ("egypt_do_not_crush", "egypt_otc"):
        sec = data[key]
        if sec.get("source") not in sources:
            errs.append(f"{key}: unknown source")
        entries += [x["id"] for x in sec["items"]]
    for k, n in Counter(entries).items():
        if n > 1:
            errs.append(f"duplicate entry id {k}")
    return errs


def egypt_sources(data: dict | None = None) -> list[dict]:
    data = data or load()
    return [s for s in data["meta"]["sources"] if s.get("egypt")]
