"""Supplier / distributor price lists (Excel or CSV) into the reference catalog (plan stage M4).

Any file with recognisable columns (same header words as the data import: name, barcode, public price,
company, active ingredient…) becomes a reference source named "file:<label>", for example
"file:ibn-sina". Non-drug products (cosmetics, supplements, supplies) need no pharmaceutical fields.
Like every reference source it never edits the pharmacy's items; linking and prices need approval.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..core.errors import ValidationFailed
from ..dataimport.service import _detect, _read
from ..db import Database
from ..purchasing.model import parse_decimal, to_piasters
from . import reference as ref
from .normalize import ingredient_key, normalize_barcode, normalize_form, normalize_text, parse_name, strength_norm

MAX_ROWS = 100_000


def source_name(label: str) -> str:
    slug = re.sub(r"[^\w-]+", "-", normalize_text(label)).strip("-")[:40]
    if not slug:
        raise ValidationFailed("اكتب اسم المصدر (مثال: ابن سينا)")
    return "file:" + slug


def _cell(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0", s):          # Excel number -> text
        s = s[:-2]
    return s or None


def parse_rows(rows: list[list]) -> tuple[list[dict], list[dict]]:
    det = _detect(rows, "items")
    if not det:
        raise ValidationFailed("مش لاقي أعمدة معروفة في الملف (الاسم، الباركود، السعر…)")
    hdr, cmap = det
    out, rejected = [], []
    for n, row in enumerate(rows[hdr + 1:hdr + 1 + MAX_ROWS], start=hdr + 2):
        raw = {f: _cell(row[j]) if j < len(row) else None for j, f in cmap.items()}
        if not any(raw.values()):
            continue
        name = raw.get("name_en") or raw.get("name_ar")
        if not name:
            rejected.append({"row": n, "reason": "من غير اسم"})
            continue
        price = to_piasters(parse_decimal(raw["public_price"])) if raw.get("public_price") else None
        if raw.get("public_price") and price is None:
            rejected.append({"row": n, "reason": f"سعر غير صالح: {raw.get('public_price')}"})
            continue
        if price is not None and price < 0:
            rejected.append({"row": n, "reason": "سعر بالسالب"})
            continue
        bc = normalize_barcode(raw.get("barcode"))
        bc = bc if bc and len(bc) >= 6 else None
        parsed = parse_name(name)
        pack = None
        if raw.get("pack_size") and str(raw["pack_size"]).isdigit() and int(raw["pack_size"]) >= 1:
            pack = int(raw["pack_size"])
        # the file's own code is the stable id; else barcode; else the name itself (normalized)
        sid = raw.get("code") or bc or hashlib.sha1(normalize_text(name).encode()).hexdigest()[:16]
        out.append({
            "source_id": sid, "name_en": raw.get("name_en"), "name_ar": raw.get("name_ar"),
            "name_norm": normalize_text(name), "scientific_name": raw.get("active_ingredient"),
            "ingredient_key": ingredient_key(raw.get("active_ingredient")),
            "strength": raw.get("strength"),
            "strength_norm": strength_norm(raw.get("strength")) or parsed.strength,
            "dosage_form": normalize_form(raw.get("dosage_form")) or parsed.form,
            "units_per_package": pack or parsed.pack_count,
            "company": raw.get("manufacturer"),
            "barcodes": [bc] if bc else [],
            "ingredients": ([{"position": 0, "name_en": raw["active_ingredient"], "name_ar": None,
                              "strength": raw.get("strength"), "atc_code": None}]
                            if raw.get("active_ingredient") else []),
            "price": None if price is None else {"price": price, "currency": "EGP", "effective_date": None,
                                                 "source_recorded_at": None, "verified": 0},
            "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "_row": n,
        })
    return out, rejected


def import_file(db: Database, data: bytes, filename: str, label: str, observed_at: str | None = None) -> dict:
    source = source_name(label)
    rows = _read(data, filename, "items")
    records, rejected = parse_rows(rows)
    observed_at = observed_at or ref.now_iso()
    rep = {"source": source, "seen": len(records) + len(rejected), "created": 0, "updated": 0, "unchanged": 0,
           "prices_added": 0, "no_price": 0, "duplicate_ids": 0, "rejected": rejected}
    seen: set[str] = set()
    for r in records:
        if r["source_id"] in seen:
            rep["duplicate_ids"] += 1
            rep["rejected"].append({"row": r["_row"], "reason": f"كود مكرر في الملف: {r['source_id']}"})
            continue
        seen.add(r["source_id"])
        res = ref.upsert_parsed(db, source, {k: v for k, v in r.items() if k != "_row"}, observed_at)
        rep[res["status"]] += 1
        rep["prices_added"] += res["price_added"]
        rep["no_price"] += r["price"] is None
    rep["summary"] = ref.summary(db, source)
    return rep
