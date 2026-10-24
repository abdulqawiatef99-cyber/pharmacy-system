#!/usr/bin/env python3
"""Measure invoice-reading accuracy on REAL supplier files.

Folder layout (keep it OUTSIDE the git repository — real invoices are private):
    real_invoices/
        ibn_sina_001.xlsx
        ibn_sina_001.expected.json
        photo_017.jpg
        photo_017.expected.json

expected.json (typed by a person from the paper invoice):
    {"invoice_no": "77881", "invoice_date": "2026-10-19", "declared_total": "705.20",
     "lines": [{"name": "Panadol Extra 24 tab", "qty": 10, "bonus_qty": 2,
                "unit_net_price": "45.92", "line_total": "459.20",
                "batch_no": "PX9", "expiry": "05/2028"}]}

Runs EXTRACTION ONLY (no database, nothing is imported). Photos/scanned PDFs use
the configured provider (needs ANTHROPIC_API_KEY) unless --no-ai.

    python scripts/eval_invoices.py real_invoices/ [--no-ai] [--json report.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.catalog.normalize import normalize_text  # noqa: E402
from app.purchasing.importers import files  # noqa: E402
from app.purchasing.model import parse_expiry  # noqa: E402

LINE_FIELDS = ("qty", "bonus_qty", "unit_net_price", "line_total", "batch_no", "expiry")


def _eq(field: str, got, exp) -> bool:
    if exp is None:
        return True                      # not specified in the expected file
    if field in ("unit_net_price", "line_total", "declared_total"):
        return got is not None and abs(Decimal(str(got)) - Decimal(str(exp))) <= Decimal("0.01")
    if field == "expiry":
        return got == parse_expiry(exp)[0]
    if field == "batch_no":
        return (got or "").strip().upper() == str(exp).strip().upper()
    return got == exp


def evaluate_file(path: Path, expected: dict, use_ai: bool) -> dict:
    data = path.read_bytes()
    kind, mime = files.sniff(data, path.name)
    ex = files.extract_structured(kind, data)
    if ex is None:
        if not use_ai:
            return {"file": path.name, "skipped": "needs AI provider"}
        from app.purchasing.extractors import from_settings
        ex = from_settings(config.load()).extract(data, mime)
    res = {"file": path.name, "method": ex.method, "fields": {}, "lines_expected": len(expected.get("lines", [])),
           "lines_found": len(ex.lines), "low_confidence_flags": 0, "wrong_but_confident": 0, "usage": ex.usage}
    for f in ("invoice_no", "invoice_date", "declared_total"):
        if f in expected:
            got = getattr(ex, f)
            res["fields"][f] = _eq(f, str(got) if f == "invoice_no" and got else got, expected[f])
    # pair lines by order, fall back to name similarity
    remaining = list(ex.lines)
    correct = total = 0
    for e in expected.get("lines", []):
        key = normalize_text(e.get("name", ""))
        line = next((l for l in remaining if normalize_text(l.raw_name) == key), remaining[0] if remaining else None)
        if line is None:
            total += sum(1 for f in LINE_FIELDS if f in e)
            continue
        remaining.remove(line)
        for f in LINE_FIELDS:
            if f not in e:
                continue
            total += 1
            got = line.expiry_date if f == "expiry" else getattr(line, f)
            ok = _eq(f, got, e[f])
            correct += ok
            conf = line.confidence.get("expiry_date" if f == "expiry" else f, 1.0)
            if conf < 0.8:
                res["low_confidence_flags"] += 1
            elif not ok:
                res["wrong_but_confident"] += 1      # the dangerous category
    res["line_field_accuracy"] = round(correct / total, 4) if total else None
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    results = []
    for exp_file in sorted(Path(a.folder).glob("*.expected.json")):
        stem = exp_file.name[: -len(".expected.json")]
        src = next((p for p in exp_file.parent.glob(stem + ".*") if not p.name.endswith(".expected.json")), None)
        if not src:
            results.append({"file": stem, "error": "source file missing"})
            continue
        try:
            results.append(evaluate_file(src, json.loads(exp_file.read_text(encoding="utf-8")), not a.no_ai))
        except Exception as e:  # report and continue
            results.append({"file": src.name, "error": f"{type(e).__name__}: {e}"})
    scored = [r for r in results if r.get("line_field_accuracy") is not None]
    summary = {"files": len(results), "scored": len(scored),
               "mean_line_field_accuracy": round(sum(r["line_field_accuracy"] for r in scored) / len(scored), 4) if scored else None,
               "wrong_but_confident": sum(r.get("wrong_but_confident", 0) for r in results)}
    for r in results:
        print(json.dumps(r, ensure_ascii=False, default=str))
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False,
                                           indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    main()
