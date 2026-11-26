"""U4-0: bring the device, printing and screen settings back from an older copy of the data.

A clean start (launch-fresh-start.bat) renames the data folder, and every setting kept in the database
(org_settings) went back to its default: the receipt and label printers (the label printed across two labels
again, #100), the logo, the drawer, the card machines and wallets, the screen customization. The owner will
not type them again. This module:
- lists the older copies on this PC: the renamed data folders (data-trial-*, data-*) next to the data folder,
  and the backup files (the backup folder, the owner's off-site folder, D:\\PharmacyPlatform-backups);
- reads ONLY the settings below from the chosen copy (never sales, stock, customers, users or passwords);
- shows them (preview), and applies the groups the owner ticks in ONE transaction with its audit record;
- writes / reads a small carry file, so a future clean start keeps them without asking (carry_out / carry_in).

The client never sends a path: it sends the id of a listed source, and the server lists the sources again.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import audit
from .errors import NotFound, ValidationFailed
from .permissions import Context
from ..db import Database, new_id

# group -> (Arabic name, org_settings keys, checked by default). "cards" is the payment_channels table.
# Never here: users, passwords, PINs (shift_handover_pin), roles, money accounts, business data, update state.
GROUPS = {
    "printing": ("الطباعة: الإيصال والملصقات والدرج والطابعات", ("receipt",), True),
    "logo": ("لوجو الإيصال", ("receipt_logo",), True),
    "cards": ("مكن الفيزا والمحافظ الإلكترونية", (), True),
    "screen": ("تخصيص الشاشة (الشريط العلوي والاختصارات والأعمدة ومفاتيح البيع)", ("ui",), True),
    "cash": ("إعدادات الخزينة والفاتورة (عهدة الدرج، حد مصروفات المدير، ترتيب الأصناف، أيام المرتجع، التقريب)",
             ("float_target", "manager_expense_limit", "line_order", "return_window_days", "invoice_rounding"), False),
}
CARRY_GROUPS = ("printing", "logo", "cards", "screen")      # what a clean start keeps by itself
CARRY_FILE = "device_settings_carry.json"
OFFSITE_EXTRA = (Path("D:/PharmacyPlatform-backups"),)
MAX_ZIPS = 20
MAX_VALUE = 2 * 1024 * 1024                                  # one setting value (the logo is far smaller)
_cache: dict[str, tuple[float, dict]] = {}                   # source id -> (mtime, snapshot): a backup is read once


# ------------------------------------------------------------------ sources
def _sid(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _when(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def sources(data_dir: Path, backup_dir: Path, extra_dirs: tuple[Path, ...] = ()) -> list[dict]:
    """Older copies on this PC, newest first: renamed data folders, then backup files."""
    out: list[dict] = []
    here = data_dir.resolve()
    home = data_dir.parent
    try:
        folders = [p for p in home.iterdir() if p.is_dir() and p.name.startswith("data-") and p.resolve() != here
                   and (p / "pharmacy.db").is_file()]
    except OSError:
        folders = []
    for p in folders:
        db = p / "pharmacy.db"
        try:
            out.append({"id": _sid(db), "kind": "folder", "name": p.name, "path": str(db), "when": _when(db)})
        except OSError:
            continue
    zips: dict[Path, Path] = {}
    for d in (backup_dir, *extra_dirs, *OFFSITE_EXTRA):
        try:
            if not d or not d.is_dir():
                continue
            for z in list(d.glob("pharmacy-backup-*.zip")) + list(d.glob("*/pharmacy-backup-*.zip")):
                if z.is_file():
                    zips.setdefault(z.resolve(), z)
        except OSError:
            continue
    dated = []
    for z in zips.values():
        try:
            dated.append((z.stat().st_mtime, z))
        except OSError:
            continue                                     # gone between the listing and now
    for _, z in sorted(dated, key=lambda x: x[0], reverse=True)[:MAX_ZIPS]:
        try:
            out.append({"id": _sid(z), "kind": "backup", "name": z.name, "path": str(z), "when": _when(z)})
        except OSError:
            continue
    out.sort(key=lambda r: r["when"], reverse=True)
    return out


def _find(src_id: str, data_dir: Path, backup_dir: Path, extra_dirs: tuple[Path, ...]) -> dict:
    for r in sources(data_dir, backup_dir, extra_dirs):
        if r["id"] == src_id:
            return r
    raise NotFound("النسخة دي مش موجودة على الجهاز دلوقتي")


# ------------------------------------------------------------------ reading a copy (read-only)
def _read_db(path: Path) -> dict:
    """The settings of the (single) pharmacy in an older database file, opened read-only."""
    from urllib.parse import quote
    uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except (sqlite3.Error, OSError) as e:
        raise ValidationFailed(f"الملف ده مش قاعدة بيانات البرنامج: {e}") from e
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"organizations", "org_settings"} <= tables:
            raise ValidationFailed("الملف ده مش قاعدة بيانات البرنامج")
        org = con.execute("SELECT id, name FROM organizations ORDER BY created_at LIMIT 1").fetchone() \
            if "created_at" in {r[1] for r in con.execute("PRAGMA table_info(organizations)")} else \
            con.execute("SELECT id, name FROM organizations LIMIT 1").fetchone()
        if not org:
            raise ValidationFailed("النسخة دي فاضية (مفيش صيدلية متسجلة فيها)")
        keys = sorted({k for _, ks, _ in GROUPS.values() for k in ks})
        settings = {r["key"]: r["value"] for r in con.execute(
            f"SELECT key, value FROM org_settings WHERE org_id=? AND key IN ({','.join('?' * len(keys))})",
            (org["id"], *keys)) if r["value"] is not None and len(r["value"]) <= MAX_VALUE}
        channels = []
        if "payment_channels" in tables:
            channels = [{"kind": r["kind"], "name": r["name"], "is_active": int(r["is_active"]), "sort": int(r["sort"])}
                        for r in con.execute("SELECT kind, name, is_active, sort FROM payment_channels WHERE org_id=?"
                                             " ORDER BY kind, sort, name", (org["id"],))
                        if r["kind"] in ("card", "transfer")]
        return {"pharmacy": org["name"], "settings": settings, "channels": channels}
    except sqlite3.Error as e:
        raise ValidationFailed(f"تعذّرت قراءة النسخة: {e}") from e
    finally:
        con.close()


def read_source(src: dict) -> dict:
    p = Path(src["path"])
    try:
        mtime = p.stat().st_mtime
    except OSError as e:
        raise NotFound("النسخة دي اتشالت من مكانها") from e
    hit = _cache.get(src["id"])
    if hit and hit[0] == mtime:
        return hit[1]
    if src["kind"] == "folder":
        snap = _read_db(p)
    else:
        from .backup import _extract_db
        try:
            with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(p) as z:
                snap = _read_db(_extract_db(z, tmp))
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as e:
            raise ValidationFailed(f"ملف النسخة الاحتياطية تالف أو مش مقروء: {e}") from e
        except Exception as e:                      # lzma.LZMAError and friends
            if isinstance(e, ValidationFailed):
                raise
            raise ValidationFailed(f"ملف النسخة الاحتياطية تالف أو مش مقروء: {e}") from e
    _cache[src["id"]] = (mtime, snap)
    return snap


# ------------------------------------------------------------------ preview / apply
def _current(db: Database, org_id: str) -> dict:
    keys = sorted({k for _, ks, _ in GROUPS.values() for k in ks})
    settings = {r["key"]: r["value"] for r in db.all(
        f"SELECT key, value FROM org_settings WHERE org_id=? AND key IN ({','.join('?' * len(keys))})", (org_id, *keys))}
    channels = [dict(r) for r in db.all("SELECT kind, name, is_active, sort FROM payment_channels WHERE org_id=?"
                                        " ORDER BY kind, sort, name", (org_id,))]
    return {"settings": settings, "channels": channels}


def _jload(v):
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


_RECEIPT_WORDS = {"receipt_target": "طابعة الإيصال", "label_target": "طابعة الملصقات", "label_lang": "نوع طابعة الملصقات",
                  "label_size": "مقاس الملصق", "label_rows": "عدد الملصقات في صفحة ويندوز", "label_gap": "المسافة بين الملصقات",
                  "label_shift": "تحريك الطباعة (مم)", "label_flip": "قلب الملصق", "paper": "ورق الإيصال (مم)",
                  "drawer_on_cash": "فتح الدرج مع النقدي", "drawer_timing": "توقيت الدرج", "title": "اسم الصيدلية على الإيصال",
                  "footer": "آخر سطر"}


def _describe(group: str, snap: dict) -> list[str]:
    """Plain lines the owner reads before he confirms."""
    st = snap["settings"]
    if group == "printing":
        if "receipt" not in st:
            return []
        v = _jload(st["receipt"])
        if not isinstance(v, dict):
            return []
        lines = [f"{w}: {v[k]}" for k, w in _RECEIPT_WORDS.items() if k in v and v[k] not in ("", None)]
        return lines or ["إعدادات الإيصال محفوظة (القيم الافتراضية)"]
    if group == "logo":
        return ["فيه لوجو محفوظ"] if st.get("receipt_logo") else []
    if group == "cards":
        return [f"{'فيزا' if c['kind'] == 'card' else 'محفظة'}: {c['name']}{'' if c['is_active'] else ' (موقوف)'}"
                for c in snap["channels"]]
    if group == "screen":
        v = _jload(st.get("ui")) if "ui" in st else None
        if not isinstance(v, dict):
            return []
        out = []
        if v.get("nav_main"):
            out.append("الشريط العلوي: " + "، ".join(map(str, v["nav_main"])))
        if v.get("pos_keys"):
            out.append("مفاتيح البيع المتغيرة: " + "، ".join(f"{k}={x}" for k, x in v["pos_keys"].items()))
        if v.get("columns"):
            out.append("أعمدة مخفية في الجداول")
        return out or ["تخصيص الشاشة محفوظ"]
    if group == "cash":
        words = {"float_target": "عهدة الدرج", "manager_expense_limit": "حد مصروفات المدير (قرش)",
                 "line_order": "ترتيب الأصناف", "return_window_days": "أيام المرتجع", "invoice_rounding": "التقريب (قرش)"}
        return [f"{w}: {st[k] if k != 'float_target' else 'محفوظة'}" for k, w in words.items() if k in st]
    return []


def preview(db: Database, ctx: Context, src: dict) -> dict:
    ctx.require("settings.manage")
    snap = read_source(src)
    cur = _current(db, ctx.org_id)
    groups = []
    for g, (title, keys, default) in GROUPS.items():
        lines = _describe(g, snap)
        if g == "cards":
            row = lambda c: (c["kind"], c["name"], int(c["is_active"]), int(c["sort"]))       # noqa: E731
            same = sorted(map(row, snap["channels"])) == sorted(map(row, cur["channels"]))
        else:
            same = all(_jload(snap["settings"].get(k)) == _jload(cur["settings"].get(k)) for k in keys)
        groups.append({"key": g, "title": title, "lines": lines, "available": bool(lines), "same": same,
                       "default": default and bool(lines) and not same})
    return {"source": {k: src[k] for k in ("id", "kind", "name", "when")}, "pharmacy": snap["pharmacy"],
            "groups": groups,
            "note": "مش هيتنقل أي بيع أو مخزون أو عملاء أو مستخدمين أو كلمات سر. ربط كل ماكينة فيزا بحسابها بيتعمل من الإعدادات."}


def _clean_ui(raw: str) -> str:
    """Keep only the screens this version knows (an older copy could name one that is gone)."""
    from . import ui_settings
    v = _jload(raw)
    if not isinstance(v, dict):
        raise ValidationFailed("تخصيص الشاشة في النسخة دي مش مقروء")
    for k in ("nav_main", "home"):
        if isinstance(v.get(k), list):
            v[k] = [x for x in v[k] if x in ui_settings.SCREENS]
    if isinstance(v.get("shortcuts"), dict):
        v["shortcuts"] = {a: b for a, b in v["shortcuts"].items() if b in ui_settings.SCREENS}
    if isinstance(v.get("pos_keys"), dict):
        v["pos_keys"] = {a: b for a, b in v["pos_keys"].items()
                         if a in ui_settings.POS_KEYS_DEFAULT and b in ui_settings.POS_KEYS_ALLOWED}
    return json.dumps(v, ensure_ascii=False)


def _clean_receipt(raw: str) -> tuple[str, list[str]]:
    """The receipt / label settings of the older copy, field by field through the same checks the settings
    screen uses (printing.settings): a field that fails is dropped (the default stays), never stored as it is
    (a printer target is never a file path). Returns (JSON, dropped fields)."""
    from ..printing import settings as rs
    from ..printing.labels import DEFAULT_FIELDS, LANGS
    v = _jload(raw)
    if not isinstance(v, dict):
        raise ValidationFailed("إعدادات الطباعة في النسخة دي مش مقروءة")
    is_int = lambda x: isinstance(x, int) and not isinstance(x, bool)          # noqa: E731
    one_of = lambda *ok: (lambda x: x if x in ok else _bad())                  # noqa: E731
    checks = {
        "receipt_target": lambda x: rs.check_target(x, allow_browser=True), "label_target": rs.check_target,
        "label_size": rs.label_size_ok, "label_shift": rs.label_shift_ok, "label_variant": rs.label_variant_ok,
        "label_flip": rs.label_flip_ok, "label_rows": rs.label_rows_ok,
        "label_a4": lambda x: "%dx%d" % rs.a4_grid(x),
        "paper": one_of(80, 58), "names": one_of("en", "ar", "both"), "label_lang": one_of(*LANGS),
        "label_barcode_source": rs.label_barcode_source_ok, "label_text_lang": one_of("ar", "en"), "label_dpi": one_of(203, 300), "drawer_timing": one_of(*rs.DRAWER_TIMINGS),
        "time_format": one_of("12", "24"), "copies": one_of(1, 2, 3),
        "label_gap": lambda x: x if is_int(x) and 0 <= x <= 10 else _bad(),
        "auto_receipt_min": lambda x: x if is_int(x) and 0 <= x <= 100_000_000 else _bad(),
        "lines": lambda x: [str(s or "").strip()[:60] for s in x[:3]] if isinstance(x, list) else _bad(),
        "label_fields": lambda x: {k: bool(b) for k, b in x.items() if k in DEFAULT_FIELDS} if isinstance(x, dict) else _bad(),
    }
    for k in ("title", "footer", "header"):
        checks[k] = lambda x: str(x or "").strip()[:80]
    for k in ("tax_no", "cr_no"):
        checks[k] = lambda x: str(x or "").strip()[:30]
    for k in ("label_pharmacy", "label_phone", "label_pharmacy_en", "qr_caption", "qr2_caption"):
        checks[k] = lambda x: str(x or "").strip()[:40]
    for k in ("qr_text", "qr2_text"):
        checks[k] = lambda x: str(x or "").strip()[:213]
    for k in ("show_cashier", "show_customer", "show_expiry", "show_return_policy", "auto_receipt", "drawer_on_cash",
              "show_item_count", "show_change"):
        checks[k] = lambda x: x if isinstance(x, bool) else _bad()
    out, dropped = {}, []
    for k, x in v.items():
        if k not in checks:
            continue                                    # a field this version does not know: get() ignores it anyway
        try:
            out[k] = checks[k](x)
        except (ValidationFailed, TypeError, ValueError, AttributeError):
            dropped.append(k)
    if out.get("label_lang") == "driver" and out.get("label_target") and not out["label_target"].startswith("win:"):
        out.pop("label_target")
        dropped.append("label_target")
    return json.dumps(out, ensure_ascii=False), dropped


def _bad():
    raise ValidationFailed("قيمة غير صحيحة")


def _logo_ok(raw: str) -> bool:
    import base64
    import binascii
    try:
        return base64.b64decode(raw, validate=True).startswith(b"\x89PNG\r\n\x1a\n")
    except (binascii.Error, ValueError):
        return False


def _apply(db: Database, org_id: str, snap: dict, groups: list[str]) -> dict:
    """Write the chosen groups (caller holds the transaction). Returns what changed, for the audit record."""
    changed: dict = {}
    for g in groups:
        if g == "cards":
            have = {(r["kind"], r["name"]): r for r in db.all(
                "SELECT id, kind, name, is_active, sort FROM payment_channels WHERE org_id=?", (org_id,))}
            added, updated = [], []
            for c in snap["channels"]:
                k = (c["kind"], c["name"])
                if k in have:
                    if (have[k]["is_active"], have[k]["sort"]) != (c["is_active"], c["sort"]):
                        db.execute("UPDATE payment_channels SET is_active=?, sort=? WHERE id=? AND org_id=?",
                                   (c["is_active"], c["sort"], have[k]["id"], org_id))
                        updated.append(c["name"])
                else:
                    db.execute("INSERT INTO payment_channels (id, org_id, kind, name, is_active, sort) VALUES (?,?,?,?,?,?)",
                               (new_id(), org_id, c["kind"], c["name"], c["is_active"], c["sort"]))
                    added.append(c["name"])
            # a default the old copy did not have (renamed or deleted there) is stopped, unless a sale used it
            old = {(c["kind"], c["name"]) for c in snap["channels"]}
            if old:
                for k, r in have.items():
                    if k not in old and r["is_active"] and not db.one(
                            "SELECT 1 FROM sale_payments WHERE org_id=? AND channel_id=? LIMIT 1", (org_id, r["id"])):
                        db.execute("UPDATE payment_channels SET is_active=0 WHERE id=? AND org_id=?", (r["id"], org_id))
                        updated.append(r["name"])
            if added or updated:
                changed["cards"] = {"added": added, "updated": updated}
            continue
        for key in GROUPS[g][1]:
            if key not in snap["settings"]:
                continue
            val = snap["settings"][key]
            if key == "ui":
                val = _clean_ui(val)
            elif key == "receipt":
                val, dropped = _clean_receipt(val)
                if dropped:
                    changed["receipt_dropped"] = dropped
            elif key == "receipt_logo" and not _logo_ok(val):
                changed["receipt_logo_dropped"] = True
                continue
            before = db.one("SELECT value FROM org_settings WHERE org_id=? AND key=?", (org_id, key))
            if before and before["value"] == val:
                continue
            db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?,?,?) ON CONFLICT(org_id, key)"
                       " DO UPDATE SET value=excluded.value", (org_id, key, val))
            changed[key] = ("لوجو" if key == "receipt_logo" else
                            {"before": _jload(before["value"]) if before else None, "after": _jload(val)})
    return changed


def _groups(chosen) -> list[str]:
    if not isinstance(chosen, list) or not chosen or any(g not in GROUPS for g in chosen):
        raise ValidationFailed("اختار الإعدادات اللي عايز ترجعها")
    return list(dict.fromkeys(chosen))


def restore(db: Database, ctx: Context, src: dict, chosen) -> dict:
    ctx.require("settings.manage")
    groups = _groups(chosen)
    snap = read_source(src)
    with db.transaction():
        changed = _apply(db, ctx.org_id, snap, groups)
        audit.record(db, ctx, "settings.restore_devices", "organization", ctx.org_id,
                     after={"source": src["name"], "kind": src["kind"], "groups": groups, "changed": changed})
    return {"groups": groups, "changed": sorted(changed)}


# ------------------------------------------------------------------ clean start carry-over
def carry_out(db: Database, out: Path) -> dict:
    """manage.py export-device-settings: launch-fresh-start.bat writes this before it renames the data folder."""
    org = db.one("SELECT id, name FROM organizations LIMIT 1")
    if not org:
        raise ValidationFailed("no pharmacy in this database")
    cur = _current(db, org["id"])
    keys = {k for g in CARRY_GROUPS for k in GROUPS[g][1]}
    data = {"format": 1, "pharmacy": org["name"], "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "settings": {k: v for k, v in cur["settings"].items() if k in keys},
            "channels": [{k: c[k] for k in ("kind", "name", "is_active", "sort")} for c in cur["channels"]]}
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    return {"keys": sorted(data["settings"]), "channels": len(data["channels"])}


def carry_in(db: Database, data_dir: Path, org_id: str, user_id: str | None) -> dict | None:
    """Called once, right after the first-run setup created the pharmacy: apply the carry file of a clean start
    (if there is one) and rename it, so it is never applied twice. A bad file is left aside, never fatal."""
    f = data_dir / CARRY_FILE
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != 1:
            raise ValueError("unknown format")
        keys = {k for g in CARRY_GROUPS for k in GROUPS[g][1]}
        snap = {"settings": {k: v for k, v in (data.get("settings") or {}).items()
                             if k in keys and isinstance(v, str) and len(v) <= MAX_VALUE},
                "channels": [c for c in (data.get("channels") or []) if isinstance(c, dict)
                             and c.get("kind") in ("card", "transfer") and isinstance(c.get("name"), str) and c["name"].strip()]}
        for c in snap["channels"]:
            c["is_active"], c["sort"] = int(bool(c.get("is_active", 1))), int(c.get("sort") or 0)
        with db.transaction():
            changed = _apply(db, org_id, snap, list(CARRY_GROUPS))
            audit.record(db, None, "settings.restore_devices", "organization", org_id,
                         after={"source": CARRY_FILE, "kind": "clean_start", "groups": list(CARRY_GROUPS),
                                "changed": changed}, org_id=org_id, user_id=user_id)
    except (OSError, ValueError, TypeError, ValidationFailed) as e:
        try:
            f.replace(f.with_suffix(".bad"))
        except OSError:
            pass
        return {"applied": False, "error": str(e)}
    f.replace(f.with_suffix(".applied"))
    return {"applied": True, "changed": sorted(changed)}
