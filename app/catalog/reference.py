"""External reference catalog (docs/CATALOG_DATA_PLAN.md, stage M1).

Copies drug records from an outside source into the platform-wide ref_* tables.
First source: Dwaprice (https://dwaprice.com/api/docs, v1).

Rules (tested in tests/test_reference_catalog.py):
- Never touches a pharmacy's items, prices, stock or invoices.
- Re-importing the same data changes nothing (idempotent); a price change adds a history row.
- A missing price is no row, never zero. A barcode may belong to several packs.
- Change events can arrive twice (at-least-once delivery): an event id is applied once.
- The API key lives in an environment variable on the machine that syncs; it is never stored in
  the database or sent in sample mode. The licence forbids sharing one key across pharmacies.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Iterable, Iterator

from ..db import Database, new_id
from .normalize import ingredient_key, normalize_barcode, normalize_text, strength_norm

SOURCE = "dwaprice"
COUNTRY = "EG"
DEFAULT_BASE_URL = "https://api.dwaprice.com/v1"
PAGE_LIMIT = 100
MAX_RETRY_WAIT = 120


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _piasters(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        p = (Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"سعر غير صالح: {value!r}")
    if p < 0:
        raise ValueError(f"سعر بالسالب: {value!r}")
    return int(p)


def _s(v: Any) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def parse_drug(d: Any) -> dict:
    """One Dwaprice Drug object -> our columns. Raises ValueError on a record we must not store."""
    if not isinstance(d, dict):
        raise ValueError("السجل مش كائن")
    sid = _s(d.get("id"))
    if not sid:
        raise ValueError("السجل من غير معرّف")
    name_en, name_ar = _s(d.get("trade_name_en")), _s(d.get("trade_name_ar"))
    if not (name_en or name_ar):
        raise ValueError("السجل من غير اسم")
    form = d.get("dosage_form") or {}
    price = d.get("price") or {}
    ings = []
    for i, ing in enumerate(d.get("ingredients") or []):
        if isinstance(ing, dict) and (_s(ing.get("name_en")) or _s(ing.get("name_ar"))):
            ings.append({"position": i, "name_en": _s(ing.get("name_en")), "name_ar": _s(ing.get("name_ar")),
                         "strength": _s(ing.get("strength")), "atc_code": _s(ing.get("atc_code"))})
    ing_text = " + ".join(x["name_en"] or x["name_ar"] for x in ings) or _s(d.get("scientific_name"))
    units = d.get("units_per_package")
    units = units if isinstance(units, int) and not isinstance(units, bool) and units >= 1 else None
    company, maker = d.get("company") or {}, d.get("manufacturer") or {}
    bc = normalize_barcode(_s(d.get("barcode")))
    return {
        "source_id": sid, "name_en": name_en, "name_ar": name_ar,
        "name_norm": normalize_text(name_en or name_ar),
        "scientific_name": _s(d.get("scientific_name")),
        "ingredient_key": ingredient_key(ing_text),
        "strength": _s(d.get("strength")), "strength_norm": strength_norm(_s(d.get("strength"))),
        "dosage_form": _s(form.get("class")) if isinstance(form, dict) else None,
        "route": _s(d.get("route")), "units_per_package": units, "packaging": _s(d.get("packaging")),
        "company": _s(company.get("name")) if isinstance(company, dict) else None,
        "manufacturer": _s(maker.get("name")) if isinstance(maker, dict) else None,
        "prescription_status": _s(d.get("prescription_status")), "listing_status": _s(d.get("status")),
        "barcodes": [bc] if bc and len(bc) >= 6 else [],
        "ingredients": ings,
        "price": None if not isinstance(price, dict) else {
            "price": _piasters(price.get("current")), "currency": _s(price.get("currency")) or "EGP",
            "effective_date": _s(price.get("effective_date")),
            "source_recorded_at": _s(price.get("updated_at")),
            "verified": 1 if price.get("verified") is True else 0},
        "raw_json": json.dumps(d, ensure_ascii=False, sort_keys=True),
    }


_COLS = ("name_en", "name_ar", "name_norm", "scientific_name", "ingredient_key", "strength", "strength_norm",
         "dosage_form", "route", "units_per_package", "packaging", "company", "manufacturer",
         "prescription_status", "listing_status")


def upsert_drug(db: Database, d: Any, observed_at: str, origin: str = "catalog") -> dict:
    """Store one record. Returns {'status': created|updated|unchanged, 'price_added': bool, 'ref_id'}."""
    p = parse_drug(d)
    with db.transaction():
        row = db.one("SELECT id, raw_json FROM ref_products WHERE source=? AND country_code=? AND source_id=?",
                     (SOURCE, COUNTRY, p["source_id"]))
        if row is None:
            ref_id, status = new_id(), "created"
            db.execute(f"INSERT INTO ref_products (id, source, country_code, source_id, {', '.join(_COLS)},"
                       f" raw_json, first_seen_at, last_seen_at) VALUES (?,?,?,?,{','.join('?' * len(_COLS))},?,?,?)",
                       (ref_id, SOURCE, COUNTRY, p["source_id"], *[p[c] for c in _COLS],
                        p["raw_json"], observed_at, observed_at))
        elif row["raw_json"] == p["raw_json"]:
            ref_id, status = row["id"], "unchanged"
            db.execute("UPDATE ref_products SET last_seen_at=?, removed_at=NULL WHERE id=?", (observed_at, ref_id))
        else:
            ref_id, status = row["id"], "updated"
            db.execute(f"UPDATE ref_products SET {', '.join(c + '=?' for c in _COLS)}, raw_json=?, last_seen_at=?,"
                       " removed_at=NULL WHERE id=?", (*[p[c] for c in _COLS], p["raw_json"], observed_at, ref_id))
        if status != "unchanged":
            db.execute("DELETE FROM ref_barcodes WHERE ref_id=?", (ref_id,))
            for bc in p["barcodes"]:
                db.execute("INSERT OR IGNORE INTO ref_barcodes (ref_id, barcode) VALUES (?,?)", (ref_id, bc))
            db.execute("DELETE FROM ref_ingredients WHERE ref_id=?", (ref_id,))
            for ing in p["ingredients"]:
                db.execute("INSERT INTO ref_ingredients (ref_id, position, name_en, name_ar, strength, atc_code)"
                           " VALUES (?,?,?,?,?,?)", (ref_id, ing["position"], ing["name_en"], ing["name_ar"],
                                                     ing["strength"], ing["atc_code"]))
        added = False
        pr = p["price"]
        if pr and pr["price"] is not None:
            cur = db.execute("INSERT OR IGNORE INTO ref_prices (id, ref_id, price, currency, effective_date,"
                             " source_recorded_at, observed_at, verified, origin) VALUES (?,?,?,?,?,?,?,?,?)",
                             (new_id(), ref_id, pr["price"], pr["currency"], pr["effective_date"],
                              pr["source_recorded_at"], observed_at, pr["verified"], origin))
            added = cur.rowcount == 1
    return {"status": status, "price_added": added, "ref_id": ref_id}


def import_pages(db: Database, pages: Iterable[dict], observed_at: str | None = None) -> dict:
    """Catalog pages ({'data': [...], 'meta': {...}}) -> counts. A bad record is reported, not fatal."""
    observed_at = observed_at or now_iso()
    rep = {"seen": 0, "created": 0, "updated": 0, "unchanged": 0, "prices_added": 0, "no_price": 0,
           "rejected": [], "dataset_version": None, "sample": False}
    for page in pages:
        meta = (page or {}).get("meta") or {}
        rep["dataset_version"] = meta.get("dataset_version") or rep["dataset_version"]
        for d in (page or {}).get("data") or []:
            rep["seen"] += 1
            rep["sample"] = rep["sample"] or (isinstance(d, dict) and d.get("sample") is True) \
                or (page or {}).get("sample") is True
            try:
                r = upsert_drug(db, d, observed_at)
            except ValueError as e:
                rep["rejected"].append({"id": d.get("id") if isinstance(d, dict) else None, "reason": str(e)})
                continue
            rep[r["status"]] += 1
            rep["prices_added"] += r["price_added"]
            price = d.get("price") if isinstance(d, dict) else None
            if not isinstance(price, dict) or price.get("current") is None:
                rep["no_price"] += 1
    if rep["dataset_version"]:
        _save_state(db, dataset_version=rep["dataset_version"])
    return rep


def apply_events(db: Database, events: Iterable[dict], observed_at: str | None = None) -> dict:
    """The /price-changes feed. Each event id is applied once; the event carries the current record."""
    observed_at = observed_at or now_iso()
    rep = {"applied": 0, "skipped_duplicate": 0, "rejected": [], "by_type": {}}
    latest = None
    for e in events:
        try:
            eid, etype, sid = int(e["id"]), str(e["type"]), str(e["drug_id"])
        except (KeyError, TypeError, ValueError):
            rep["rejected"].append({"event": e if isinstance(e, dict) else None, "reason": "حدث ناقص"})
            continue
        with db.transaction():
            if db.one("SELECT 1 FROM ref_events_applied WHERE source=? AND event_id=?", (SOURCE, eid)):
                rep["skipped_duplicate"] += 1
                continue
            try:
                if etype == "removed":
                    db.execute("UPDATE ref_products SET removed_at=?, listing_status=coalesce(?, listing_status)"
                               " WHERE source=? AND country_code=? AND source_id=?",
                               (observed_at, ((e.get("data") or {}).get("last_status")), SOURCE, COUNTRY, sid))
                elif etype in ("price_changed", "added", "status_changed", "renamed"):
                    if etype == "renamed":
                        new = _s((e.get("data") or {}).get("new_id"))
                        db.execute("UPDATE ref_products SET replaced_by=? WHERE source=? AND country_code=?"
                                   " AND source_id=?", (new, SOURCE, COUNTRY, sid))
                    if e.get("drug") is not None:
                        upsert_drug(db, e["drug"], observed_at, origin="event")
                else:
                    raise ValueError(f"نوع حدث مش معروف: {etype}")
            except ValueError as err:
                rep["rejected"].append({"event_id": eid, "reason": str(err)})
                continue
            db.execute("INSERT INTO ref_events_applied (source, event_id, event_type, source_id, applied_at)"
                       " VALUES (?,?,?,?,?)", (SOURCE, eid, etype, sid, observed_at))
        rep["applied"] += 1
        rep["by_type"][etype] = rep["by_type"].get(etype, 0) + 1
        det = _s(e.get("detected_at"))
        latest = max(latest, det) if latest and det else (det or latest)
    if latest:
        _save_state(db, latest_detected_at=latest)
    return rep


def _save_state(db: Database, **fields) -> None:
    with db.transaction():
        db.execute("INSERT OR IGNORE INTO ref_sync_state (source, country_code, updated_at) VALUES (?,?,?)",
                   (SOURCE, COUNTRY, now_iso()))
        for k, v in fields.items():
            if k in ("latest_detected_at", "dataset_version"):
                db.execute(f"UPDATE ref_sync_state SET {k}=?, updated_at=? WHERE source=? AND country_code=?",
                           (v, now_iso(), SOURCE, COUNTRY))


def sync_state(db: Database) -> dict | None:
    r = db.one("SELECT * FROM ref_sync_state WHERE source=? AND country_code=?", (SOURCE, COUNTRY))
    return dict(r) if r else None


def by_barcode(db: Database, barcode: str) -> list[dict]:
    bc = normalize_barcode(barcode)
    if not bc:
        return []
    return [dict(r) for r in db.all(
        "SELECT p.* FROM ref_barcodes b JOIN ref_products p ON p.id=b.ref_id WHERE b.barcode=?"
        " ORDER BY p.removed_at IS NOT NULL, p.source_id", (bc,))]


def price_history(db: Database, source_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT r.price, r.currency, r.effective_date, r.observed_at, r.verified, r.origin FROM ref_prices r"
        " JOIN ref_products p ON p.id=r.ref_id WHERE p.source=? AND p.source_id=?"
        " ORDER BY coalesce(r.effective_date, ''), r.observed_at", (SOURCE, source_id))]


def summary(db: Database) -> dict:
    """Coverage numbers, measured from what is stored (never estimated)."""
    one = lambda sql: db.one(sql, (SOURCE, COUNTRY))["n"]  # noqa: E731
    base = "FROM ref_products p WHERE p.source=? AND p.country_code=? AND p.removed_at IS NULL"
    return {
        "products": one(f"SELECT count(*) n {base}"),
        "with_price": one(f"SELECT count(*) n {base} AND EXISTS (SELECT 1 FROM ref_prices r WHERE r.ref_id=p.id)"),
        "with_barcode": one(f"SELECT count(*) n {base} AND EXISTS (SELECT 1 FROM ref_barcodes b WHERE b.ref_id=p.id)"),
        "with_ingredients": one(f"SELECT count(*) n {base} AND p.ingredient_key IS NOT NULL"),
        "shared_barcodes": one("SELECT count(*) n FROM (SELECT b.barcode FROM ref_barcodes b JOIN ref_products p"
                               " ON p.id=b.ref_id WHERE p.source=? AND p.country_code=? AND p.removed_at IS NULL"
                               " GROUP BY b.barcode HAVING count(*) > 1)"),
        "removed": db.one("SELECT count(*) n FROM ref_products WHERE source=? AND country_code=?"
                          " AND removed_at IS NOT NULL", (SOURCE, COUNTRY))["n"],
    }


class DwapriceError(RuntimeError):
    pass


class DwapriceClient:
    """Minimal read-only client. Sample mode uses the free keyless endpoints and never sends a key."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str | None = None, sample: bool = False,
                 session=None, sleep: Callable[[float], None] = time.sleep, max_retries: int = 3,
                 timeout: float = 30):
        if not sample and not api_key:
            raise DwapriceError("مفيش مفتاح: استخدم العينة المجانية أو حط المفتاح في DWAPRICE_API_KEY")
        if session is None:
            import requests
            session = requests.Session()
        self.base, self.key, self.sample = base_url.rstrip("/"), (None if sample else api_key), sample
        self.session, self.sleep, self.max_retries, self.timeout = session, sleep, max_retries, timeout
        self.requests_made = 0

    def _get(self, path: str, params: dict) -> dict:
        headers = {"Accept": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        for attempt in range(self.max_retries + 1):
            r = self.session.get(self.base + path, params=params, headers=headers, timeout=self.timeout)
            self.requests_made += 1
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 503) and attempt < self.max_retries:
                try:
                    wait = float(r.headers.get("Retry-After", 5))
                except ValueError:
                    wait = 5
                if wait > MAX_RETRY_WAIT:   # monthly quota or long maintenance: stop, don't hang
                    break
                self.sleep(max(wait, 1))
                continue
            break
        try:
            err = (r.json() or {}).get("error") or {}
        except ValueError:
            err = {}
        raise DwapriceError(f"{r.status_code} {err.get('code') or ''} {err.get('message') or ''}".strip())

    def pages(self, path: str, params: dict, max_pages: int) -> Iterator[dict]:
        params = {**params, "limit": PAGE_LIMIT}
        for _ in range(max_pages):
            page = self._get(path, params)
            yield page
            nxt = ((page or {}).get("page") or {}).get("next_cursor")
            if not nxt:
                return
            params = {"cursor": nxt, "limit": PAGE_LIMIT}

    def catalog(self, max_pages: int) -> Iterator[dict]:
        return self.pages("/sample/drugs" if self.sample else "/drugs", {}, max_pages)

    def changes(self, since: str, max_pages: int) -> Iterator[dict]:
        return self.pages("/sample/price-changes" if self.sample else "/price-changes", {"since": since}, max_pages)
