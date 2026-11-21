"""Customers: profile, credit account, monthly (chronic) orders the pharmacist confirms."""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..core import audit
from ..core.errors import Conflict, NotFound, ValidationFailed
from ..core.numbers import whole_number
from ..core.permissions import Context
from ..db import Database, new_id


from ..catalog.normalize import spelling_key, spelling_sql

# update 1: a name also matches by its Arabic spelling key (جالهوم = جلهوم, ى/ي, ة/ه), every word of the query
SEARCH_SQL = ("c.name LIKE ? OR coalesce(c.name_alt,'') LIKE ? OR coalesce(c.phone,'') LIKE ?"
              " OR coalesce(c.phone2,'') LIKE ? OR coalesce(c.phone3,'') LIKE ?"
              f" OR ({spelling_sql('c.name')} LIKE ? AND ? <> '%%')"
              " OR (" + spelling_sql("coalesce(c.name_alt,'')") + " LIKE ? AND ? <> '%%')")
SEARCH_N = 9
TEXT_FIELDS = ("name", "name_alt", "address1", "address2", "phone", "phone2", "phone3", "notes")


EG_MOBILE = re.compile(r"(?:\+|00)?20(1[0125]\d{8})")


def clean_phone(v) -> str | None:
    """Keeps digits and a leading +; Arabic-Indic digits become 0-9. An Egyptian mobile in its international
    form (+20 / 0020 / 20) is saved as the local 01... number, so one buyer is one number (#324 M2-20)."""
    s = str(v or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    s = re.sub(r"[^\d+]", "", s)
    if not s:
        return None
    if not re.fullmatch(r"\+?\d{3,15}", s):
        raise ValidationFailed(f"رقم الهاتف غير صحيح: {v}")
    m = EG_MOBILE.fullmatch(s)
    return "0" + m.group(1) if m else s


def search_terms(q: str) -> list[str]:
    """The SEARCH_SQL parameters: a whole Egyptian mobile typed in any form is looked up in the form it is saved
    in (01...), so +20 10... finds the customer; anything else (a name, part of a number) as typed."""
    q = str(q or "").strip()
    ph = q
    try:
        c = clean_phone(q)
        if c and re.fullmatch(r"01[0125]\d{8}", c):
            ph = c
    except ValidationFailed:
        pass
    key = "%" + "%".join(spelling_key(q).split()) + "%"
    return [f"%{q}%"] * 2 + [f"%{ph}%"] * 3 + [key, key] * 2


# update 1 (owner 21/11: «د محمد الشربيني», E-Stock code 335, not found): titles are not part of the name
_TITLES = {"د", "دكتر", "لدكتر", "دكتره", "لدكتره", "دك", "ا", "استاذ", "ستاذ", "استذ", "ستذ", "م", "مهندس", "مهندسه"}
ESTOCK_TAG = "كود إي ستوك: "


def search_clause(q: str) -> tuple[str, list, str, list]:
    """WHERE part and ORDER BY rank for a customer search, POS and the customers screen alike: name / second name /
    phones as typed; every word of the query in any order, by its Arabic spelling key (أإآ/ا, ى/ي, ة/ه, long
    vowels; «د/» «دكتور» dropped); and the E-Stock customer code exactly (kept in the notes by the move), ranked
    first. Returns (where_sql, params, rank_sql, rank_params)."""
    q = str(q or "").strip()[:100]
    parts, params = [SEARCH_SQL], search_terms(q)
    words = [w for w in spelling_key(q.replace("/", " ").replace(".", " ")).split() if w not in _TITLES]
    if words:
        one = f"({spelling_sql('c.name')} LIKE ? OR " + spelling_sql("coalesce(c.name_alt,'')") + " LIKE ?)"
        parts.append("(" + " AND ".join([one] * len(words)) + ")")
        for w in words:
            params += [f"%{w}%", f"%{w}%"]
    code_sql = "(coalesce(c.notes,'') = ? OR coalesce(c.notes,'') LIKE ?)"
    code_params = [ESTOCK_TAG + q, ESTOCK_TAG + q + "؛%"]
    parts.append(code_sql)
    params += code_params
    return " OR ".join(f"({p})" for p in parts), params, f"CASE WHEN {code_sql} THEN 0 ELSE 1 END", code_params


def phone_forms(ph: str) -> list[str]:
    """Every way the same number may already be saved (numbers saved before the canonical form)."""
    if re.fullmatch(r"01[0125]\d{8}", ph):
        return [ph, "+2" + ph, "002" + ph, "2" + ph]
    return [ph]


def wa_number(phone: str | None) -> str | None:
    """International form for wa.me: Egyptian 01xxxxxxxxx -> 201xxxxxxxxx."""
    if not phone:
        return None
    p = phone.lstrip("+")
    if re.fullmatch(r"01[0125]\d{8}", p):
        return "2" + p
    if re.fullmatch(r"201[0125]\d{8}", p) or (phone.startswith("+") and len(p) >= 8):
        return p
    return None


def canonical(ph: str) -> str:
    """A saved number in the form clean_phone gives today (one saved before keeps its text if odd)."""
    try:
        return clean_phone(ph) or ph
    except ValidationFailed:
        return ph


def phone_doubles(db: Database, ctx: Context) -> list[dict]:
    """Customers that share one number in two forms, saved before the canonical form: for the owner to
    review and merge by hand (#324 M2-20). Nothing is changed here."""
    ctx.require("customers.manage")
    groups: dict[str, dict[str, dict]] = {}
    for r in db.all("SELECT id, name, phone, phone2, phone3 FROM customers WHERE org_id=? AND is_active=1"
                    " AND coalesce(phone, phone2, phone3) IS NOT NULL", (ctx.org_id,)):
        for k in ("phone", "phone2", "phone3"):
            if r[k]:
                groups.setdefault(canonical(r[k]), {})[r["id"]] = {"id": r["id"], "name": r["name"]}
    return [{"phone": ph, "customers": list(cs.values())} for ph, cs in sorted(groups.items()) if len(cs) > 1]


def check_phones(db: Database, org_id: str, phones: list[str], exclude: str | None = None) -> None:
    """Call inside the write transaction: two tills saving the same number cannot both pass."""
    for ph in phones:
        forms = phone_forms(ph)
        q = ",".join("?" * len(forms))
        r = db.one(f"SELECT id, name FROM customers WHERE org_id=? AND (phone IN ({q}) OR phone2 IN ({q})"
                   f" OR phone3 IN ({q}))" + (" AND id<>?" if exclude else ""),
                   (org_id, *forms, *forms, *forms) + ((exclude,) if exclude else ()))
        if r:
            raise Conflict(f"رقم {ph} مسجل للعميل {r['name']}")
    if len(set(phones)) != len(phones):
        raise ValidationFailed("نفس الرقم مكتوب مرتين")


def _bal_sql() -> str:
    # org_id too: the ledger index is (org_id, customer_id); without it every balance read the whole ledger (#113)
    return "coalesce((SELECT sum(amount) FROM customer_ledger l WHERE l.org_id=c.org_id AND l.customer_id=c.id),0)"


def listing(db: Database, ctx: Context, q: str = "", only: str | None = None, today: date | None = None) -> list[dict]:
    """only: debt / refill / inactive (update 1: the «عملاء موقوفين», e.g. E-Stock-deleted ones with no balance)."""
    ctx.require("sales.read")
    today = today or date.today()
    rank, rank_params = "1", []
    sql = (f"SELECT c.*, {_bal_sql()} balance,"
           " (SELECT min(date(coalesce(r.last_at, r.created_at), '+' || r.every_days || ' days')) FROM customer_refills r"
           "  WHERE r.customer_id=c.id) next_refill, {rank} rank"
           " FROM customers c WHERE c.org_id=? AND c.is_active=" + ("0" if only == "inactive" else "1"))
    params: list = [ctx.org_id]
    if q.strip():
        where, wp, rank, rank_params = search_clause(q)
        sql += f" AND ({where})"
        params += wp
    sql = sql.replace("{rank}", rank)
    params = rank_params + params
    # the filter runs before the 300 limit, so a big customer list never hides a debtor
    sql = f"SELECT * FROM ({sql}) WHERE 1=1"
    if only == "debt":
        sql += " AND balance > 0"
    elif only == "refill":
        sql += " AND next_refill IS NOT NULL AND next_refill <= ?"
        params.append((today + timedelta(days=5)).isoformat())
    sql += " ORDER BY rank, " + ("balance DESC" if only == "debt" else "name") + " LIMIT 300"
    return [dict(r) for r in db.all(sql, tuple(params))]


def set_active(db: Database, ctx: Context, cid: str, active: bool) -> dict:
    """«رجّعه نشط» for a stopped customer (update 1), or stop one; owner / manager (customers.manage), audited."""
    ctx.require("customers.manage")
    c = db.one("SELECT id, is_active FROM customers WHERE id=? AND org_id=?", (cid, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    with db.transaction():
        db.execute("UPDATE customers SET is_active=? WHERE id=?", (1 if active else 0, cid))
        audit.record(db, ctx, "customer.active", "customer", cid, before={"active": c["is_active"]},
                     after={"active": 1 if active else 0})
    return {"id": cid, "is_active": 1 if active else 0}


def update(db: Database, ctx: Context, cid: str, data: dict) -> dict:
    ctx.require("customers.manage")
    c = db.one("SELECT * FROM customers WHERE id=? AND org_id=?", (cid, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    ch = {}
    for k in TEXT_FIELDS:
        if k in data:
            ch[k] = clean_phone(data[k]) if k.startswith("phone") else (str(data[k] or "").strip() or None)
    if "name" in ch and not ch["name"]:
        raise ValidationFailed("اسم العميل مطلوب")
    phones = [ch.get(k, c[k]) for k in ("phone", "phone2", "phone3")]
    if "credit_limit" in data:
        try:                                   # as on create: true is not 1 piaster, and no number SQLite cannot hold
            v = whole_number(data["credit_limit"])
        except (TypeError, ValueError):
            raise ValidationFailed("حد الآجل غير صحيح")
        if v < 0:
            raise ValidationFailed("حد الآجل غير صحيح")
        ch["credit_limit"] = v
    if "whatsapp_ok" in data:
        ch["whatsapp_ok"] = int(bool(data["whatsapp_ok"]))
    if ch:
        with db.transaction():
            if any(k.startswith("phone") for k in ch):        # inside the write, as on create (#324 M2-20)
                check_phones(db, ctx.org_id, [canonical(p) for p in phones if p], exclude=cid)
            try:
                db.execute(f"UPDATE customers SET {', '.join(f'{k}=?' for k in ch)} WHERE id=?", (*ch.values(), cid))
            except Exception as e:
                if "UNIQUE" in str(e):
                    raise Conflict("رقم الهاتف مسجل لعميل آخر") from e
                raise
            audit.record(db, ctx, "customer.update", "customer", cid, before={k: c[k] for k in ch}, after=ch)
    return profile(db, ctx, cid)


def profile(db: Database, ctx: Context, cid: str, today: date | None = None) -> dict:
    ctx.require("sales.read")
    c = db.one(f"SELECT c.*, {_bal_sql()} balance FROM customers c WHERE c.id=? AND c.org_id=?", (cid, ctx.org_id))
    if not c:
        raise NotFound("العميل غير موجود")
    out = dict(c)
    k = db.one("SELECT id, name, discount_local_bp, discount_imported_bp, is_active FROM contracts WHERE id=?", (c["contract_id"],)) \
        if c["contract_id"] else None
    out["contract"] = dict(k) if k else None
    out["wa"] = [w for w in (wa_number(c[k]) for k in ("phone", "phone2", "phone3")) if w]
    out["ledger"] = [dict(r) for r in db.all(
        "SELECT l.amount, l.kind, l.note, l.created_at, l.ref_id, u.full_name by_name FROM customer_ledger l LEFT JOIN users u"
        " ON u.id=l.created_by WHERE l.org_id=? AND l.customer_id=? ORDER BY l.created_at DESC LIMIT 100", (ctx.org_id, cid))]
    mine = "" if ctx.can("sales.totals") else " AND s.created_by=?"
    out["sales"] = [dict(r) for r in db.all(
        "SELECT s.id, s.sale_no, s.total, s.completed_at, s.status FROM sales s WHERE s.org_id=? AND s.customer_id=?"
        f" AND s.status<>'draft'{mine} ORDER BY s.completed_at DESC LIMIT 30",
        (ctx.org_id, cid) + (() if not mine else (ctx.user_id,)))]
    today = today or date.today()
    refills = []
    for r in db.all("SELECT r.*, i.name_en, i.name_ar, i.pack_size, i.base_unit_name,"
                    " coalesce((SELECT sum(qty) FROM stock_balances b WHERE b.item_id=i.id AND b.org_id=r.org_id),0) stock"
                    " FROM customer_refills r JOIN items i ON i.id=r.item_id WHERE r.customer_id=? ORDER BY i.name_en", (cid,)):
        d = dict(r)
        from .service import _local_date
        base = _local_date(d["last_at"] or d["created_at"])
        due = base + timedelta(days=d["every_days"])
        d["due"], d["days_left"], d["in_stock"] = due.isoformat(), (due - today).days, d["stock"] >= d["qty_base"]
        refills.append(d)
    out["refills"] = refills
    out["suggest_refills"] = [dict(r) for r in db.all(
        # bought the same item in 3+ different months: probably monthly medicine
        "SELECT l.item_id, i.name_en, i.name_ar, count(DISTINCT substr(s.completed_at,1,7)) months, max(l.qty_base) qty_base"
        " FROM sale_lines l JOIN sales s ON s.id=l.sale_id JOIN items i ON i.id=l.item_id WHERE s.org_id=? AND s.customer_id=?"
        " AND s.status='completed' AND l.item_id NOT IN (SELECT item_id FROM customer_refills WHERE customer_id=?)"
        " GROUP BY l.item_id HAVING months >= 3 ORDER BY months DESC LIMIT 10", (ctx.org_id, cid, cid))]
    return out


def save_refill(db: Database, ctx: Context, cid: str, item_id: str, qty_base: int, every_days: int = 30) -> dict:
    ctx.require("customers.manage")
    if not isinstance(qty_base, int) or qty_base <= 0 or not isinstance(every_days, int) or not 7 <= every_days <= 180:
        raise ValidationFailed("الكمية أو المدة غير صحيحة")
    if not db.one("SELECT 1 FROM customers WHERE id=? AND org_id=?", (cid, ctx.org_id)) or \
            not db.one("SELECT 1 FROM items WHERE id=? AND org_id=?", (item_id, ctx.org_id)):
        raise NotFound("العميل أو الصنف غير موجود")
    with db.transaction():
        db.execute("INSERT INTO customer_refills (id, org_id, customer_id, item_id, qty_base, every_days) VALUES (?,?,?,?,?,?)"
                   " ON CONFLICT(customer_id, item_id) DO UPDATE SET qty_base=excluded.qty_base, every_days=excluded.every_days",
                   (new_id(), ctx.org_id, cid, item_id, qty_base, every_days))
        audit.record(db, ctx, "customer.refill_save", "customer", cid, after={"item": item_id, "qty": qty_base, "days": every_days})
    return profile(db, ctx, cid)


def remove_refill(db: Database, ctx: Context, cid: str, item_id: str) -> dict:
    ctx.require("customers.manage")
    with db.transaction():
        db.execute("DELETE FROM customer_refills WHERE customer_id=? AND item_id=? AND org_id=?", (cid, item_id, ctx.org_id))
        audit.record(db, ctx, "customer.refill_remove", "customer", cid, after={"item": item_id})
    return profile(db, ctx, cid)


def prepare_refill(db: Database, ctx: Context, cid: str) -> str:
    """Open a cashier invoice for this customer with his monthly items (only what is in stock).
    The pharmacist reviews it and completes it like any sale."""
    from .service import Sales
    s = Sales(db)
    sid = s.create(ctx, cid)
    added = 0
    for r in db.all("SELECT item_id, qty_base FROM customer_refills WHERE customer_id=? AND org_id=?", (cid, ctx.org_id)):
        try:
            s.add_line(ctx, sid, r["item_id"], units=r["qty_base"])
            added += 1
        except Conflict:
            continue                     # not enough stock: left out, the screen shows it as missing
    if not added:
        s.discard(ctx, sid)
        raise Conflict("لا يتوفر أي صنف من الطلب الشهري", code="nothing_in_stock")
    return sid


def mark_refilled(db: Database, sale_id: str) -> None:
    """Called when a sale completes: items of the customer's monthly list restart their count."""
    s = db.one("SELECT customer_id, completed_at FROM sales WHERE id=?", (sale_id,))
    if not s or not s["customer_id"]:
        return
    db.execute("UPDATE customer_refills SET last_at=? WHERE customer_id=? AND item_id IN"
               " (SELECT item_id FROM sale_lines WHERE sale_id=?)", (s["completed_at"], s["customer_id"], sale_id))


REMINDER_TEXT = "أهلًا {name}، {when}. هل تحب أن نجهّزه لك؟ راسلنا هنا ونوصله إليك."


def whatsapp_reminder(db: Database, ctx: Context, cid: str, today: date | None = None) -> dict:
    """A free wa.me click-to-chat link (opens WhatsApp on this device with the text ready; the
    person presses send). No API, no subscription. The text never names the medicine: it leaves
    this program through WhatsApp."""
    from urllib.parse import quote
    ctx.require("customers.manage")
    p = profile(db, ctx, cid, today)
    if not p["whatsapp_ok"]:
        raise Conflict("العميل لا يوافق على رسائل واتساب")
    if not p["wa"]:
        raise ValidationFailed("لا يوجد رقم هاتف صالح لواتساب")
    from ..printing import settings as ps
    org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))
    pharmacy = ps.get(db, ctx.org_id).get("title") or (org["name"] if org else "الصيدلية")
    due = min((r["due"] for r in p["refills"]), default=None)
    today = today or date.today()
    base = f"موعد طلبك الشهري من {pharmacy}"
    when = (f"اليوم {base}" if due == today.isoformat() else f"{base} مرّ" if due and due < today.isoformat()
            else f"{base} يوم {due}" if due else f"{base} اقترب")
    text = REMINDER_TEXT.format(name=p["name"].strip(), when=when)
    with db.transaction():
        db.execute("UPDATE customers SET reminded_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (cid,))
        audit.record(db, ctx, "customer.whatsapp_reminder", "customer", cid)
    return {"url": f"https://wa.me/{p['wa'][0]}?text={quote(text)}", "text": text, "phone": p["wa"][0]}
