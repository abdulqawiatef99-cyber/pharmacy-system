"""Extract structured product rows from the three supplier PDFs.

Every output row keeps provenance (file, page, column, y position, raw visual text).
Lines that cannot be parsed are never dropped: they go to the issues file.

Usage: python -I scripts/extract_pdfs.py <originals_dir> <output_dir>
"""
import csv
import json
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textfix import fix_visual_arabic  # noqa: E402

NUM = re.compile(r"^\d+(?:\.\d+)?$")
HEADER_WORDS = {"رعسلا", "رﻌﺳﻟا"}

# Zones are offsets from the x of each column's "السعر" header word.
SOURCES = {
    "nahda": {
        "file": "nahda_اوردر.pdf",
        "supplier": "Al Nahda",
        "kind": "discount",          # price = public price, second number = discount %
        "price": (-8, 35), "second": (35, 62), "code": None,
    },
    "ibn_khaldun": {
        "file": "ibn_khaldun_اوردر_شركات.pdf",
        "supplier": "Ibn Khaldun",
        "kind": "discount",
        "price": (-8, 40), "second": (40, 85), "code": None,
    },
    "future": {
        "file": "future_تحديث_فيوتشر_كبار_عملاء_31-10.pdf",
        "supplier": "Future",
        "kind": "net",               # first number = pharmacy price, second = public price
        "price": (-8, 30), "second": (30, 72), "code": 225,
    },
}
FOOTER_MARKERS = ("ﺔﻛرﺷ", "ةكرش", "Page", "ةعابطلا", "0222562089")


def cluster_rows(words, tol=3.5):
    rows, cur, top = [], [], None
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if top is None or w["top"] - top <= tol:
            cur.append(w)
            top = w["top"] if top is None else top
        else:
            rows.append(cur)
            cur, top = [w], w["top"]
    if cur:
        rows.append(cur)
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def parse_page(pg, cfg, page_no):
    words = pg.extract_words(keep_blank_chars=False)
    headers = sorted((w for w in words if w["text"] in HEADER_WORDS), key=lambda w: w["x0"])
    if not headers:
        return [], [{"page": page_no, "issue": "no_header_found"}]
    header_top = max(h["bottom"] for h in headers)
    footer_top = min([w["top"] for w in words if any(m in w["text"] for m in FOOTER_MARKERS)
                      and w["top"] > pg.height - 80] or [pg.height])
    hx = [h["x0"] for h in headers]
    bounds = [(hx[i] - 8, (hx[i + 1] - 8) if i + 1 < len(hx) else pg.width) for i in range(len(hx))]
    records, issues = [], []
    for col, (x0, x1) in enumerate(bounds):
        base = hx[col]
        band = [w for w in words if x0 <= w["x0"] < x1 and header_top < w["top"] < footer_top - 1]
        last = None
        for row in cluster_rows(band):
            price = second = code = None
            name_words = []
            for w in row:
                rel = w["x0"] - base
                t = w["text"]
                if NUM.match(t) and cfg["price"][0] <= rel < cfg["price"][1] and price is None:
                    price = t
                elif NUM.match(t) and cfg["second"][0] <= rel < cfg["second"][1] and second is None:
                    second = t
                elif cfg["code"] is not None and NUM.match(t) and rel >= cfg["code"] and code is None:
                    code = t
                else:
                    name_words.append(t)
            raw = " ".join(w["text"] for w in row)
            name = fix_visual_arabic(" ".join(name_words))
            top = round(row[0]["top"], 1)
            if price is None and second is None and code is None:
                if last is not None and top - last["_top"] < 16:
                    last["name"] = f'{last["name"]} {name}'.strip()
                    last["raw_text"] += " || " + raw
                    last["notes"] = (last["notes"] + ";" if last["notes"] else "") + "joined_continuation_line"
                    last["_top"] = top
                else:
                    issues.append({"page": page_no, "column": col + 1, "top": top,
                                   "issue": "orphan_text_line", "raw_text": raw, "text": name})
                continue
            rec = {"page": page_no, "column": col + 1, "top": top, "_top": top,
                   "code": code or "", "name": name, "price": price or "", "second": second or "",
                   "raw_text": raw, "notes": ""}
            problems = []
            if price is None:
                problems.append("missing_price")
            if second is None:
                problems.append("missing_second_number")
            if cfg["code"] is not None and code is None:
                problems.append("missing_code")
            if not name:
                problems.append("missing_name")
            rec["notes"] = ";".join(problems)
            records.append(rec)
            last = rec
            if problems:
                issues.append({"page": page_no, "column": col + 1, "top": top,
                               "issue": ";".join(problems), "raw_text": raw, "text": name})
    return records, issues


def main(orig_dir, out_dir):
    orig_dir, out_dir = Path(orig_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = {}
    for key, cfg in SOURCES.items():
        recs, issues = [], []
        with pdfplumber.open(orig_dir / cfg["file"]) as pdf:
            for i, pg in enumerate(pdf.pages, start=1):
                r, iss = parse_page(pg, cfg, i)
                recs += r
                issues += iss
        for n, r in enumerate(recs, start=1):
            r["source_row"] = f"{key}:p{r['page']}:c{r['column']}:{n}"
            r.pop("_top", None)
        fields = ["source_row", "page", "column", "top", "code", "name", "price", "second", "notes", "raw_text"]
        with open(out_dir / f"pdf_{key}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(recs)
        with open(out_dir / f"pdf_{key}_issues.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["page", "column", "top", "issue", "raw_text", "text"])
            w.writeheader()
            w.writerows(issues)
        metrics[key] = {"pages": i, "records": len(recs), "issues": len(issues),
                        "continuations_joined": sum("joined_continuation_line" in r["notes"] for r in recs)}
    (out_dir / "pdf_extract_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
