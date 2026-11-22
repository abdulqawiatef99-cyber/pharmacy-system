"""HTTP API (Starlette) + static PWA hosting.

Auth: opaque session token, sent either as an HttpOnly SameSite=Strict cookie
(browser/PWA) or as `Authorization: Bearer <token>` (future mobile app /
integrations). Cookie-authenticated state-changing requests must carry the
`X-Requested-With: pharm` header (CSRF defence; cross-site forms cannot set it).
All business rules and permission checks live in the service layer; routes
only translate HTTP <-> service calls.
"""
from __future__ import annotations

import re

import base64
import functools
import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from decimal import Decimal
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import config as config_mod
from ..catalog import service as catalog
from ..core import accounts, approvals, audit, idempotency, notify, people
from ..core.errors import Conflict, DomainError, Forbidden, NotFound, Unauthorized, ValidationFailed
from ..core.numbers import whole_number
from ..finance import money, reconcile
from ..db import Database
from ..inventory import service as inventory
from ..purchasing.service import Purchasing
from ..dataimport.service import DataImport, export_catalog, template_xlsx
from ..safety.engine import SafetyEngine
from ..recalls.service import Recalls, extract_batches
from ..inventory.counting import Counting
from ..procurement.service import Procurement, import_consumption, suggestions_xlsx
from ..procurement import deadstock
from ..sales.service import Sales
from ..sales.cash import CashDrawer, redact_summary
from ..printing import service as printing
import html as _html

log = logging.getLogger("pharm.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@functools.lru_cache(maxsize=256)
def _content_hash(path: Path, mtime_ns: int, size: int) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def _static_v(name: str) -> str:
    """/static/<name>?v=<content hash>, so a browser never keeps an old copy after an update.
    Keyed by mtime and size: a file replaced by an update gets a new hash without a restart (#85)."""
    path = WEB_DIR / name
    st = path.stat()
    return f"/static/{name}?v={_content_hash(path, st.st_mtime_ns, st.st_size)}"


# Shell assets referenced by index.html; each gets its content hash in the URL (#85).
SHELL_ASSETS = ("app.css", "app.js", "i18n-en.js", "manifest.json", "icon.svg")


def _web_build() -> str:
    """One id for the whole screen shell (index.html and its assets). An open tab compares the id it was loaded
    with against /api/health and asks for a reload when an update replaced the files (#153)."""
    parts = [_static_v(name) for name in SHELL_ASSETS]
    st = (WEB_DIR / "index.html").stat()
    parts.append(_content_hash(WEB_DIR / "index.html", st.st_mtime_ns, st.st_size))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _index_html() -> str:
    page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for name in SHELL_ASSETS:
        page = page.replace(f'"/static/{name}"', f'"{_static_v(name)}"')
    return page.replace("</head>", f'  <meta name="pharm-build" content="{_web_build()}">\n</head>', 1)
COOKIE = "pharm_sid"
MAX_JSON = 256 * 1024
MAX_UPLOAD_FILES = 3          # the most files one upload route takes (dead-stock report)
# A screen's table sent back for an Excel file (reports/export.py, up to MAX_ROWS rows): the range
# invoice list alone can hold 5,000 invoices, about 0.6 MB, so the general JSON limit is too small here.
MAX_EXPORT_JSON = 8 * 1024 * 1024
JSON_LIMITS = {"/api/export/xlsx": MAX_EXPORT_JSON}

# URL prefix -> entitlement. A tool switched off for a pharmacy answers 403
# without affecting the others.
FEATURE_PREFIXES = (("/api/sales", "sales"), ("/api/customers", "sales"), ("/receipt", "sales"),
                    ("/api/estock/sales", "sales"),       # returns against old E-Stock invoices (#358 Codex P2)
                    ("/api/cash", "sales"), ("/api/print", "sales"),
                    ("/api/purchases", "purchasing"), ("/api/imports", "dataimport"),
                    ("/api/counts", "counting"), ("/api/procurement", "procurement"),
                    ("/api/recalls", "recalls"), ("/api/safety", "safety"))
IDEMPOTENCY_TTL_DAYS = 7

CSP = ("default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self';"
       " object-src 'none'; frame-src 'self' blob:; base-uri 'none'; frame-ancestors 'none';"
       " form-action 'self'")
# Printable pages may be framed by the app itself: the cashier prints them from a hidden frame,
# which no pop-up blocker stops. Everything else stays unframeable.
PRINT_PAGES = ("/receipt/", "/return-receipt/", "/shift-report/", "/labels")
CSP_PRINT = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if hasattr(o, "__dict__"):
        return o.__dict__
    raise TypeError(type(o))


class J(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=_json_default).encode("utf-8")


SLOW_MS = 800                      # U2-10: a request slower than this is written to the server log
SLOW_LOG: deque = deque(maxlen=200)   # and kept here (newest last) for the «البرنامج» screen
_slow_logger = logging.getLogger("pharmacy.slow")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.monotonic()
        resp = await call_next(request)
        ms = int((time.monotonic() - t0) * 1000)
        if ms > SLOW_MS:                 # the path only, never the query (a customer search carries a phone, #140)
            org = (request.scope.get("state") or {}).get("org_id")
            SLOW_LOG.append({"at": time.time(), "method": request.method, "path": request.url.path, "ms": ms,
                             "status": resp.status_code, "org_id": org})
            _slow_logger.warning("slow request %s %s %d ms", request.method, request.url.path, ms)
        printable = request.url.path.startswith(PRINT_PAGES)
        resp.headers.setdefault("Content-Security-Policy", CSP_PRINT if printable else CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN" if printable else "DENY"
        resp.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            # Revalidate with the ETag on every load: no heuristic caching of an old app.js after an update (#85).
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp


class RateLimiter:
    def __init__(self, limit: int, window_s: int, message: str = "محاولات كثيرة، انتظر دقيقة"):
        self.limit, self.window, self.message = limit, window_s, message
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise DomainError(self.message, code="rate_limited")
        q.append(now)


def create_app(db: Database | None = None, settings: config_mod.Settings | None = None,
               purchasing: Purchasing | None = None, *, auto_backup: bool = False) -> Starlette:
    s = settings or config_mod.load()
    if db is None:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(s.db_path)
        db.migrate()
    accounts.sync_system_roles(db)
    from ..catalog.search import backfill_search_keys, search as item_search
    backfill_search_keys(db)
    from ..dataimport import estock_party_fix      # update 1: customers / suppliers the move hid, once
    estock_party_fix.run(db)
    from ..catalog import alternatives as alts
    alts.backfill_ingredient_keys(db)
    from ..catalog import card as item_card
    item_card.ensure_default_categories(db)
    from ..sales import channels as pay_channels
    pay_channels.ensure_defaults(db)
    from ..finance import expenses as fin
    fin.ensure_defaults(db)
    from ..core.autobackup import AutoBackup
    autob = AutoBackup(db, s.upload_dir, s.backup_path, s.data_dir)
    purch = purchasing or Purchasing(db, s)
    dimp = DataImport(db)
    safety = SafetyEngine(db)
    recalls = Recalls(db)
    counting = Counting(db)
    login_limiter = RateLimiter(10, 60)
    approver_limiter = RateLimiter(20, 60)
    # the current password typed in an open session (#134): 5 tries in 5 minutes per user, so an open
    # session cannot be used to guess the password at full speed
    reauth_limiter = RateLimiter(5, 300, "محاولات كثيرة لكلمة المرور، انتظر 5 دقائق")

    def _qlimit(req: Request, default: int, most: int) -> int:
        """?limit= as a whole number from 1 to `most`: "abc" is a 422, not a server error, and -1 is not
        SQLite's "no limit" (every row of the table)."""
        raw = req.query_params.get("limit") or default
        try:
            n = whole_number(raw)
        except (TypeError, ValueError):
            raise ValidationFailed("قيمة غير صحيحة: limit")
        return max(1, min(n, most))

    def client_ip(req: Request) -> str:
        return req.client.host if req.client else "?"

    def approver_access(org_id: str, who: str) -> dict:
        """The approver's permissions now; nothing once he is disabled (#137: a token never outlives that)."""
        if not db.one("SELECT 1 FROM users u JOIN memberships m ON m.user_id=u.id AND m.org_id=?"
                      " WHERE u.id=? AND u.is_active=1 AND m.is_active=1", (org_id, who)):
            return {"permissions": frozenset(), "role_code": None, "max_discount_bp": 0}
        return accounts.effective_access(db, org_id, who)

    def ctx_of(req: Request):
        token = None
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        else:
            token = req.cookies.get(COOKIE)
            if token and req.method not in ("GET", "HEAD") and \
                    req.headers.get("x-requested-with") != "pharm":
                raise Unauthorized("طلب غير موثّق", code="csrf")
        if not token:
            raise Unauthorized("سجّل الدخول أولًا")
        ctx = accounts.resolve_session(db, token, ip=client_ip(req))
        req.scope.setdefault("state", {})["org_id"] = ctx.org_id    # the slow-request log keeps each pharmacy apart
        approver = req.headers.get("x-approver", "").strip()
        if approver and req.url.path == "/api/approvals/check":
            # the PIN is typed once and checked here only; every other request carries the approval token (#137)
            approver_limiter.check(client_ip(req))
            ctx = accounts.with_approval(db, ctx, approver[:64], req.headers.get("x-approver-pin", "")[:6])
            req.state.session_token = token
        elif req.headers.get("x-approval"):
            ctx = approvals.apply(ctx, token, req.headers["x-approval"][:128], f"{req.method} {req.url.path}",
                                  lambda who: approver_access(ctx.org_id, who),
                                  body=getattr(req.state, "approval_body", None))
        for prefix, feature in FEATURE_PREFIXES:
            if req.url.path.startswith(prefix):
                accounts.require_feature(db, ctx, feature)
                break
        return ctx

    def _length_guard(req: Request, limit: int, *, required: bool = False) -> None:
        """Refuse an oversized request from its Content-Length, before reading it (#140)."""
        cl = req.headers.get("content-length")
        if required and req.headers.get("transfer-encoding"):
            # the HTTP parser streams a chunked body whatever Content-Length says, so a declared size is only
            # true without it; an upload is spooled before its real size is checked (#324 X18)
            raise ValidationFailed("حجم الطلب غير معروف", code="length_required")
        if cl is None:
            if required:
                raise ValidationFailed("حجم الطلب غير معروف", code="length_required")
            return
        if not cl.isdigit():
            raise ValidationFailed("صيغة الطلب غير صحيحة")
        if int(cl) > limit:
            raise ValidationFailed("حجم الملف أكبر من المسموح" if limit > MAX_JSON else "الطلب كبير جدًا",
                                   code="too_large")

    def _body_limit(req: Request) -> int:
        if "multipart/form-data" in req.headers.get("content-type", ""):
            return MAX_UPLOAD_FILES * s.max_upload_bytes + 64 * 1024
        return JSON_LIMITS.get(req.url.path, MAX_JSON)

    async def _read_body(req: Request) -> bytes:
        """req.body() that stops at the limit instead of reading everything first (#140).
        The bytes are cached on the request, so req.body() and req.form() reuse them."""
        if hasattr(req, "_body"):
            return req._body
        limit = _body_limit(req)
        _length_guard(req, limit)
        chunks, n = [], 0
        async for chunk in req.stream():
            n += len(chunk)
            if n > limit:
                raise ValidationFailed("الطلب كبير جدًا", code="too_large")
            chunks.append(chunk)
        req._body = b"".join(chunks)
        return req._body

    async def body(req: Request) -> dict:
        raw = await _read_body(req)
        if len(raw) > JSON_LIMITS.get(req.url.path, MAX_JSON):
            raise ValidationFailed("الطلب كبير جدًا", code="too_large")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValidationFailed("صيغة الطلب غير صحيحة")
        if not isinstance(data, dict):
            raise ValidationFailed("صيغة الطلب غير صحيحة")
        return data

    def _query_part(req: Request) -> bytes:
        """The query string is part of the request (?month= names the month an advance goes to, M2-22). Added only
        when there is one, so the fingerprint of every other request stays what it was before."""
        return b"?" + req.url.query.encode() if req.url.query else b""

    def _same_form(req: Request, raw: bytes) -> bytes:
        """A file upload sent again carries a new multipart boundary around the same fields and file: the
        fingerprint is taken without it, so a retry replays instead of being called a different request
        (#324 M2-23). Any other body is fingerprinted as it is."""
        m = re.search(r'boundary="?([^";]+)"?', req.headers.get("content-type", ""))
        if not m or not req.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            return raw
        return raw.replace(b"--" + m.group(1).encode("latin-1"), b"--\x00boundary")

    async def _idempotent(req: Request, fn):
        """Replay the stored response for a repeated POST with the same
        Idempotency-Key (client retry after a network drop / double click)."""
        # This database-only route commits its action and replay record together.
        if req.method == "POST" and (req.url.path == "/api/cash/movements" or
                (req.url.path.startswith("/api/customers/") and req.url.path.endswith("/payments"))):
            return await fn(req)
        key = req.headers.get("idempotency-key", "").strip()
        if req.method != "POST" or not key or len(key) > 100 or req.url.path.startswith("/api/auth/"):
            return await fn(req)
        ctx = ctx_of(req)
        raw = await _read_body(req)
        rhash = hashlib.sha256(req.method.encode() + req.url.path.encode() + _query_part(req)
                               + _same_form(req, raw)).hexdigest()
        ident = (ctx.org_id, ctx.user_id, key)
        # Reserve the key (status 0) before running, in one write transaction: a retry that
        # arrives while the first attempt runs, or after it committed but could not store its
        # response, finds the reservation and is refused instead of posting a second time.
        with db.transaction():
            hit = db.one("SELECT * FROM idempotency_keys WHERE org_id=? AND user_id=? AND key=?", ident)
            if not hit:
                idempotency.prune(db, IDEMPOTENCY_TTL_DAYS)   # at most once an hour, not a scan per POST (#324 M2-25)
                db.execute("INSERT INTO idempotency_keys (org_id, user_id, key, method, path,"
                           " request_hash, status, response) VALUES (?,?,?,?,?,?,0,'')",
                           (*ident, req.method, req.url.path, rhash + "|" + ",".join(sorted(ctx.permissions))))
        if hit:
            stored, _, held = hit["request_hash"].partition("|")
            if stored != rhash:
                raise ValidationFailed("نفس مفتاح الطلب استُخدم لطلب مختلف", code="idempotency_mismatch")
            if hit["status"] == 0:
                raise Conflict("هذه العملية أُرسلت من قبل ولم تتأكد نتيجتها بعد. راجعها (الفواتير/الخزينة/الحساب)"
                               " قبل إعادتها", code="idempotency_unconfirmed")
            if req.url.path == "/api/cash/close":
                # rebuilt under the permissions held now, not the report stored at the close (#324 M4)
                resp = J(CashDrawer(db).close_result(ctx, json.loads(hit["response"])["id"]), status_code=hit["status"])
            else:
                # the stored answer was built under the permissions held then: after one of them was taken away
                # it is not handed out again (a payroll answer after payroll.manage was revoked, #324 X7)
                if held and not set(held.split(",")) <= set(ctx.permissions):
                    raise Forbidden("صلاحياتك اتغيّرت بعد العملية دي؛ راجع نتيجتها من شاشتها",
                                    code="permissions_changed")
                resp = Response(hit["response"], status_code=hit["status"], media_type="application/json")
            resp.headers["Idempotent-Replay"] = "true"
            return resp

        def release():
            with db.transaction():
                db.execute("DELETE FROM idempotency_keys WHERE org_id=? AND user_id=? AND key=? AND status=0",
                           ident)

        # every write is saved together with its audit row: a rule error raised AFTER one (a 403 while
        # building the answer) must keep the key, or the retry posts a second time (#324 S6, as #331)
        audit_mark = _audit_mark(ctx)
        try:
            resp = await fn(req)
        except DomainError:
            if not _committed_since(audit_mark, ctx):
                release()        # refused by a rule before any write: the same key may run again
            raise
        # Any other exception keeps the reservation: it may have come after a commit, so a retry
        # with this key is refused rather than risk a second posting (a new click uses a new key).
        if 200 <= resp.status_code < 300 and resp.media_type == "application/json":
            # If this write fails, the reservation stays and a retry is refused (never re-run).
            with db.transaction():
                db.execute("UPDATE idempotency_keys SET status=?, response=? WHERE org_id=? AND user_id=? AND key=?",
                           (resp.status_code, resp.body.decode("utf-8"), *ident))
        else:
            release()            # only successful JSON answers are replayed, as before
        return resp

    def _audit_mark(ctx) -> int:
        """The newest audit row of this pharmacy. `+org_id` keeps SQLite on the rowid order (read backwards,
        stopped at the first match) instead of the org index, which reads every row of the pharmacy."""
        r = db.one("SELECT rowid n FROM audit_log WHERE +org_id=? ORDER BY rowid DESC LIMIT 1", (ctx.org_id,))
        return r["n"] if r else 0

    def _committed_since(mark: int, ctx) -> bool:
        """Did this person save anything (an audit row) since `mark`? A concurrent write by the same
        person also counts: the key is then kept, the safe side (the retry is refused, never re-run).
        A rowid range (`+` keeps SQLite off the org index): only the rows written since the mark are read."""
        return db.one("SELECT 1 FROM audit_log WHERE rowid>? AND +org_id=? AND +user_id=? LIMIT 1",
                      (mark, ctx.org_id, ctx.user_id)) is not None

    def handler(fn):
        async def wrapped(req: Request):
            try:
                approval = req.headers.get("x-approval", "")[:128]
                if approval and req.method not in ("GET", "HEAD"):
                    # an approval is bound to the exact request it is first used on and spent once it succeeds:
                    # one PIN = one action with one quantity (#324 X5)
                    req.state.approval_body = hashlib.sha256(await _read_body(req)).hexdigest()
                resp = await _idempotent(req, fn)
                if approval and req.method not in ("GET", "HEAD") and 200 <= resp.status_code < 300:
                    approvals.consume(approval)
                return resp
            except DomainError as e:
                status = 429 if e.code == "rate_limited" else e.status
                return J({"error": {"code": e.code, "message": e.message, "details": e.details}},
                         status_code=status)
            except Exception:   # never leak internals
                log.exception("unhandled error on %s %s", req.method, req.url.path)
                return J({"error": {"code": "internal", "message": "حدث خطأ غير متوقع"}},
                         status_code=500)
        return wrapped

    async def off_loop(fn, *args, **kwargs):
        """#249: a report reads in a worker thread, on that thread's own connection and inside one
        read snapshot, so a long report never stops the cashier screen or /api/health."""
        def run():
            with db.read_snapshot():
                return fn(*args, **kwargs)
        return await run_in_threadpool(run)

    # ------------------------------------------------------------- auth
    @handler
    async def login(req):
        # only from the program's own screen or a JSON client: a form on another site (text/plain) cannot sign a
        # browser in to someone else's account (#324 X25)
        if req.headers.get("x-requested-with") != "pharm" and \
                not req.headers.get("content-type", "").lower().startswith("application/json"):
            raise Unauthorized("طلب غير موثّق", code="csrf")
        login_limiter.check(client_ip(req))
        b = await body(req)
        res = accounts.login(db, str(b.get("username", "")), str(b.get("password", "")),
                             org_id=b.get("org_id"), ip=client_ip(req),
                             user_agent=req.headers.get("user-agent"))
        ctx = accounts.resolve_session(db, res["token"])
        out = {"me": accounts.me(db, ctx), "expires_at": res["expires_at"]}
        if b.get("token_in_body"):        # mobile/integration clients
            out["token"] = res["token"]
        resp = J(out)
        resp.set_cookie(COOKIE, res["token"], httponly=True, samesite="strict",
                        secure=s.cookie_secure, max_age=int(accounts.SESSION_TTL.total_seconds()),
                        path="/")
        return resp

    # ------------------------------------------------------------- first run
    setup_lock = threading.Lock()

    def _setup_needed() -> bool:
        return db.one("SELECT count(*) n FROM organizations")["n"] == 0

    def _own_screen_only(req) -> None:
        """Setup runs before any session exists, so nothing else stops a web page open in the browser on
        this PC from posting here (a form or text/plain POST needs no CORS permission). The program's
        own screens send this header; a cross-site page cannot (same rule as ctx_of for sessions)."""
        if req.headers.get("x-requested-with") != "pharm":
            raise Forbidden("طلب غير موثّق", code="csrf")

    @handler
    async def setup_status(req):
        return J({"needed": _setup_needed()})

    @handler
    async def setup_create(req):
        """First run only: creates the pharmacy and its owner, then logs in. Refused once any
        pharmacy exists, and (unless PHARM_ALLOW_REMOTE_SETUP=1) from any machine but this PC."""
        import os as _os
        _own_screen_only(req)
        if client_ip(req) not in ("127.0.0.1", "::1", "localhost", "testclient") and \
                _os.environ.get("PHARM_ALLOW_REMOTE_SETUP") != "1":
            raise Forbidden("الإعداد الأول يتم من جهاز الصيدلية نفسه فقط")
        login_limiter.check(client_ip(req))
        if not _setup_needed():
            raise Conflict("البرنامج مُعَدّ بالفعل؛ سجّل الدخول", code="already_setup")
        b = await body(req)
        name, user, pw = (str(b.get(k, "")).strip() for k in ("pharmacy", "username", "password"))
        if len(name) < 2 or len(user) < 3:
            raise ValidationFailed("اكتب اسم الصيدلية واسم المستخدم (3 حروف على الأقل)")
        if str(b.get("rounding") if b.get("rounding") is not None else "").strip() not in ("", "0", "25", "50", "100"):
            raise ValidationFailed("اختار تقريب الفاتورة")
        with setup_lock:
            if not _setup_needed():
                raise Conflict("البرنامج مُعَدّ بالفعل؛ سجّل الدخول", code="already_setup")
            accounts.create_organization(db, name=name, owner_username=user,
                                         owner_full_name=str(b.get("full_name") or user).strip(),
                                         owner_password=pw)
            item_card.ensure_default_categories(db)
            pay_channels.ensure_defaults(db)
            fin.ensure_defaults(db)
        res = accounts.login(db, user, pw, ip=client_ip(req), user_agent=req.headers.get("user-agent"))
        if b.get("rounding") is not None and str(b["rounding"]).strip() != "":   # U2-14: asked at setup
            from ..sales import rounding as _rnd
            _rnd.set_step(db, accounts.resolve_session(db, res["token"]), b.get("rounding"))
        ctx = accounts.resolve_session(db, res["token"])
        resp = J({"me": accounts.me(db, ctx)}, status_code=201)
        resp.set_cookie(COOKIE, res["token"], httponly=True, samesite="strict", secure=s.cookie_secure,
                        max_age=int(accounts.SESSION_TTL.total_seconds()), path="/")
        return resp

    # ------------------------------------------------------------- program update (owner)
    upd_cache: dict = {}

    def _update_status() -> dict:
        from ..core import updater
        from ..version import __version__
        now = time.time()
        if upd_cache.get("at", 0) > now - 600:
            return upd_cache["v"]
        info = updater.installed(s.data_dir)
        cur = info.get("sha")
        token = updater.read_token()
        # #107: the install's own channel (develop on test PCs); a review installer of a work branch follows the
        # official release (main) once main contains it (owner 20/11), never an older main
        chan = updater.follow(info, updater.DEFAULT_REPO, token)
        out = {"version": __version__, "installed": cur, "latest": None, "available": False, "channel": chan,
               "restarts_itself": os.environ.get("PHARM_LAUNCHER") == "1", "smart": _smart_status(chan, info)}
        if chan is None:
            out["candidate"] = info["ref"]        # a review installer main does not contain yet: no channel
            upd_cache.update(at=now, v=out)
            return out
        try:
            out["latest"] = updater.latest_sha(updater.DEFAULT_REPO, chan, token)
            out["available"] = out["latest"] != cur
        except updater.UpdateError as e:
            out["error"] = str(e)
        upd_cache.update(at=now, v=out)
        return out

    upd_root = Path(__file__).resolve().parents[2]

    def _smart_status(chan, info) -> dict:
        """docs/SMART_UPDATE.md: signed bundles (migrations and packages too) once this PC trusts a key."""
        from ..core import smart_update as su, update_sign, updater
        out = {"enabled": False, "prepared": su.prepared(s.data_dir) or None,
               "request": su.pending_request(s.data_dir) or None, "result": su.last_result(s.data_dir) or None}
        try:
            keys = update_sign.trusted_keys(upd_root)
        except (OSError, update_sign.SignatureError):
            keys = []
        if not keys or chan is None or su.bootstrap_level(su.home_of(s.data_dir)) < 2:
            return out                                # old launcher: today's code-only button
        token = updater.read_token()
        try:
            m = su.read_manifest(su.GitHubSource(updater.DEFAULT_REPO, chan, token), keys)
            forward = su.is_forward(m, info, updater.DEFAULT_REPO, token)   # never an older bundle
        except updater.UpdateError as e:
            out["error"] = str(e)
            return out
        out.update(enabled=True, sha=m["sha"], available=forward, needs_installer=m["python"] != su.python_tag(),
                   notes=[n for n in m.get("notes", []) if n not in set(su.notes_of(upd_root))][:60],
                   database=bool(set(m["migrations"]) - {f.stem for f in (upd_root / "app" / "migrations").glob("*.sql")}))
        if out["request"]:
            out["waiting"] = su.busy_reason(db, out["request"].get("when", "now"))
        return out

    def _manage(*args, timeout=1800):
        import subprocess
        import sys as _sys
        return subprocess.run([_sys.executable, str(upd_root / "manage.py"), "update", *args], cwd=os.getcwd(),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              env={**os.environ, "PYTHONUTF8": "1"})

    def _smart_loop():
        from ..core import smart_update as su
        try:
            su.import_log(db, s.data_dir)
        except Exception:                             # the audit copy is retried on the next start
            logging.getLogger(__name__).exception("update log import failed")
        while True:
            time.sleep(30)
            try:
                if os.environ.get("PHARM_LAUNCHER") == "1":
                    su.trigger(s, db)
            except Exception:
                logging.getLogger(__name__).exception("smart update check failed")

    if auto_backup and os.environ.get("PHARM_LAUNCHER") == "1":
        threading.Thread(target=_smart_loop, daemon=True, name="smart-update").start()

    @handler
    async def system_update_status(req):
        ctx = ctx_of(req)
        ctx.require("settings.manage")
        st = await run_in_threadpool(_update_status)
        from ..dataimport import estock_party_fix                  # update 1: what the one-time repair brought back
        return J({**st, "parties_reactivated": estock_party_fix.result(db, ctx.org_id)})

    @handler
    async def system_update_run(req):
        ctx = ctx_of(req)
        ctx.require("settings.manage")
        b = await body(req)
        action = (b or {}).get("action") or "classic"
        if action != "classic":
            return J(await _smart_action(ctx, action, b))
        import subprocess
        import sys as _sys
        from ..core import updater
        chan = updater.follow(updater.installed(s.data_dir), updater.DEFAULT_REPO, updater.read_token())
        if chan is None:
            raise ValidationFailed("النسخة دي متثبتة من فرع تجربة مؤقت ومالهاش قناة تحديث؛ ثبّت بالمثبّت", code="no_channel")
        root = Path(__file__).resolve().parents[2]
        proc = await run_in_threadpool(lambda: subprocess.run(
            [_sys.executable, str(root / "manage.py"), "update", "--ref", chan], cwd=os.getcwd(), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=900,
            env={**os.environ, "PYTHONUTF8": "1"}))
        ok = proc.returncode == 0
        with db.transaction():
            audit.record(db, ctx, "system.update", "system", None,
                         after={"ok": ok, "channel": chan, "tail": (proc.stdout or "")[-500:]})
        upd_cache.clear()
        restart = ok and os.environ.get("PHARM_LAUNCHER") == "1"
        if restart:          # the launcher starts the new version when we exit with 75
            threading.Timer(1.5, lambda: os._exit(75)).start()
        return J({"ok": ok, "restarting": restart,
                  "output": ((proc.stdout or "") + (proc.stderr or ""))[-3000:]}, status_code=200 if ok else 500)

    async def _smart_action(ctx, action: str, b: dict) -> dict:
        """prepare (download, verify, rehearse) / schedule (now | after_close) / cancel; each one audited."""
        from ..core import smart_update as su, updater
        if action == "prepare":
            proc = await run_in_threadpool(_manage, "--prepare")
            ok = proc.returncode == 0
            text = (proc.stdout or "") + (proc.stderr or "")
            with db.transaction():
                audit.record(db, ctx, "system.update.prepare", "system", None, after={"ok": ok, "tail": text[-500:]})
            upd_cache.clear()
            if not ok:
                raise ValidationFailed(text.strip().splitlines()[-1] if text.strip() else "التجهيز فشل", code="update_failed")
            return {"ok": True, "prepared": su.prepared(s.data_dir) or None}
        if action == "schedule":
            when = b.get("when")
            try:
                value = su.request(s.data_dir, when, ctx.user_id)
            except updater.UpdateError as e:
                raise ValidationFailed(str(e), code="update_failed") from e
            with db.transaction():
                audit.record(db, ctx, "system.update.schedule", "system", None, after=value)
            upd_cache.clear()
            return {"ok": True, "request": value, "waiting": su.busy_reason(db, when)}
        if action == "cancel":
            su.cancel(s.data_dir)
            with db.transaction():
                audit.record(db, ctx, "system.update.cancel", "system", None)
            upd_cache.clear()
            return {"ok": True}
        raise ValidationFailed("أمر تحديث غير معروف", code="bad_action")

    @handler
    async def setup_restore(req):
        """First run only: bring an existing pharmacy over from a backup .zip (moving to a new PC
        or to the installed version). The file is verified now and applied by the launcher on
        restart, when no database connection is open."""
        from ..core import backup as backup_mod
        _own_screen_only(req)
        if client_ip(req) not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise Forbidden("الاسترجاع يتم من جهاز الصيدلية نفسه فقط")
        if not _setup_needed():
            raise Conflict("البرنامج به بيانات بالفعل؛ الاسترجاع من هنا لأول تشغيل فقط", code="already_setup")
        if os.environ.get("PHARM_LAUNCHER") != "1":
            raise ValidationFailed("الاسترجاع من هنا يعمل في النسخة المثبتة فقط. استخدم الأمر restore")
        # a backup of a real pharmacy is far above the normal upload limit: stream it to disk
        if "multipart/form-data" not in req.headers.get("content-type", ""):
            raise ValidationFailed("اختر ملف النسخة الاحتياطية")
        # the form is spooled whole before the 4 GiB check below: refuse a bigger or unsized body first (#324 X18)
        _length_guard(req, 4 * 1024 ** 3 + 1024 * 1024, required=True)
        form = await req.form(max_files=1, max_fields=2)
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise ValidationFailed("اختر ملف النسخة الاحتياطية")
        s.data_dir.mkdir(parents=True, exist_ok=True)
        pending = s.data_dir / "pending_restore.zip"
        size, limit = 0, 4 * 1024 ** 3
        try:
            with open(pending, "wb") as out:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ValidationFailed("ملف النسخة الاحتياطية أكبر من 4 جيجا")
                    out.write(chunk)
        except ValidationFailed:
            pending.unlink(missing_ok=True)
            raise
        finally:
            await up.close()
        try:
            info = await run_in_threadpool(backup_mod.verify_archive, pending)
        except Exception as e:  # noqa: BLE001 - not a zip / unreadable
            info = {"problems": [f"الملف ليس نسخة احتياطية ({type(e).__name__})"]}
        if info["problems"]:
            pending.unlink(missing_ok=True)
            raise ValidationFailed("ملف النسخة الاحتياطية غير سليم: " + "؛ ".join(info["problems"][:3]))
        threading.Timer(1.0, lambda: os._exit(75)).start()
        return J({"ok": True, "restarting": True})

    @handler
    async def logout(req):
        if req.cookies.get(COOKIE) and req.headers.get("x-requested-with") != "pharm":
            raise Unauthorized("طلب غير موثّق", code="csrf")
        token = req.cookies.get(COOKIE) or req.headers.get("authorization", "")[7:]
        if token:
            accounts.logout(db, token)
        resp = J({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @handler
    async def me(req):
        ctx = ctx_of(req)
        from ..core import ui_settings
        from ..inventory import integrity
        from ..printing import settings as rs
        return J({**accounts.me(db, ctx), "notifications_unread": notify.unread_count(db, ctx),
                  "ui": ui_settings.get(db, ctx.org_id), "stock_alert": integrity.banner(db, ctx),
                  "time_format": rs.get(db, ctx.org_id)["time_format"]})   # #206: every screen shows times this way

    @handler
    async def stock_alert_api(req):
        """#202: the red banner, refreshed by the screens on their own (login answers do not carry it)."""
        from ..inventory import integrity
        ctx = ctx_of(req)
        ctx.require("audit.read")
        return J({"alert": integrity.banner(db, ctx)})

    @handler
    async def stock_check_report(req):
        """#202 «مطابقة الأرصدة»: POST runs the stock integrity check now and returns the report."""
        from ..inventory import integrity
        return J(await run_in_threadpool(integrity.report, db, ctx_of(req)))

    @handler
    async def stock_check_ack(req):
        """#202: the owner marks one sale-vs-invoice difference as reviewed (audited), then the report again."""
        from ..inventory import integrity
        ctx = ctx_of(req)
        b = await body(req)
        # in a worker thread: it may wait for a running check's lock and re-runs the check (Codex P2)
        await run_in_threadpool(lambda: integrity.acknowledge(
            db, ctx, str(b.get("sale_id") or ""), str(b.get("item_id") or ""), b.get("note"),
            moved=b.get("moved"), expected=b.get("expected")))
        return J(await run_in_threadpool(integrity.report, db, ctx))

    @handler
    async def rounding_api(req):
        from ..sales import rounding as rnd
        ctx = ctx_of(req)
        if req.method == "PUT":
            return J({"step": rnd.set_step(db, ctx, (await body(req)).get("step"))})
        ctx.require("sales.read")
        return J({"step": rnd.step(db, ctx.org_id)})

    @handler
    async def ui_settings_api(req):
        from ..core import ui_settings
        ctx = ctx_of(req)
        if req.method == "PUT":
            return J(ui_settings.save(db, ctx, await body(req)))
        return J(ui_settings.get(db, ctx.org_id))

    # ------------------------------------------------------------- people: PIN, approval, users
    def _local_only(req):
        if client_ip(req) not in ("127.0.0.1", "::1", "localhost", "testclient") and \
                os.environ.get("PHARM_ALLOW_REMOTE_QUICK") != "1":
            raise Forbidden("الدخول السريع من جهاز الصيدلية فقط", code="local_only")

    @handler
    async def quick_users(req):
        _local_only(req)
        return J({"users": people.quick_users(db)})

    @handler
    async def pin_login(req):
        _local_only(req)
        login_limiter.check(client_ip(req))
        b = await body(req)
        users = {u["id"]: u for u in people.quick_users(db)}
        u = users.get(str(b.get("user_id", "")))
        if not u:
            raise Unauthorized("الرقم السري غير صحيح", code="invalid_pin")
        res = people.pin_login(db, u["org_id"], u["id"], str(b.get("pin", ""))[:6], ip=client_ip(req),
                               user_agent=req.headers.get("user-agent"))
        ctx = accounts.resolve_session(db, res["token"])
        resp = J({"me": accounts.me(db, ctx), "expires_at": res["expires_at"]})
        resp.set_cookie(COOKIE, res["token"], httponly=True, samesite="strict", secure=s.cookie_secure,
                        max_age=int(accounts.SESSION_TTL.total_seconds()), path="/")
        return resp

    @handler
    async def my_pin(req):
        ctx = ctx_of(req)
        b = await body(req)
        u = db.one("SELECT password_hash FROM users WHERE id=?", (ctx.user_id,))
        from ..core import security as _sec
        reauth_limiter.check(ctx.user_id)
        if not _sec.verify_password(str(b.get("password", "")), u["password_hash"]):
            raise ValidationFailed("كلمة المرور غير صحيحة")
        people.set_pin(db, ctx, ctx.user_id, str(b.get("pin", "")))
        return J({"ok": True})

    @handler
    async def my_prefs(req):
        ctx = ctx_of(req)
        return J(people.set_prefs(db, ctx, await body(req)))

    @handler
    async def item_health_api(req):
        from ..reports.item_health import item_health
        return J(await off_loop(item_health, db, ctx_of(req)))

    @handler
    async def item_pack_preview(req):
        from ..catalog import repack
        return J(repack.preview(db, ctx_of(req), req.path_params["id"],
                              int(p) if (p := req.query_params.get("pack") or "").isdigit() else -1))

    @handler
    async def item_pack_correct(req):
        from ..catalog import repack
        b = await body(req)
        return J(repack.correct_pack(db, ctx_of(req), req.path_params["id"], b.get("pack_size"), b.get("reason") or ""))

    @handler
    async def item_card_get(req):
        return J(item_card.card(db, ctx_of(req), req.path_params["id"]))

    @handler
    async def sections_suggest(req):
        from ..catalog import suggest_sections
        return J(await run_in_threadpool(suggest_sections.suggest, db, ctx_of(req)))

    @handler
    async def contracts_api(req):
        from ..sales import contracts as k
        ctx = ctx_of(req)
        if req.method == "GET":
            return J({"contracts": k.listing(db, ctx)})
        return J({"contracts": k.save(db, ctx, await body(req), req.path_params.get("id"))})

    @handler
    async def contract_report(req):
        from ..sales import contracts as k
        qp = req.query_params
        return J(await off_loop(k.report, db, ctx_of(req), req.path_params["id"], qp.get("from", ""), qp.get("to", "")))

    @handler
    async def customer_contract(req):
        from ..sales import contracts as k
        from ..sales import customers as cu
        ctx = ctx_of(req)
        b = await body(req)
        k.link_customer(db, ctx, req.path_params["id"], b.get("contract_id") or None)
        return J(cu.profile(db, ctx, req.path_params["id"]))

    @handler
    async def audit_list(req):
        qp = req.query_params
        return J(await off_loop(audit.listing, db, ctx_of(req), qp.get("from", ""), qp.get("to", ""),
                                qp.get("user") or None, qp.get("group") or None))

    @handler
    async def item_flags(req):
        """Mark many items at once: prescription only / often misused (items.write)."""
        ctx = ctx_of(req)
        ctx.require("items.write")
        b = await body(req)
        ids = b.get("item_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, str) for i in ids):
            raise ValidationFailed("اختر الأصناف (حتى 500 مرة واحدة)")
        ch = {k: int(bool(b[k])) for k in ("rx_required", "misuse_risk", "quick_pin") if k in b}
        if not ch:
            raise ValidationFailed("اختر العلامة")
        marks = ",".join("?" * len(ids))
        with db.transaction():
            n = db.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in ch)} WHERE org_id=? AND id IN ({marks})",
                           (*ch.values(), ctx.org_id, *ids)).rowcount
            audit.record(db, ctx, "item.flags", "item", None, after={**ch, "items": n})
        return J({"updated": n})

    @handler
    async def item_alternatives(req):
        return J(alts.for_item(db, ctx_of(req), req.path_params["id"]))

    @handler
    async def ingredients_list(req):
        return J(alts.ingredients(db, ctx_of(req)))

    @handler
    async def groups_api(req):
        ctx, gid = ctx_of(req), req.path_params.get("id")
        if req.method == "GET":
            return J({"groups": alts.list_groups(db, ctx)})
        if req.method == "DELETE":
            return J({"groups": alts.delete_group(db, ctx, gid)})
        return J({"groups": alts.save_group(db, ctx, await body(req), gid)})

    @handler
    async def pos_quick(req):
        return J(alts.quick_items(db, ctx_of(req)))

    @handler
    async def invoices_report(req):
        from ..reports import invoices
        qp = req.query_params
        return J(await off_loop(invoices.report, db, ctx_of(req), date_from=qp.get("from", ""), date_to=qp.get("to", ""),
                                 types=[t for t in qp.get("types", "").split(",") if t], q=qp.get("q"),
                                 item_id=qp.get("item") or None, user_id=qp.get("user") or None, with_lines=qp.get("lines") == "1"))

    @handler
    async def owner_report(req):
        from ..reports import owner
        fn = owner.REPORTS.get(req.path_params["kind"])
        if not fn:
            raise NotFound("التقرير غير موجود")
        qp = req.query_params
        return J(await off_loop(fn, db, ctx_of(req), qp.get("from", ""), qp.get("to", "")))

    @handler
    async def warnings_rep(req):
        from ..sales.service import warnings_report
        qp = req.query_params
        return J(await off_loop(warnings_report, db, ctx_of(req), qp.get("from", ""), qp.get("to", "")))

    @handler
    async def stock_reasons(req):
        from ..inventory import adjust
        from ..procurement import returns
        ctx_of(req)
        return J({"adjust": adjust.REASONS, "supplier_return": returns.REASONS, "up": list(adjust.UP_OK)})

    @handler
    async def item_adjust(req):
        from ..inventory import adjust
        b = await body(req)
        qd, st = b.get("qty_delta"), b.get("set_to")
        return J(adjust.adjust(db, ctx_of(req), req.path_params["id"], qty_delta=_int(qd, "qty") if qd not in (None, "") else None,
                               set_to=_int(st, "qty") if st not in (None, "") else None, batch_id=b.get("batch_id") or None,
                               reason=str(b.get("reason") or ""), note=b.get("note")))

    @handler
    async def losses_report(req):
        from ..inventory import adjust
        qp = req.query_params
        return J(await off_loop(adjust.loss_report, db, ctx_of(req), qp.get("from", ""), qp.get("to", "")))

    @handler
    async def supplier_returns_api(req):
        from ..procurement import returns
        ctx = ctx_of(req)
        rid = req.path_params.get("id")
        if req.method == "GET":
            qp = req.query_params
            if rid == "suggest":
                return J({"unit_value": returns.suggest_value(db, ctx, qp.get("item", ""), qp.get("batch") or None,
                                                              qp.get("supplier") or None)})
            if rid == "invoice":        # part 3 item 6b: load an approved purchase invoice to return from
                return J(returns.from_invoice(db, ctx, qp.get("supplier", ""), qp.get("no", "")))
            if rid:
                return J(returns.get(db, ctx, rid))
            return J(returns.listing(db, ctx, qp.get("from", ""), qp.get("to", ""), qp.get("supplier") or None))
        b = await body(req)
        if b.get("invoice_id"):
            return J(returns.create_from_invoice(db, ctx, str(b.get("supplier_id") or ""), str(b["invoice_id"]),
                                                 b.get("lines") or [], credit_note_no=b.get("credit_note_no"),
                                                 note=b.get("note"), partial_bonus_reason=b.get("partial_bonus_reason")))
        return J(returns.create(db, ctx, str(b.get("supplier_id") or ""), b.get("lines") or [], b.get("note")))

    @handler
    async def supplier_return_page(req):
        """Printable return slip (A4) for the supplier's representative to sign."""
        from ..procurement import returns
        ctx = ctx_of(req)
        r = returns.get(db, ctx, req.path_params["id"])
        org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
        e = _html.escape
        m = lambda p: f"{(p or 0) / 100:,.2f}"  # noqa: E731
        show = "total" in r
        taxed = show and bool(r.get("tax_total"))       # part 3 item 6b: the tax credited back, on its own column
        from ..printing import settings as rs
        when = rs.fmt_when(accounts.parse_iso(r["created_at"]).astimezone(), rs.get(db, ctx.org_id)["time_format"])  # #206
        rows = "".join(
            f"<tr><td>{i}</td><td>{e(l['name_en'] or l['name_ar'] or '')}</td><td>{e(l['batch_no'] or '')}</td>"
            f"<td class='n'><bdi dir='ltr'>{e((l['expiry_date'] or '')[:7])}</bdi></td><td>{l['qty_base']}{' (بونص)' if l.get('is_bonus') else ''}</td><td>{e(l['reason_label'])}</td>"
            + (f"<td>{m(l['unit_value'])}</td>" + (f"<td>{m(l.get('tax_value'))}</td>" if taxed else "")
               + f"<td>{m(l['unit_value'] * l['qty_base'] + (l.get('tax_value') or 0))}</td>" if show else "") + "</tr>"
            for i, l in enumerate(r["lines"], 1))
        page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>مرتجع للمورد {r['return_no']}</title>
<link rel="stylesheet" href="/static/doc.css"></head><body><main class="doc">
<header><h1>{e(org)}</h1><h2>إذن مرتجع للمورد رقم {r['return_no']}</h2>
<p>المورد: <b>{e(r['supplier'])}</b> · التاريخ: <bdi dir="ltr">{when}</bdi> · سجّله: {e(r['by_name'] or '')}{f" · إشعار المورد: {e(r['credit_note_no'])}" if r.get('credit_note_no') else ''}</p></header>
<table><thead><tr><th>#</th><th>الصنف</th><th>التشغيلة</th><th>الصلاحية</th><th>الكمية (وحدة)</th><th>السبب</th>
{'<th>سعر الوحدة</th>' + ('<th>الضريبة</th>' if taxed else '') + '<th>القيمة</th>' if show else ''}</tr></thead><tbody>{rows}</tbody>
{f'<tfoot><tr><th colspan="{8 if taxed else 7}">الإجمالي</th><th>{m(r["total"])}</th></tr></tfoot>' if show else ''}</table>
{f'<p>ملاحظة: {e(r["note"])}</p>' if r.get('note') else ''}
<div class="sign"><p>توقيع الصيدلية: ................</p><p>توقيع مندوب المورد: ................</p></div>
</main><script src="/static/print.js"></script></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    @handler
    async def item_moves(req):
        qp = req.query_params
        return J(item_card.movements(db, ctx_of(req), req.path_params["id"], qp.get("from", ""), qp.get("to", "")))

    @handler
    async def item_action(req):
        ctx = ctx_of(req)
        iid, action = req.path_params["id"], req.path_params["action"]
        b = await body(req)
        if action == "shortage":
            item_card.set_shortage(db, ctx, iid, bool(b.get("on")))
        elif action == "category":
            item_card.set_category(db, ctx, [iid], b.get("category_id") or None)
        elif action == "barcode":
            if b.get("remove"):
                item_card.remove_barcode(db, ctx, iid, str(b.get("barcode", "")))
            else:
                catalog.add_barcode(db, ctx, iid, str(b.get("barcode", "")))
        else:
            raise ValidationFailed("عملية غير معروفة")
        return J(item_card.card(db, ctx, iid))

    @handler
    async def categories_list(req):
        return J({"categories": item_card.list_categories(db, ctx_of(req))})

    @handler
    async def categories_save(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(item_card.save_category(db, ctx, b, req.path_params.get("id")))

    @handler
    async def categories_assign(req):
        ctx = ctx_of(req)
        b = await body(req)
        n = item_card.set_category(db, ctx, b.get("item_ids") or [], b.get("category_id") or None)
        return J({"updated": n})

    @handler
    async def suppliers_all(req):
        from ..procurement import suppliers as sup
        return J({"suppliers": sup.listing(db, ctx_of(req))})

    @handler
    async def supplier_account(req):
        from ..procurement import suppliers as sup
        ctx = ctx_of(req)
        if req.method == "PATCH":
            return J(sup.update(db, ctx, req.path_params["id"], await body(req)))
        return J(sup.account(db, ctx, req.path_params["id"]))

    @handler
    async def supplier_pay(req):
        from ..procurement import suppliers as sup
        ctx = ctx_of(req)
        b = await body(req)
        sid = req.path_params["id"]
        method = str(b.get("method", ""))
        source = b.get("source")
        disc = b.get("discount")
        disc_bp = b.get("discount_bp")
        if source in (None, "") and disc in (None, "", 0) and disc_bp in (None, ""):
            # an older screen: the drawer, or (owner only) outside the program's accounts
            if method not in ("drawer", ""):
                money.check_source(db, ctx, money.OUTSIDE)
            return J(sup.pay(db, ctx, sid, _int(b.get("amount"), "amount"), method, b.get("note"), b.get("ref")))
        if disc_bp not in (None, ""):
            paid, discount = reconcile.split_payment(settle=_int(b.get("settle"), "settle"),
                                                     percent_bp=_int(disc_bp, "discount_bp"))
        else:
            paid, discount = reconcile.split_payment(paid=_int(b.get("amount"), "amount"),
                                                     discount=_int(disc or 0, "discount"))
        ctx.require("suppliers.pay")
        src = money.check_source(db, ctx, source or money.OUTSIDE)
        if src == money.DRAWER:
            method = "drawer"
        elif src == money.OUTSIDE:
            method = method if method in ("cash", "cheque") else "cash"
        else:
            kind = money.get_account(db, ctx, src)["kind"] if ctx.can("cash.see_expected") else "other"
            method = method if method == "cheque" else {"bank": "bank", "wallet": "wallet"}.get(kind, "cash")
        with db.transaction():
            if src == money.DRAWER:
                money.drawer_check(db, ctx, paid)        # the drawer never goes below zero (F105)
            sup.pay(db, ctx, sid, paid, method, b.get("note"), b.get("ref"))
            lid = db.one("SELECT id FROM supplier_ledger WHERE org_id=? AND supplier_id=? AND kind='payment'"
                         " ORDER BY rowid DESC LIMIT 1", (ctx.org_id, sid))["id"]
            if src not in (money.DRAWER, money.OUTSIDE):
                name = db.one("SELECT name FROM suppliers WHERE id=? AND org_id=?", (sid, ctx.org_id))["name"]
                money.pay_out(db, ctx, src, paid, "supplier_payment", ref_type="supplier_ledger", ref_id=lid,
                              note=f"دفع للمورد {name}")
            if discount:
                reconcile.supplier_discount(db, ctx, sid, discount, payment_id=lid, note=b.get("discount_note"))
        out = sup.account(db, ctx, sid) if ctx.can("suppliers.read") else {"id": sid}
        return J({**out, "paid": paid, "discount": discount, "payment_id": lid})

    @handler
    async def export_xlsx(req):
        from urllib.parse import quote
        from ..reports.export import xlsx
        ctx_of(req)                                   # signed in; the content is what the screen already shows
        b = await body(req)
        data = await run_in_threadpool(xlsx, b)
        name = re.sub(r'[\\/:*?"<>|]', " ", str(b.get("filename") or "report"))[:80].strip() or "report"
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=report.xlsx; filename*=UTF-8''{quote(name)}.xlsx",
                                 "Cache-Control": "no-store"})

    @handler
    async def sale_attach(req):
        from ..sales import attachments as att
        ctx = ctx_of(req)
        data, name, fields = await _upload(req)
        return J(await run_in_threadpool(att.attach, db, s.upload_dir, ctx, req.path_params["id"], data, name, fields.get("note")))

    @handler
    async def attachment_get(req):
        from ..sales import attachments as att
        data, mime = att.file(db, s.upload_dir, ctx_of(req), req.path_params["id"])
        return Response(data, media_type=mime, headers={"Content-Disposition": "inline", "Cache-Control": "private, max-age=3600",
                                                        "X-Content-Type-Options": "nosniff"})

    @handler
    async def attachment_review(req):
        from ..sales import attachments as att
        b = await body(req)
        amount = b.get("amount")
        if amount is not None:                      # part of the transfer arrived (#324 M2-01)
            amount = _int(amount, "amount")
        return J(att.review(db, ctx_of(req), req.path_params["id"], bool(b.get("ok")), b.get("note"), amount=amount))

    # ------------------------------------------------------------- E-Stock history (read only) and old-invoice returns
    @handler
    async def estock_available(req):
        from ..dataimport import estock_history as eh
        ctx = ctx_of(req)
        if not any(ctx.allowed(p) for p in ("sales.read", "items.read", "purchases.read", "suppliers.read")):
            ctx.require("sales.read")             # only someone who may open one of the history screens asks
        return J({"available": eh.available(db)})

    @handler
    async def estock_invoices(req):
        from ..dataimport import estock_history as eh
        qp = req.query_params
        return J({"invoices": await off_loop(eh.invoices, db, ctx_of(req), qp.get("kind") or "sale",
                                             q=qp.get("q") or None, date_from=qp.get("from") or None,
                                             date_to=qp.get("to") or None, party_id=qp.get("party") or None,
                                             item_id=qp.get("item") or None, ticket=qp.get("ticket") or None,
                                             limit=_int(qp.get("limit") or 200, "limit"))})

    @handler
    async def estock_invoice(req):
        from ..dataimport import estock_history as eh
        return J(await off_loop(eh.invoice, db, ctx_of(req), req.path_params["kind"], req.path_params["id"]))

    @handler
    async def estock_customer_items(req):
        from ..dataimport import estock_history as eh
        return J({"items": await off_loop(eh.customer_items, db, ctx_of(req), req.path_params["id"])})

    @handler
    async def estock_item_history(req):
        from ..dataimport import estock_history as eh
        return J({"history": await off_loop(eh.item_history, db, ctx_of(req), req.path_params["id"])})

    @handler
    async def estock_statement(req):
        from ..dataimport import estock_history as eh
        return J(await off_loop(eh.statement_with_opening, db, ctx_of(req), req.path_params["kind"],
                                req.path_params["id"]))

    @handler
    async def estock_return_lines(req):
        from ..sales import estock_returns as er
        return J({"lines": await off_loop(er.invoice_lines, db, ctx_of(req), req.path_params["id"])})

    @handler
    async def estock_return(req):
        """A return against an old E-Stock sale (#346): money back only the way it was received, never more."""
        from ..sales import estock_returns as er
        ctx = ctx_of(req)
        b = await body(req)
        raw = b.get("lines")
        if not isinstance(raw, dict) or not raw:
            raise ValidationFailed("اختر الأصناف والكميات المرتجعة", code="empty_return")
        lines = {str(k): _int(v, "qty") for k, v in raw.items()}
        key = req.headers.get("idempotency-key", "").strip()
        return J(er.return_items(db, ctx, req.path_params["id"], lines, reason=str(b.get("reason") or ""), key=key),
                 status_code=201)

    @handler
    async def transfers_report(req):
        from ..sales import attachments as att
        qp = req.query_params
        return J(await off_loop(att.transfers, db, ctx_of(req), qp.get("from", ""), qp.get("to", "")))

    @handler
    async def exp_sched(req):
        from ..finance import schedules
        ctx = ctx_of(req)
        sid = req.path_params.get("id")
        if req.method == "GET":
            return J({"schedules": schedules.listing(db, ctx)})
        b = await body(req)
        if sid and b.get("action") == "pay":
            amt = b.get("amount")
            exp = b.get("expected_amount")
            return J(schedules.pay(db, ctx, sid, _int(amt, "amount") if amt not in (None, "") else None,
                                   b.get("paid_from") or None, b.get("note"),
                                   period=b["period"] if b.get("period") not in (None, "") else None,
                                   expected_amount=_int(exp, "amount") if exp not in (None, "") else None))
        if sid and b.get("action") == "skip":
            return J({"schedules": schedules.skip(db, ctx, sid, str(b.get("period", "")))})
        if "amount" in b and b["amount"] not in (None, ""):
            b["amount"] = _int(b["amount"], "amount")
        return J(schedules.save(db, ctx, b, sid))

    @handler
    async def supplier_statement(req):
        from ..procurement import suppliers as sup
        qp = req.query_params
        return J(sup.statement(db, ctx_of(req), req.path_params["id"], qp.get("from", ""), qp.get("to", "")))

    @handler
    async def supplier_statements(req):
        from ..procurement import suppliers as sup
        qp = req.query_params
        return J(sup.statements(db, ctx_of(req), qp.get("from", ""), qp.get("to", ""), qp.get("type") or None))

    @handler
    async def shortages_list(req):
        from ..procurement import suppliers as sup
        return J({"items": sup.shortages(db, ctx_of(req))})

    @handler
    async def customers_all(req):
        from ..sales import customers as cu
        qp = req.query_params
        return J({"customers": cu.listing(db, ctx_of(req), qp.get("q", ""), qp.get("only") or None,
                                          sort=qp.get("sort") or None)})

    @handler
    async def customers_phone_doubles(req):
        from ..sales import customers as cu
        return J({"doubles": cu.phone_doubles(db, ctx_of(req))})

    @handler
    async def customer_profile(req):
        from ..sales import customers as cu
        ctx = ctx_of(req)
        if req.method == "PATCH":
            return J(cu.update(db, ctx, req.path_params["id"], await body(req)))
        return J(cu.profile(db, ctx, req.path_params["id"]))

    @handler
    async def customer_refill(req):
        from ..sales import customers as cu
        ctx = ctx_of(req)
        b = await body(req)
        cid = req.path_params["id"]
        if b.get("prepare"):
            return J({"sale_id": cu.prepare_refill(db, ctx, cid)})
        if b.get("whatsapp"):
            return J(cu.whatsapp_reminder(db, ctx, cid))
        if b.get("remove"):
            return J(cu.remove_refill(db, ctx, cid, str(b.get("item_id", ""))))
        return J(cu.save_refill(db, ctx, cid, str(b.get("item_id", "")), b.get("qty_base"), b.get("every_days", 30)))

    @handler
    async def expenses_list(req):
        ctx = ctx_of(req)
        qp = req.query_params
        flt = dict(status=qp.get("status") or None, month=qp.get("month") or None, date_from=qp.get("from") or None,
                   date_to=qp.get("to") or None)
        return J({"expenses": fin.listing(db, ctx, **flt), "totals": fin.totals(db, ctx, **flt),
                  "categories": fin.categories(db, ctx), "manager_limit": fin.manager_limit(db, ctx.org_id)})

    @handler
    async def expenses_add(req):
        ctx = ctx_of(req)
        b = await body(req)
        if b.get("new_category"):
            return J({"categories": fin.add_category(db, ctx, str(b["new_category"]))})
        return J(fin.record(db, ctx, category_id=str(b.get("category_id", "")), amount=_int(b.get("amount"), "amount"),
                            paid_from=str(b.get("paid_from", "")), note=b.get("note"), spent_on=b.get("spent_on")),
                 status_code=201)

    @handler
    async def expenses_decide(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(fin.decide(db, ctx, req.path_params["id"], bool(b.get("approve")), b.get("reason")))

    @handler
    async def profit_report(req):
        qp = req.query_params
        return J(await off_loop(fin.profit, db, ctx_of(req), qp.get("from", ""), qp.get("to", "")))

    def backup_health() -> dict:
        """The last good automatic backup and whether it is late: red when the last run failed, or the last good
        one (a run that found no changes counts) is older than two intervals."""
        from datetime import datetime, timedelta
        st, cfg = autob.state(), autob.settings()
        interval = int(cfg.get("interval_min") or 60)
        times = [st.get("last_at")] + ([st.get("last_check_at")] if st.get("last_ok") else [])
        good = None
        for t in times:
            try:
                d = datetime.fromisoformat(t) if t else None
            except (TypeError, ValueError):
                d = None
            if d and d <= datetime.now() and (good is None or d > good):
                good = d
        failed = st.get("last_ok") is False
        late = good is not None and datetime.now() - good > timedelta(minutes=2 * interval)
        return {"last_good_at": good.isoformat(timespec="seconds") if good else None, "ok": st.get("last_ok"),
                "failed": failed, "late": late, "warn": failed or late, "interval_min": interval,
                "last_fail_at": st.get("last_fail_at"), "last_error": st.get("last_error") if failed else None,
                "offsite_error": st.get("offsite_error")}

    @handler
    async def day_health_view(req):
        """Part 3 item 4: the day's checks at shift close and the PC check, read only."""
        from ..reports import dayhealth
        ctx = ctx_of(req)

        def run():
            return {"day": dayhealth.day_checks(db, ctx, backup=backup_health()),
                    "pc": dayhealth.pc_checks(db, s.data_dir, org_id=ctx.org_id)}
        return J(await off_loop(run))

    @handler
    async def backup_health_view(req):
        ctx_of(req).require("settings.manage")
        return J(backup_health())

    @handler
    async def home_view(req):
        from ..reports import service as rep_svc
        ctx = ctx_of(req)
        out = await off_loop(rep_svc.home, db, ctx)
        if ctx.can("settings.manage"):
            st = autob.state()
            out["backup"] = {"last_at": st.get("last_at"), "ok": st.get("last_ok"), "offsite_error": st.get("offsite_error"),
                             **backup_health()}
        return J(out)

    @handler
    async def reports_sales(req):
        from ..reports import service as rep_svc
        qp = req.query_params
        return J(await off_loop(rep_svc.sales_report, db, ctx_of(req), qp.get("from", ""), qp.get("to", "")))

    @handler
    async def reports_stock(req):
        from ..reports import service as rep_svc
        return J(await off_loop(rep_svc.stock_report, db, ctx_of(req)))

    @handler
    async def feedback_view(req):
        from ..core import feedback as fb
        ctx = ctx_of(req)
        if req.method == "POST":
            b = await body(req)
            if b.get("id"):
                fb.set_status(db, ctx, str(b["id"]), str(b.get("status", "")), b.get("reply"))
                return J({"items": fb.listing(db, ctx)})
            return J(fb.add(db, ctx, str(b.get("kind", "")), str(b.get("text", "")), b.get("screen")), status_code=201)
        return J({"items": fb.listing(db, ctx)})

    def _lan_addresses() -> list[str]:
        import socket
        ips = set()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.connect(("10.255.255.255", 1))          # no packet is sent; picks the LAN interface
                ips.add(sk.getsockname()[0])
        except OSError:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ips.add(info[4][0])
        except OSError:
            pass
        return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))

    @handler
    async def system_storage(req):
        from ..core import retention
        ctx = ctx_of(req)
        ctx.require("settings.manage")
        return J(await run_in_threadpool(retention.sizes, db, Path(db.path), s.upload_dir, s.backup_path))

    @handler
    async def system_network(req):
        """Mobiles on the pharmacy Wi-Fi: on/off (owner). Takes effect after the program restarts itself."""
        ctx = ctx_of(req)
        ctx.require("settings.manage")
        f = s.data_dir / "network.json"
        try:
            cur = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cur = {"lan": False}
        if req.method == "PUT":
            want = bool((await body(req)).get("lan"))
            f.write_text(json.dumps({"lan": want}), encoding="utf-8")
            with db.transaction():
                audit.record(db, ctx, "system.network", "system", None, before=cur, after={"lan": want})
            cur = {"lan": want}
            if os.environ.get("PHARM_LAUNCHER") == "1":
                threading.Timer(1.5, lambda: os._exit(75)).start()
                cur["restarting"] = True
        listening_lan = req.url.hostname not in ("127.0.0.1", "localhost") or os.environ.get("PHARM_HOST") == "0.0.0.0"
        port = req.url.port or 80
        return J({**cur, "addresses": [f"http://{ip}:{port}/" for ip in _lan_addresses()], "active_now": listening_lan,
                  "launcher": os.environ.get("PHARM_LAUNCHER") == "1"})

    @handler
    async def attendance_me(req):
        from ..finance import payroll as pay
        ctx = ctx_of(req)
        if req.method == "POST":
            b = await body(req)
            src = "pc" if client_ip(req) in ("127.0.0.1", "::1", "testclient") else "phone"
            return J(pay.check(db, ctx, str(b.get("action", "")), src))
        return J(pay.status(db, ctx))

    @handler
    async def staff_sheet(req):
        from ..finance import payroll as pay
        ctx = ctx_of(req)
        month = req.query_params.get("month") or ""
        if req.method == "POST":
            b = await body(req)
            a, uid, month = str(b.get("action", "")), str(b.get("user_id", "")), b.get("month", month)
            if a in ("pay_settings", "fix"):          # not about a month: never save and then answer 422
                month = pay.sheet_month(month)
            if a == "pay_settings":
                pay.set_pay(db, ctx, uid, _int(b.get("monthly_salary"), "salary"), _int(b.get("work_days") or 26, "work_days"))
            elif a == "advance":
                pay.add_advance(db, ctx, uid, _int(b.get("amount"), "amount"), month, b.get("note"), str(b.get("paid_from", "outside")))
            elif a == "fix":
                pay.fix_attendance(db, ctx, uid, str(b.get("day", "")), str(b.get("check_in", "")), b.get("check_out") or None,
                                   str(b.get("note", "")))
            elif a == "approve":
                return J(pay.approve(db, ctx, uid, month, _int(b.get("bonus"), "bonus") or 0, _int(b.get("deduction"), "deduction") or 0,
                                     b.get("note")))
            elif a == "pay":
                net = b.get("expected_net")
                return J(pay.pay(db, ctx, uid, month, str(b.get("paid_from", "outside")),
                                 expected_net=_int(net, "net") if net not in (None, "") else None))
            else:
                raise ValidationFailed("عملية غير معروفة")
        return J(pay.month_sheet(db, ctx, month))

    @handler
    async def approvers_list(req):
        return J({"approvers": people.approvers(db, ctx_of(req))})

    @handler
    async def approval_check(req):
        """The approver typed his PIN: confirm it before the screen retries the action."""
        ctx = ctx_of(req)
        if not ctx.approver_id:
            raise ValidationFailed("اختر من سيوافق")
        b = await body(req)
        perm, path = str(b.get("permission", "")), str(b.get("path", "")).split("?")[0]
        method = str(b.get("method") or "GET").upper()
        ok = ctx.allowed(perm) if perm in accounts.PERMISSIONS else perm == approvals.DISCOUNT
        out = {"ok": ok, "discount_limit_bp": ctx.discount_limit_bp}
        if ok and path.startswith("/api/") and len(path) <= 200 and path != "/api/approvals/check" \
                and method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            # #137: lend only this permission, only on the action (method + path) that asked for it, for 90 s
            out["token"], out["expires_in"] = approvals.issue(
                ctx, req.state.session_token, ctx.approver_id, f"{method} {path}", perm,
                previous=str(b.get("previous") or "")[:128] or None)
        return J(out)

    @handler
    async def users_list(req):
        ctx = ctx_of(req)
        return J({"users": people.list_users(db, ctx), "roles": people.roles(db, ctx)})

    @handler
    async def users_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        uid = people.add_user(db, ctx, username=str(b.get("username", "")), full_name=str(b.get("full_name", "")),
                              password=str(b.get("password", "")), role_code=str(b.get("role", "")),
                              pin=(str(b["pin"]) if b.get("pin") else None))
        return J(people.access_matrix(db, ctx, uid), status_code=201)

    @handler
    async def users_get(req):
        return J(people.access_matrix(db, ctx_of(req), req.path_params["id"]))

    @handler
    async def users_action(req):
        ctx = ctx_of(req)
        uid, action = req.path_params["id"], req.path_params["action"]
        b = await body(req)
        if action in ("pin", "password") and uid == ctx.user_id:
            # #131: an open session alone must not change its own sign-in (owner lock-out, PIN take-over)
            u = db.one("SELECT password_hash FROM users WHERE id=?", (ctx.user_id,))
            from ..core import security as _sec
            reauth_limiter.check(ctx.user_id)
            if not _sec.verify_password(str(b.get("current_password", "")), u["password_hash"]):
                raise ValidationFailed("اكتب كلمة المرور الحالية الصحيحة", code="current_password_required")
        if action == "permission":
            return J(people.set_exception(db, ctx, uid, str(b.get("permission", "")), str(b.get("mode", ""))))
        if action == "reset":
            return J(people.reset_to_role(db, ctx, uid))
        if action == "discount":
            return J(people.set_discount(db, ctx, uid, b.get("max_discount_bp")))
        if action == "role":
            return J(people.set_role(db, ctx, uid, str(b.get("role", ""))))
        if action == "active":
            return J(people.set_active(db, ctx, uid, bool(b.get("active"))))
        if action == "password":
            people.reset_password(db, ctx, uid, str(b.get("password", "")))
            return J({"ok": True})
        if action == "pin":
            people.set_pin(db, ctx, uid, str(b.get("pin", "")))
            return J({"ok": True})
        raise ValidationFailed("عملية غير معروفة")

    @handler
    async def role_discount(req):
        ctx = ctx_of(req)
        people.set_role_discount(db, ctx, req.path_params["code"], (await body(req)).get("max_discount_bp"))
        return J({"roles": people.roles(db, ctx)})

    @handler
    async def notifications_list(req):
        ctx = ctx_of(req)
        return J(notify.listing(db, ctx, unread_only=req.query_params.get("unread") == "1",
                                limit=_qlimit(req, 50, 200)))

    @handler
    async def notifications_read(req):
        ctx = ctx_of(req)
        ids = (await body(req)).get("ids")
        notify.mark_read(db, ctx, ids)
        return J({"unread": notify.unread_count(db, ctx)})

    @handler
    async def backup_status(req):
        ctx = ctx_of(req)
        if req.method == "PUT":
            autob.update_settings(ctx, await body(req))
        return J(autob.status(ctx))

    @handler
    async def backup_run(req):
        ctx = ctx_of(req)
        ctx.require("settings.manage")
        st = await run_in_threadpool(lambda: autob.run("manual", force=True))
        if not st.get("last_ok"):
            raise Conflict(f"النسخة فشلت: {st.get('last_error')}", code="backup_failed")
        return J(autob.status(ctx))

    @handler
    async def sales_settings(req):
        from ..sales.service import LINE_ORDERS, line_order, return_window_days
        ctx = ctx_of(req)
        if req.method == "PUT":
            ctx.require("settings.manage")
            b = await body(req)
            if "line_order" in b:
                if b["line_order"] not in LINE_ORDERS:
                    raise ValidationFailed("اختر ترتيب الأصناف")
                with db.transaction():
                    before = line_order(db, ctx.org_id)
                    db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'line_order', ?)"
                               " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (ctx.org_id, b["line_order"]))
                    audit.record(db, ctx, "settings.line_order", "organization", ctx.org_id,
                                 before={"order": before}, after={"order": b["line_order"]})
                if "return_window_days" not in b:
                    return J({"return_window_days": return_window_days(db, ctx.org_id), "line_order": line_order(db, ctx.org_id)})
            try:
                days = whole_number(b.get("return_window_days"))
            except (TypeError, ValueError):
                raise ValidationFailed("اكتب عدد الأيام")
            if not 0 <= days <= 365:
                raise ValidationFailed("مدة المرتجع من 0 إلى 365 يومًا")
            with db.transaction():
                before = return_window_days(db, ctx.org_id)
                db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'return_window_days', ?)"
                           " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (ctx.org_id, str(days)))
                audit.record(db, ctx, "settings.return_window", "organization", ctx.org_id,
                             before={"days": before}, after={"days": days})
        return J({"return_window_days": return_window_days(db, ctx.org_id), "line_order": line_order(db, ctx.org_id)})

    # ------------------------------------------------------------- catalog
    @handler
    async def items_list(req):
        ctx = ctx_of(req)
        limit = _qlimit(req, 20, 200)
        # the sale screen (update 1, owner): items with stock first, and those with none left out unless asked
        stock_first = req.query_params.get("stock") in ("first", "only")
        only_stock = req.query_params.get("stock") == "only"
        # the stock filter looks at up to 500 name matches, not the first 10x (Codex on #400)
        res = item_search(db, ctx, req.query_params.get("q", ""), 500 if stock_first else limit,
                          category=req.query_params.get("category") or None)
        cats = {c["id"]: c for c in db.all("SELECT id, name, mark, no_return FROM categories WHERE org_id=?", (ctx.org_id,))}
        for r in res["items"]:
            c = cats.get(r.get("category_id"))
            r["category"], r["mark"], r["no_return"] = (c["name"], c["mark"], c["no_return"]) if c else (None, None, 0)
        rows = res["items"]
        if rows and ctx.can("inventory.read") and ctx.branch_id:
            ids = [r["id"] for r in rows]
            qty = {r["item_id"]: r["q"] for r in db.all(
                f"SELECT item_id, sum(qty) q FROM stock_balances WHERE org_id=? AND branch_id=?"
                f" AND item_id IN ({','.join('?' * len(ids))}) GROUP BY item_id",
                (ctx.org_id, ctx.branch_id, *ids))}
            from ..sales.service import local_today as _today_local
            # U2-35: the nearest expiry still to come of what is on the shelf
            soon = {r["item_id"]: r["e"] for r in db.all(
                f"SELECT sb.item_id, min(b.expiry_date) e FROM stock_balances sb JOIN batches b ON b.id=sb.batch_key"
                f" WHERE sb.org_id=? AND sb.branch_id=? AND sb.qty>0 AND b.expiry_date>=?"
                f" AND sb.item_id IN ({','.join('?' * len(ids))}) GROUP BY sb.item_id",
                (ctx.org_id, ctx.branch_id, _today_local().isoformat(), *ids))}
            for r in rows:
                r["stock_qty"] = qty.get(r["id"], 0)     # base units
                r["nearest_expiry"] = soon.get(r["id"])
        hidden_no_stock = 0
        if stock_first and rows and "stock_qty" in rows[0]:
            has = lambda r: bool(r.get("is_service")) or (r["stock_qty"] or 0) > 0      # noqa: E731
            rows = sorted(rows, key=lambda r: not has(r))                          # stable: the search order inside
            if only_stock:
                hidden_no_stock = sum(not has(r) for r in rows)
                rows = [r for r in rows if has(r)]
        rows = rows[:limit]
        hint = res["hint"]
        if not hint and rows and all(r.get("match") == "spelling" for r in rows):
            hint = {"kind": "spelling"}          # only spelling guesses left: Enter never adds one alone (Codex #400)
        if req.query_params.get("meta") == "1":
            return J({"items": rows, "hint": hint, "hidden_no_stock": hidden_no_stock})
        return J(rows)

    @handler
    async def items_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        iid = catalog.create_item(db, ctx, b.get("item") or {}, barcodes=b.get("barcodes") or [],
                                  force=bool(b.get("force")))
        return J(catalog.get_item(db, ctx, iid), status_code=201)

    @handler
    async def item_get(req):
        ctx = ctx_of(req)
        item = catalog.get_item(db, ctx, req.path_params["id"])
        if ctx.can("inventory.read"):
            item["stock"] = inventory.stock_for_item(db, ctx, item["id"])
        return J(item)

    @handler
    async def item_update(req):
        ctx = ctx_of(req)
        return J(catalog.update_item(db, ctx, req.path_params["id"], await body(req)))

    @handler
    async def suppliers_list(req):
        return J(catalog.list_suppliers(db, ctx_of(req)))

    @handler
    async def suppliers_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        allowed = {"name", "supplier_type", "base_discount_local_bp", "base_discount_imported_bp",
                   "payment_terms", "phone"}
        sid = catalog.create_supplier(db, ctx, **{k: v for k, v in b.items() if k in allowed})
        return J(catalog.get_supplier(db, ctx, sid), status_code=201)

    # ------------------------------------------------------------- purchases
    @handler
    async def purchases_list(req):
        qp = req.query_params
        return J(purch.list(ctx_of(req), qp.get("status") or None, date_from=qp.get("from") or None,
                            date_to=qp.get("to") or None, supplier_id=qp.get("supplier") or None))

    @handler
    async def purchases_import(req):
        ctx = ctx_of(req)
        ctype = req.headers.get("content-type", "")
        if "multipart/form-data" not in ctype:
            raise ValidationFailed("ارفع الملف كـ multipart/form-data")
        _length_guard(req, s.max_upload_bytes + 64 * 1024, required=True)
        form = await req.form(max_files=1, max_fields=5)
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise ValidationFailed("اختر ملف الفاتورة")
        try:
            data = await up.read(s.max_upload_bytes + 1)
        finally:
            await up.close()
        inv_id = await run_in_threadpool(purch.import_file, ctx, data, up.filename or "upload",
                                         form.get("supplier_id") or None)
        return J(purch.get(ctx, inv_id), status_code=201)

    @handler
    async def purchases_manual(req):
        ctx = ctx_of(req)
        b = await body(req)
        inv_id = purch.create_manual(ctx, supplier_id=b.get("supplier_id"))
        head = {k: b[k] for k in ("invoice_no", "invoice_date") if b.get(k)}
        if head:
            try:
                purch.update_header(ctx, inv_id, head, purch.get(ctx, inv_id)["invoice"]["version"])
            except DomainError:      # e.g. duplicate invoice number: don't leave an empty draft behind
                purch.cancel(ctx, inv_id, "إلغاء تلقائي: بيانات الفاتورة مرفوضة عند الإنشاء", None)
                raise
        return J(purch.get(ctx, inv_id), status_code=201)

    @handler
    async def purchase_get(req):
        return J(purch.get(ctx_of(req), req.path_params["id"]))

    @handler
    async def purchase_header(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.update_header(ctx, req.path_params["id"], b.get("changes") or {},
                                     b.get("version")))

    @handler
    async def line_add(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.add_line(ctx, req.path_params["id"], b.get("line") or {}, b.get("version")))

    @handler
    async def line_update(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.update_line(ctx, req.path_params["id"], req.path_params["line_id"],
                                   b.get("changes") or {}, b.get("version")))

    @handler
    async def line_delete(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.delete_line(ctx, req.path_params["id"], req.path_params["line_id"],
                                   b.get("version")))

    @handler
    async def line_split(req):
        ctx = ctx_of(req)
        b = await body(req)
        try:
            qty = whole_number(b.get("qty"))
        except (TypeError, ValueError):
            raise ValidationFailed("الكمية لازم رقم صحيح")
        return J(purch.split_line(ctx, req.path_params["id"], req.path_params["line_id"], qty, b.get("expiry_date"),
                                  b.get("batch_no"), b.get("version")))

    @handler
    async def line_merge(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.merge_lines(ctx, req.path_params["id"], req.path_params["line_id"], str(b.get("other_line", "")),
                                   b.get("version")))

    @handler
    async def line_new_item(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.create_item_from_line(ctx, req.path_params["id"], req.path_params["line_id"],
                                             b.get("item") or {}, b.get("version"),
                                             force=bool(b.get("force"))))

    @handler
    async def purchase_ack(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.acknowledge(ctx, req.path_params["id"], str(b.get("code", "")),
                                   str(b.get("reason", "")), b.get("line_id"), b.get("version")))

    @handler
    async def purchase_approve(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.approve(ctx, req.path_params["id"], b.get("version")))

    @handler
    async def purchase_cancel(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.cancel(ctx, req.path_params["id"], str(b.get("reason", "")),
                              b.get("version")))

    @handler
    async def file_get(req):
        ctx = ctx_of(req)
        data, mime, name = purch.get_file(ctx, req.path_params["id"])
        if mime not in ("application/pdf", "image/png", "image/jpeg", "image/webp"):
            # spreadsheets/CSV are downloaded, never rendered inline
            return Response(data, media_type="application/octet-stream", headers={
                "Content-Disposition": "attachment; filename=source" + Path(name).suffix})
        return Response(data, media_type=mime, headers={
            "Content-Disposition": "inline", "Cache-Control": "private, max-age=3600"})

    # ------------------------------------------------------------- data import
    async def _upload(req) -> tuple[bytes, str, dict]:
        if "multipart/form-data" not in req.headers.get("content-type", ""):
            raise ValidationFailed("ارفع الملف كـ multipart/form-data")
        _length_guard(req, s.max_upload_bytes + 64 * 1024, required=True)
        form = await req.form(max_files=1, max_fields=8)
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise ValidationFailed("اختر الملف")
        try:
            data = await up.read(s.max_upload_bytes + 1)
        finally:
            await up.close()
        if len(data) > s.max_upload_bytes:
            raise ValidationFailed("حجم الملف أكبر من المسموح")
        return data, up.filename or "upload", {k: v for k, v in form.items() if k != "file"}

    @handler
    async def import_analyze(req):
        ctx = ctx_of(req)
        data, name, fields = await _upload(req)
        try:
            mapping = json.loads(fields["mapping"]) if fields.get("mapping") else None
            options = {"qty_unit": fields.get("qty_unit") or "pack"}
            if fields.get("header_row"):
                options["header_row"] = int(fields["header_row"])
        except (TypeError, ValueError):
            raise ValidationFailed("قيم غير صحيحة")
        if mapping is not None and not (isinstance(mapping, dict) and all(
                k.isdigit() and isinstance(v, str) for k, v in mapping.items())):
            raise ValidationFailed("قيم غير صحيحة")
        run = dimp.analyze(ctx, data, name, kind=fields.get("kind") or "items", mapping=mapping,
                           source_system=fields.get("source_system") or "generic", options=options)
        return J(dimp.get(ctx, run), status_code=201)

    @handler
    async def import_get(req):
        return J(dimp.get(ctx_of(req), req.path_params["id"], req.query_params.get("action")))

    @handler
    async def import_decide(req):
        ctx = ctx_of(req)
        b = await body(req)
        dimp.decide(ctx, req.path_params["id"], str(b.get("row_id", "")), str(b.get("decision", "")))
        return J({"ok": True})

    @handler
    async def import_apply(req):
        ctx = ctx_of(req)
        return J(dimp.apply(ctx, req.path_params["id"]))

    @handler
    async def import_discard(req):
        ctx = ctx_of(req)
        dimp.discard(ctx, req.path_params["id"])
        return J({"ok": True})

    @handler
    async def import_template(req):
        ctx_of(req)
        kind = req.path_params["kind"]
        return Response(template_xlsx(kind), media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            headers={"Content-Disposition": f"attachment; filename=template-{kind}.xlsx"})

    @handler
    async def export_items(req):
        ctx = ctx_of(req)
        return Response(export_catalog(db, ctx), media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            headers={"Content-Disposition": "attachment; filename=items-export.xlsx"})

    # ------------------------------------------------------------- safety
    @handler
    async def safety_check(req):
        ctx = ctx_of(req)
        b = await body(req)
        lines = [l for l in (b.get("lines") or []) if isinstance(l, dict) and l.get("item_id")]
        if not lines:
            raise ValidationFailed("أرسل الأصناف المراد فحصها")
        findings = safety.check(ctx, lines, context_type="check")
        return J({"findings": [f.as_dict() for f in findings]})

    @handler
    async def safety_ingredients(req):
        ctx = ctx_of(req)
        b = await body(req)
        ings = [(str(i.get("name", "")), i.get("strength")) for i in (b.get("ingredients") or [])
                if isinstance(i, dict)]
        safety.set_item_ingredients(ctx, req.path_params["id"], ings, b.get("source_id"))
        if b.get("verify"):
            safety.verify_item_ingredients(ctx, req.path_params["id"])
        return J({"ok": True})

    @handler
    async def safety_rules_list(req):
        ctx = ctx_of(req)
        ctx.require("safety.read")
        rows = db.all("SELECT r.*, s.name source_name, s.version source_version FROM safety_rules r"
                      " LEFT JOIN knowledge_sources s ON s.id=r.source_id"
                      " WHERE r.org_id IS NULL OR r.org_id=? ORDER BY r.created_at DESC", (ctx.org_id,))
        return J([dict(r) for r in rows])

    @handler
    async def safety_rule_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        rid = safety.create_rule(ctx, rule_type=str(b.get("rule_type")), severity=str(b.get("severity")),
                                 title=str(b.get("title", "")), explanation=str(b.get("explanation", "")),
                                 params=b.get("params") or {}, source_id=b.get("source_id"),
                                 source_ref=b.get("source_ref"), supersedes=b.get("supersedes"))
        return J({"id": rid}, status_code=201)

    @handler
    async def safety_rule_approve(req):
        ctx = ctx_of(req)
        b = await body(req)
        safety.approve_rule(ctx, req.path_params["id"], self_approval_reason=b.get("reason"))
        return J({"ok": True})

    @handler
    async def safety_rule_disable(req):
        ctx = ctx_of(req)
        b = await body(req)
        safety.disable_rule(ctx, req.path_params["id"], str(b.get("reason", "")))
        return J({"ok": True})

    @handler
    async def safety_source_add(req):
        ctx = ctx_of(req)
        b = await body(req)
        sid = safety.add_source(ctx, name=str(b.get("name", "")), version=str(b.get("version", "")),
                                license=str(b.get("license", "")), publisher=b.get("publisher"),
                                url=b.get("url"), published_at=b.get("published_at"),
                                notes=b.get("notes"))
        return J({"id": sid}, status_code=201)

    # ------------------------------------------------------------- recalls
    @handler
    async def recall_extract(req):
        ctx_of(req).require("items.read")
        b = await body(req)
        return J({"batches": extract_batches(str(b.get("text", ""))[:20000])})

    @handler
    async def recall_add(req):
        ctx = ctx_of(req)
        b = await body(req)
        nid = recalls.add_notice(ctx, title=str(b.get("title", "")), items=b.get("items") or [],
                                 notice_no=b.get("notice_no"), notice_date=b.get("notice_date"),
                                 url=b.get("url"), raw_text=b.get("raw_text"))
        return J({"id": nid, "alerts": recalls.alerts(ctx)}, status_code=201)

    @handler
    async def recall_alerts(req):
        ctx = ctx_of(req)
        st = req.query_params.get("status", "pending")
        return J(recalls.alerts(ctx, None if st == "all" else st))

    @handler
    async def recall_decide(req):
        ctx = ctx_of(req)
        b = await body(req)
        recalls.decide(ctx, req.path_params["id"], str(b.get("status", "")), b.get("note"))
        return J({"ok": True})

    # ------------------------------------------------------------- counting
    @handler
    async def counts_list(req):
        ctx = ctx_of(req)
        ctx.require("inventory.count")
        rows = db.all("SELECT id, title, scope, status, blind, created_at FROM count_sessions"
                      " WHERE org_id=? ORDER BY created_at DESC LIMIT 50", (ctx.org_id,))
        return J([dict(r) for r in rows])

    @handler
    async def counts_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        scope, ids = b.get("scope", "full"), b.get("item_ids")
        if scope == "category":            # count one section: its items, stored as an items-scope count
            cid = str(b.get("category_id") or "")
            ids = [r["id"] for r in db.all("SELECT id FROM items WHERE org_id=? AND status='active' AND category_id=?",
                                            (ctx.org_id, cid))]
            if not ids:
                raise ValidationFailed("هذا القسم ليس به أصناف")
            scope = "items"
        sid = counting.create(ctx, title=str(b.get("title", "")), scope=scope,
                              locations=b.get("locations"), item_ids=ids,
                              blind=bool(b.get("blind", True)))
        return J({"id": sid}, status_code=201)

    @handler
    async def counts_entry(req):
        ctx = ctx_of(req)
        b = await body(req)
        try:
            qty = whole_number(b.get("qty"))          # a counted 2.5 is refused, never posted as 2
        except (TypeError, ValueError):
            raise ValidationFailed("الكمية غير صحيحة")
        return J(counting.record(ctx, req.path_params["id"], item_id=str(b.get("item_id", "")),
                                 qty=qty, batch_id=b.get("batch_id"),
                                 location=str(b.get("location") or ""), mode=b.get("mode", "set"),
                                 recount=bool(b.get("recount")), device=req.headers.get("user-agent", "")[:80]))

    @handler
    async def counts_variances(req):
        return J(counting.variances(ctx_of(req), req.path_params["id"]))

    @handler
    async def counts_approve(req):
        ctx = ctx_of(req)
        b = await body(req)
        reasons = b.get("reasons") if isinstance(b.get("reasons"), dict) else None
        zero = b.get("zero_uncounted", False)
        if not isinstance(zero, bool):       # the string "false" was truthy and zeroed every uncounted item (#324 X20)
            raise ValidationFailed("قيمة تصفير الأصناف غير المجرودة غير صحيحة")
        return J(counting.approve(ctx, req.path_params["id"], zero_uncounted=zero,
                                  reason=b.get("reason"), reasons=reasons,
                                  shortage_reason=b.get("shortage_reason") or "lost", zero_reason=b.get("zero_reason") or "lost",
                                  uncounted_item_ids=b.get("uncounted_item_ids")))

    @handler
    async def counts_cancel(req):
        ctx = ctx_of(req)
        counting.cancel(ctx, req.path_params["id"])
        return J({"ok": True})

    # ------------------------------------------------------------- procurement
    def _hundredths(v) -> int:
        """Pounds or percent as typed ("12.5", 12.5, 30) -> whole hundredths (piasters / basis points).
        More than two decimals, non-numbers and booleans are refused, never rounded."""
        if isinstance(v, bool) or v is None:
            raise ValueError("not a number")
        d = Decimal(str(v).strip()) * 100
        if not d.is_finite() or d.adjusted() > 15:      # "1e10000000": int() would spend seconds building it
            raise ValueError("out of range")
        if d != d.to_integral_value():
            raise ValueError("more than two decimals")
        return int(d)

    def _sugg_params(req):
        qp = req.query_params
        try:
            out = {"cover_days": int(qp.get("cover_days", 30)), "lead_days": int(qp.get("lead_days", 2)),
                   "safety_days": int(qp.get("safety_days", 5)), "window_days": int(qp.get("window_days", 90))}
        except ValueError:
            raise ValidationFailed("قيم غير صحيحة")
        # the screen's own limits (min 1/0/0/7); the top ends keep a typo from overflowing a date (500)
        bounds = {"cover_days": (1, 730), "lead_days": (0, 365), "safety_days": (0, 365), "window_days": (7, 3650)}
        if any(not lo <= out[k] <= hi for k, (lo, hi) in bounds.items()):
            raise ValidationFailed("قيم غير صحيحة: التغطية 1–730 يوم، التوريد والأمان 0–365، فترة الاستهلاك 7–3650")
        return out

    @handler
    async def proc_suggest(req):
        ctx = ctx_of(req)
        return J(await off_loop(Procurement(db).suggest, ctx, **_sugg_params(req)))

    @handler
    async def proc_suggest_xlsx(req):
        ctx = ctx_of(req)
        rows = Procurement(db).suggest(ctx, **_sugg_params(req))
        return Response(suggestions_xlsx(rows, db, ctx), media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            headers={"Content-Disposition": "attachment; filename=order-suggestions.xlsx"})

    @handler
    async def proc_smart(req):
        from ..procurement import smart_order
        ctx, qp = ctx_of(req), req.query_params
        try:
            dear = _hundredths(qp.get("expensive_from") or "300")
        except (TypeError, ValueError, ArithmeticError):
            raise ValidationFailed("قيم غير صحيحة")
        if not 0 <= dear <= 10_000_000:
            raise ValidationFailed("حد الصنف الغالي غير صحيح")
        return J(await off_loop(smart_order.smart_order, db, ctx, scope=qp.get("scope") or "default",
                                expensive_from=dear, **_sugg_params(req)))

    @handler
    async def proc_order_xlsx(req):
        from ..procurement import smart_order
        b = await body(req)
        return Response(smart_order.order_xlsx(db, ctx_of(req), b.get("groups")), media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            headers={"Content-Disposition": "attachment; filename=order-by-supplier.xlsx"})

    @handler
    async def proc_health(req):
        return J(await off_loop(Procurement(db).stock_health, ctx_of(req)))

    @handler
    async def proc_supplier_costs(req):
        return J(await off_loop(Procurement(db).supplier_costs, ctx_of(req), req.path_params["id"]))

    @handler
    async def proc_offer(req):
        ctx = ctx_of(req)
        b = await body(req)
        try:                       # typed in pounds and percent; exact, never through float (2.5 packs is refused)
            packs = whole_number(b.get("packs", 0))
            bp = _hundredths(b.get("discount_pct", -1))
            cash = _hundredths(b["cash_available"]) if b.get("cash_available") not in (None, "") else None
        except (TypeError, ValueError, ArithmeticError):
            raise ValidationFailed("قيم غير صحيحة")
        return J(Procurement(db).evaluate_offer(ctx, str(b.get("item_id", "")), packs=packs, discount_bp=bp,
                                                expiry_date=b.get("expiry_date") or None, cash_available=cash))

    @handler
    async def proc_consumption(req):
        ctx = ctx_of(req)
        data, name, fields = await _upload(req)
        return J(import_consumption(db, ctx, data, name, period_start=str(fields.get("period_start", "")),
                                    period_end=str(fields.get("period_end", "")),
                                    qty_unit=fields.get("qty_unit") or "pack",
                                    dry_run=fields.get("apply") != "1"))

    @handler
    async def proc_deadstock(req):
        """Dead/slow stock from 3 E-Stock exports (nothing is stored). ?format=xlsx downloads."""
        ctx = ctx_of(req)
        ctx.require("inventory.read")
        ctx.require("costs.read")
        if "multipart/form-data" not in req.headers.get("content-type", ""):
            raise ValidationFailed("ارفع الملفات كـ multipart/form-data")
        _length_guard(req, MAX_UPLOAD_FILES * s.max_upload_bytes + 64 * 1024, required=True)
        form = await req.form(max_files=MAX_UPLOAD_FILES, max_fields=4)
        files = {}
        for k in ("stock", "sales", "purchases"):
            up = form.get(k)
            if up is None or not hasattr(up, "read"):
                raise ValidationFailed("يلزم الملفات الثلاثة: الأرصدة، والمبيعات، والمشتريات")
            files[k] = await up.read(s.max_upload_bytes + 1)
            await up.close()
            if len(files[k]) > s.max_upload_bytes:
                raise ValidationFailed("حجم الملف أكبر من المسموح")
        rep = await run_in_threadpool(deadstock.compute, files["stock"], files["sales"], files["purchases"],
                                      store=str(form.get("store") or "الصيدلية"))
        if req.query_params.get("format") == "xlsx":
            return Response(deadstock.to_xlsx(rep), media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                headers={"Content-Disposition": f"attachment; filename=deadstock-{rep['as_of']}.xlsx"})
        return J(rep)

    # ------------------------------------------------------------- sales
    def _int(v, name):
        try:
            return whole_number(0 if v is None or v == "" else v)
        except (TypeError, ValueError):
            raise ValidationFailed(f"قيمة غير صحيحة: {name}")

    @handler
    async def sales_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        sales = Sales(db)
        return J(sales.get(ctx, sales.create(ctx, b.get("customer_id"))), status_code=201)

    @handler
    async def sales_list(req):
        qp = req.query_params
        no = qp.get("no")
        if no is not None and not no.isdigit():
            raise ValidationFailed("رقم الفاتورة يجب أن يكون رقمًا")
        return J(Sales(db).list_sales(ctx_of(req), day=qp.get("day") or None, sale_no=int(no) if no else None,
                                      date_from=qp.get("from") or None, date_to=qp.get("to") or None))

    @handler
    async def sales_get(req):
        return J(Sales(db).get(ctx_of(req), req.path_params["id"]))

    @handler
    async def sales_add_line(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(Sales(db).add_line(ctx, req.path_params["id"], str(b.get("item_id", "")),
                                    packs=_int(b.get("packs"), "packs"), units=_int(b.get("units"), "units"),
                                    discount_bp=_int(b.get("discount_bp"), "discount"),
                                    client_ref=(str(b["client_ref"])[:64] if b.get("client_ref") else None)))

    @handler
    async def sales_remove_line(req):
        ctx = ctx_of(req)
        return J(Sales(db).remove_line(ctx, req.path_params["id"], req.path_params["line_id"]))

    @handler
    async def sales_drafts(req):
        return J({"drafts": Sales(db).list_drafts(ctx_of(req))})

    @handler
    async def sales_details(req):
        ctx = ctx_of(req)
        return J(Sales(db).set_details(ctx, req.path_params["id"], await body(req)))

    @handler
    async def sales_discard(req):
        Sales(db).discard(ctx_of(req), req.path_params["id"])
        return J({"ok": True})

    @handler
    async def sales_update_line(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(Sales(db).update_line(ctx, req.path_params["id"], req.path_params["line_id"],
                                       qty_base=b.get("qty_base"), discount_bp=b.get("discount_bp"),
                                       discount_amount=b.get("discount_amount")))

    @handler
    async def sales_invoice_discount(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(Sales(db).set_invoice_discount(ctx, req.path_params["id"], amount=b.get("amount"), bp=b.get("bp")))

    @handler
    async def sales_delivery(req):
        ctx = ctx_of(req)
        return J(Sales(db).delivery_status(ctx, req.path_params["id"], str((await body(req)).get("status", ""))))

    @handler
    async def deliveries_open(req):
        return J({"deliveries": Sales(db).open_deliveries(ctx_of(req))})

    @handler
    async def channels_list(req):
        return J({"channels": pay_channels.listing(db, ctx_of(req))})

    @handler
    async def channels_save(req):
        ctx = ctx_of(req)
        return J({"channels": pay_channels.save(db, ctx, await body(req), req.path_params.get("id"))})

    @handler
    async def sales_check(req):
        ctx = ctx_of(req)
        return J({"findings": [f.as_dict() for f in Sales(db).safety_check(ctx, req.path_params["id"])]})

    @handler
    async def sales_complete(req):
        ctx = ctx_of(req)
        b = await body(req)
        decisions = {int(k): str(v) for k, v in (b.get("safety_decisions") or {}).items()}
        # cash_given: what the customer handed over, only to print the change due (F27); never stored.
        # Checked before the sale is saved so a bad value can never fail an already-saved sale.
        given = _int(b.get("cash_given"), "المبلغ المدفوع من العميل")
        if not 0 <= given <= 100_000_000:
            raise ValidationFailed("المبلغ المدفوع من العميل غير صحيح")
        # an amount typed below the cash part is a short tender, not a full payment: refused before posting so
        # the cashier corrects it or splits the payment (#324 M2-03); nothing typed (0) prints no change
        pays = b.get("payments") if isinstance(b.get("payments"), list) else None
        cash_part = (sum(p.get("amount") for p in pays if isinstance(p, dict) and p.get("method") == "cash"
                         and isinstance(p.get("amount"), int) and not isinstance(p.get("amount"), bool))
                     if pays is not None else _int(b.get("paid_cash"), "cash"))
        if 0 < given < cash_part:
            raise ValidationFailed("المبلغ الذي دفعه العميل أقل من المطلوب نقدًا. صحّح المبلغ أو قسّم الدفع",
                                   code="cash_given_short", details={"given": given, "cash": cash_part})
        done = Sales(db).complete(ctx, req.path_params["id"], paid_cash=_int(b.get("paid_cash"), "cash"),
                                    paid_card=_int(b.get("paid_card"), "card"),
                                    on_credit=_int(b.get("on_credit"), "credit"),
                                    safety_decisions=decisions, version=b.get("version"),
                                    request_id=(str(b["request_id"])[:64] if b.get("request_id") else None),
                                    confirm_below_cost=bool(b.get("confirm_below_cost")),
                                    payments=b.get("payments") if isinstance(b.get("payments"), list) else None)
        if not done.get("replayed"):        # the receipt and the drawer pulse happen once per sale (#81)
            done["devices"] = await run_in_threadpool(printing.checkout_devices, db, ctx, done, b.get("receipt") is True,
                                                      given)
        return J(done)

    @handler
    async def sales_return(req):
        ctx = ctx_of(req)
        b = await body(req)
        raw = b.get("lines")
        if not isinstance(raw, dict) or not raw:
            raise ValidationFailed("اختر الأصناف والكميات المرتجعة", code="empty_return")
        lines = {str(k): _int(v, "qty") for k, v in raw.items()}
        return J(Sales(db).return_items(ctx, req.path_params["id"], lines,
                                        refund_method=str(b.get("refund_method", "")),
                                        reason=str(b.get("reason", ""))))

    @handler
    async def sales_exchange(req):
        """Part 3 item 6a: a return and its replacement sale in one operation."""
        ctx = ctx_of(req)
        b = await body(req)
        raw = b.get("lines")
        if not isinstance(raw, dict) or not raw:
            raise ValidationFailed("اختر الأصناف والكميات المرتجعة", code="empty_return")
        lines = {str(k): _int(v, "qty") for k, v in raw.items()}
        if b.get("preview"):               # the three totals before the cashier confirms; writes nothing
            sales = Sales(db)
            if b.get("new_sale_id"):          # both totals in one simulation, the returned units back first
                r_value, n_total = sales.return_value(ctx, req.path_params["id"], lines, new_sid=str(b["new_sale_id"]),
                                                      refund_method=str(b.get("refund_method", "")))
            else:
                r_value, n_total = sales.return_value(ctx, req.path_params["id"], lines,
                                                      refund_method=str(b.get("refund_method", ""))), 0
            return J({"returned": r_value, "new_total": n_total, "net": n_total - r_value})
        decisions = {int(k): str(v) for k, v in (b.get("safety_decisions") or {}).items()}
        done = Sales(db).exchange(ctx, req.path_params["id"], lines, str(b.get("new_sale_id", "")),
                                  refund_method=str(b.get("refund_method", "")), pay_method=str(b.get("pay_method", "cash")),
                                  reason=str(b.get("reason", "")), channel_id=b.get("channel_id") or None,
                                  version=b.get("version"),
                                  request_id=(str(b["request_id"])[:64] if b.get("request_id") else None),
                                  safety_decisions=decisions, confirm_below_cost=bool(b.get("confirm_below_cost")),
                                  expected_new_total=(_int(b["expected_new_total"], "expected_new_total")
                                                      if b.get("expected_new_total") is not None else None))
        if not done.get("replayed"):        # the receipt, and the drawer only when cash really moves
            sale = done["sale"]
            if not (done["settled"]["pay"] and done["pay_method"] == "cash") and not (
                    done["settled"]["refund"] and done["refund_method"] == "cash"):
                sale = {**sale, "paid_cash": 0}
            done["devices"] = await run_in_threadpool(printing.checkout_devices, db, ctx, sale, b.get("receipt") is True, 0)
        return J(done)

    @handler
    async def item_branch_stock(req):
        from ..catalog import card as _card
        return J(_card.branch_stock(db, ctx_of(req), req.path_params["id"]))

    @handler
    async def returns_find(req):
        return J({"sales": await off_loop(Sales(db).find_for_return, ctx_of(req), req.query_params.get("q", ""))})

    @handler
    async def sales_void(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(Sales(db).void(ctx, req.path_params["id"], str(b.get("reason", ""))))

    @handler
    async def sales_summary(req):
        return J(Sales(db).day_summary(ctx_of(req), req.query_params.get("day")))

    @handler
    async def customers_list(req):
        ctx = ctx_of(req)
        ctx.require("sales.read")
        from ..sales import customers as cu
        where, wp, rank, rp = cu.search_clause(req.query_params.get("q", ""))   # update 1: code, titles, spelling
        # every customer, none hidden for E-Stock's flags (owner 21/11); a deleted one is tagged
        rows = db.all("SELECT c.id, c.name, c.name_alt, c.phone, c.phone2, c.phone3, c.address1, c.credit_limit,"
                      f" {cu.CODE_SQL} estock_code FROM customers c"
                      f" WHERE c.org_id=? AND ({where}) ORDER BY {rank}, c.name LIMIT 30",
                      (ctx.org_id, *wp, *rp))
        sales = Sales(db)
        return J([{**dict(r), "balance": sales.balance(ctx, r["id"])} for r in rows])

    @handler
    async def customers_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        cid = Sales(db).create_customer(ctx, name=str(b.get("name", "")), phone=b.get("phone") or None,
                                        credit_limit=_int(b.get("credit_limit"), "limit"), notes=b.get("notes"),
                                        **{k: b.get(k) for k in ("name_alt", "address1", "address2", "phone2", "phone3")})
        return J({"id": cid}, status_code=201)

    @handler
    async def customers_pay(req):
        ctx = ctx_of(req)
        ctx.require("customers.manage")
        b = await body(req)
        raw = await req.body()
        key = req.headers.get("idempotency-key", "").strip()
        rhash = hashlib.sha256(req.method.encode() + req.url.path.encode() + _query_part(req) + raw).hexdigest()

        def perform():
            resp = J({"balance": Sales(db).customer_payment(
                ctx, req.path_params["id"], _int(b.get("amount"), "amount"), b.get("note"),
                method=str(b.get("method", "cash")), channel_id=b.get("channel_id") or None)})
            return resp.status_code, resp.body.decode("utf-8")

        # Database-only operation; no await or external effect inside this transaction.
        status, payload, replayed = idempotency.run(
            db, ctx, key=key, method=req.method, path=req.url.path,
            request_hash=rhash, ttl_days=IDEMPOTENCY_TTL_DAYS, action=perform)
        resp = Response(payload, status_code=status, media_type="application/json")
        if replayed:
            resp.headers["Idempotent-Replay"] = "true"
        return resp

    @handler
    async def cash_open(req):
        ctx = ctx_of(req)
        b = await body(req)
        sid = CashDrawer(db).open(ctx, _int(b.get("opening_cash"), "opening_cash"))
        if not ctx.can("sales.read"):       # the shift is open: never a 403 after it committed (#324 M2-26)
            s = db.one("SELECT id, status, opening_cash, opened_at FROM cash_shifts WHERE id=? AND org_id=?",
                       (sid, ctx.org_id))
            return J({**dict(s), "blind": True}, status_code=201)
        return J(CashDrawer(db).summary(ctx, sid), status_code=201)

    @handler
    async def cash_current(req):
        return J(CashDrawer(db).summary(ctx_of(req)))

    @handler
    async def cash_move(req):
        ctx = ctx_of(req)
        # Recheck permissions even when the response was cached before revocation.
        ctx.require("cash.manage")
        ctx.require("sales.read")
        b = await body(req)
        raw = await req.body()
        key = req.headers.get("idempotency-key", "").strip()
        rhash = hashlib.sha256(req.method.encode() + req.url.path.encode() + _query_part(req) + raw).hexdigest()

        def perform():
            drawer = CashDrawer(db)
            drawer.movement(ctx, _int(b.get("amount"), "amount"), str(b.get("kind", "")), str(b.get("note", "")))
            resp = J(drawer.summary(ctx))
            return resp.status_code, resp.body.decode("utf-8")

        # All awaits are above: the transaction below never spans an async yield.
        status, payload, replayed = idempotency.run(
            db, ctx, key=key, method=req.method, path=req.url.path,
            request_hash=rhash, ttl_days=IDEMPOTENCY_TTL_DAYS, action=perform)
        # A stored response must not restore visibility removed since the write.
        resp = J(redact_summary(ctx, json.loads(payload)), status_code=status)
        if replayed:
            resp.headers["Idempotent-Replay"] = "true"
        return resp

    @handler
    async def cash_close(req):
        ctx = ctx_of(req)
        b = await body(req)
        more = {"float_left": _int(b["float_left"], "float_left") if b.get("float_left") not in (None, "") else None,
                "to_account": b.get("to_account") or None,
                "handover": b["handover"] if isinstance(b.get("handover"), bool) else None}
        shift = b.get("shift_id")
        if shift is not None and not isinstance(shift, str):
            raise ValidationFailed("الشيفت غير صحيح")
        # a count is a map of notes or an amount, never an empty list or nothing at all (#324 M2-19)
        if "denominations" in b:
            if not isinstance(b["denominations"], dict):
                raise ValidationFailed("عدد الفئات غير صحيح")
            return J(CashDrawer(db).close(ctx, note=b.get("note"), denominations=b["denominations"], shift_id=shift,
                                            **more))
        if b.get("counted_cash") in (None, ""):
            raise ValidationFailed("اكتب المبلغ المعدود في الخزينة")
        return J(CashDrawer(db).close(ctx, _int(b.get("counted_cash"), "counted_cash"), b.get("note"), shift_id=shift, **more))

    # ------------------------------------------------------------- money accounts (F105) and reconciliation (F108)
    @handler
    async def money_accounts_view(req):
        ctx = ctx_of(req)
        if req.method == "POST":
            b = await body(req)
            return J(money.create_account(db, ctx, str(b.get("name", "")), str(b.get("kind", "")),
                                          allow_negative=b.get("allow_negative") is True), status_code=201)
        return J({"accounts": await off_loop(money.accounts, db, ctx,
                                             include_inactive=req.query_params.get("all") == "1"),
                  "kinds": money.ACCOUNT_KINDS, "movement_kinds": money.KINDS})

    @handler
    async def money_account_one(req):
        ctx = ctx_of(req)
        if req.method == "PATCH":
            return J(money.update_account(db, ctx, req.path_params["id"], await body(req)))
        return J(money.get_account(db, ctx, req.path_params["id"]))

    @handler
    async def money_movements_view(req):
        qp = req.query_params
        return J(await off_loop(money.movements, db, ctx_of(req), account_id=qp.get("account") or None,
                                kind=qp.get("kind") or None, date_from=qp.get("from") or None,
                                date_to=qp.get("to") or None, limit=_int(qp.get("limit") or 500, "limit")))

    @handler
    async def money_statement_view(req):
        qp = req.query_params
        return J(await off_loop(money.statement, db, ctx_of(req), req.path_params["id"], qp.get("from") or None,
                                qp.get("to") or None))

    @handler
    async def money_summary_view(req):
        qp = req.query_params
        return J(await off_loop(money.summary, db, ctx_of(req), qp.get("from") or None, qp.get("to") or None,
                                qp.get("by") or "day"))

    @handler
    async def money_transfer_view(req):
        b = await body(req)
        return J(money.transfer(db, ctx_of(req), str(b.get("from", "")), str(b.get("to", "")),
                                _int(b.get("amount"), "amount"), b.get("note")), status_code=201)

    @handler
    async def money_owner_view(req):
        b = await body(req)
        return J(money.owner_movement(db, ctx_of(req), str(b.get("account", "")), _int(b.get("amount"), "amount"),
                                      str(b.get("kind", "")), b.get("note")), status_code=201)

    @handler
    async def money_count_view(req):
        b = await body(req)
        return J(money.count(db, ctx_of(req), req.path_params["id"], _int(b.get("counted"), "counted"),
                             b.get("reason")), status_code=201)

    @handler
    async def money_adjust_view(req):
        b = await body(req)
        return J(money.adjust_opening(db, ctx_of(req), req.path_params["id"], _int(b.get("correct_balance"), "amount"),
                                      str(b.get("reason") or "")), status_code=201)

    @handler
    async def money_routes_view(req):
        ctx = ctx_of(req)
        if req.method == "PUT":
            b = await body(req)
            return J({"routes": money.set_route(db, ctx, str(b.get("method", "")), b.get("channel_id") or None,
                                                b.get("account_id") or None)})
        return J({"routes": money.routes(db, ctx)})

    @handler
    async def reconcile_parties_view(req):
        qp = req.query_params
        return J({"parties": await off_loop(reconcile.parties, db, ctx_of(req), qp.get("party") or None,
                                            qp.get("q", "")), "types": reconcile.TYPES})

    @handler
    async def reconcile_record_view(req):
        b = await body(req)
        ctx = ctx_of(req)
        party, pid = str(b.get("party", "")), str(b.get("party_id", ""))
        if b.get("check"):
            return J(reconcile.check(db, ctx, party, pid, _int(b.get("statement_balance"), "statement_balance")))
        return J(reconcile.record(db, ctx, party, pid, _int(b.get("statement_balance"), "statement_balance"),
                                  program_balance=_int(b.get("program_balance"), "program_balance"),
                                  adj_type=b.get("adj_type") or None, note=b.get("note")), status_code=201)

    @handler
    async def opening_corrections_view(req):
        from ..dataimport import opening_balances as ob
        return J({"parties": await off_loop(ob.corrections, db, ctx_of(req), req.path_params["kind"],
                                            req.query_params.get("q", ""))})

    @handler
    async def opening_correct_view(req):
        from ..dataimport import opening_balances as ob
        ctx = ctx_of(req)
        kind, pid = req.path_params["kind"], req.path_params["id"]
        if req.method == "GET":
            return J({"history": ob.correction_history(db, ctx, kind, pid)})
        b = await body(req)
        if b.get("amount") in (None, "") or b.get("seen_opening") in (None, "") or b.get("seen_revision") in (None, ""):
            raise ValidationFailed("اكتب الرصيد الصحيح")
        return J(ob.correct(db, ctx, kind, pid, _int(b.get("amount"), "amount"),
                            seen_opening=_int(b.get("seen_opening"), "seen_opening"), reason=b.get("reason"),
                            seen_revision=_int(b.get("seen_revision"), "seen_revision")),
                 status_code=201)

    @handler
    async def reconcile_history_view(req):
        return J({"history": reconcile.history(db, ctx_of(req), req.path_params["party"], req.path_params["id"])})

    @handler
    async def cash_shifts(req):
        return J({"shifts": CashDrawer(db).recent(ctx_of(req))})

    @handler
    async def day_review_api(req):
        from ..reports.day_review import day_review
        return J(await off_loop(day_review, db, ctx_of(req), req.query_params.get("day") or None))

    @handler
    async def slow_requests(req):
        ctx = ctx_of(req)
        ctx.require("settings.manage")
        # only this pharmacy's requests (Codex P1 on #407); requests before sign-in carry no pharmacy and are not shown
        mine = [{k: v for k, v in x.items() if k != "org_id"} for x in SLOW_LOG if x.get("org_id") == ctx.org_id]
        return J({"threshold_ms": SLOW_MS, "requests": mine[::-1]})

    @handler
    async def cash_report(req):
        return J(CashDrawer(db).report(ctx_of(req), req.path_params["id"]))

    @handler
    async def cash_float_target(req):
        from ..sales import shift_tools
        ctx = ctx_of(req)
        if req.method == "POST":
            return J(shift_tools.save_target(db, ctx, (await body(req)).get("target")))
        ctx.require("cash.close")
        return J(shift_tools.get_target(db, ctx.org_id))

    @handler
    async def sales_change_fill(req):
        """«مفيش فكة» (U2-20): small in-stock items that fit in the customer's change."""
        from ..sales import change_fill
        ctx = ctx_of(req)
        try:
            change = int(req.query_params.get("change") or 0)
        except ValueError:
            raise ValidationFailed("الباقي غير صحيح")
        return J(await off_loop(change_fill.suggestions, db, ctx, change, req.query_params.get("sale") or None))

    @handler
    async def cash_float_preview(req):
        """Which notes stay in the drawer and which go to the treasury, for the count on the close screen."""
        from ..sales import shift_tools
        from ..sales.cash import count_total
        ctx = ctx_of(req)
        ctx.require("cash.close")
        b = await body(req)
        total, clean = count_total(b.get("denominations") or {})
        fl = b.get("float_left")
        if fl is not None:              # the closer typed his own float: show the notes for that amount (Codex P2)
            try:
                fl = whole_number(fl)
            except (TypeError, ValueError):
                raise ValidationFailed("الفكة غير صحيحة")
            if not 0 <= fl <= total:
                raise ValidationFailed("الفكة اللي هتفضل في الدرج أكبر من المعدود", code="float_too_large")
        return J(shift_tools.split_for(clean, shift_tools.get_target(db, ctx.org_id), fl))

    @handler
    async def cash_hunt(req):
        from ..sales import shift_tools
        return J(await off_loop(shift_tools.difference_hunt, db, ctx_of(req), req.path_params["id"]))

    @handler
    async def shift_report_page(req):
        """Closing report sized for the 80mm receipt printer (browser print)."""
        from ..sales.cash import DENOMINATIONS
        ctx = ctx_of(req)
        r = CashDrawer(db).report(ctx, req.path_params["id"])
        org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
        e = _html.escape
        m = lambda p: f"{(p or 0) / 100:,.2f}"  # noqa: E731
        from ..printing import settings as rs
        tf = rs.get(db, ctx.org_id)["time_format"]                                                        # #206
        t = lambda iso: rs.fmt_when(accounts.parse_iso(iso).astimezone(), tf) if iso else "—"  # noqa: E731
        k = r["cash_by_kind"]
        rows = [("رصيد البداية", r["opening_cash"]), ("مبيعات نقدي", k.get("sale", 0)),
                ("تحصيل آجل", k.get("customer_payment", 0)), ("مرتجع نقدي", k.get("refund", 0)),
                ("إلغاء فواتير", k.get("void", 0)), ("مصروفات من الخزينة", k.get("payout", 0)), ("إيداع", k.get("deposit", 0))]
        cash_rows = "".join(f"<tr><td>{a}</td><td>{m(v)}</td></tr>" for a, v in rows if v or a == "رصيد البداية")
        ch_rows = "".join(f"<tr><td>{'بطاقة' if c['method'] == 'card' else 'تحويل'} — {e(c['channel'])}</td><td>{m(c['amount'])}</td></tr>"
                          for c in r["by_channel"])
        den = r["counted_detail"] or {}
        den_rows = "".join(f"<tr><td>{d / 100:g} × {den[str(d)]}</td><td>{m(d * den[str(d)])}</td></tr>"
                           for d in DENOMINATIONS if den.get(str(d)))
        diff = r["difference"]
        res = ("" if r["status"] != "closed" else
               f"<tr class='t'><td>{'✓ مطابق' if not diff else ('✕ عجز' if diff < 0 else '⚠ زيادة')}</td><td>{m(abs(diff or 0))}</td></tr>")
        # #81: drawer-open commands (the printer cannot confirm the drawer physically opened)
        drawer = "".join(f"<tr><td><small>{t(d['at'])} {e(d.get('by') or '')} — {e(d.get('reason') or '')}"
                         f"{(' : ' + e(d['note'])) if d.get('note') else ''}{' (فشل)' if d.get('outcome') == 'failed' else ''}</small></td><td></td></tr>"
                         for d in r["drawer_manual"])
        drawer = (f"<hr><table><tr><td>فتح الدرج مع البيع النقدي</td><td>{r['drawer_auto_count']}</td></tr>"
                  f"<tr><td>فتح الدرج من غير بيع</td><td>{r['drawer_manual_count']}</td></tr>{drawer}</table>")
        # U2-28: duration, credit sales by customer, discounts by seller, notes kept / moved, signatures
        dur = ""
        if r.get("closed_at"):
            mins = int((accounts.parse_iso(r["closed_at"]) - accounts.parse_iso(r["opened_at"])).total_seconds() // 60)
            dur = f"<br>مدة الشيفت: {mins // 60} ساعة و{mins % 60} دقيقة"
        credit_rows = "".join(f"<tr><td>آجل فاتورة {c['sale_no']} — {e(c['customer'] or 'بدون اسم')}</td><td>{m(c['on_credit'])}</td></tr>"
                              for c in r.get("credit_sales") or [])
        disc_rows = "".join(f"<tr><td>خصم {e(d['name'] or '')} ({d['n']} فاتورة)</td><td>{m(d['total'])}</td></tr>"
                            for d in r.get("discount_by_user") or [])
        sp = r.get("float_split") or {}
        keep_rows = "".join(f"<tr><td>{int(d) / 100:g} × {n}</td><td>{m(int(d) * n)}</td></tr>"
                            for d, n in sorted((sp.get("keep") or {}).items(), key=lambda kv: -int(kv[0])))
        fl = r.get("float_left")
        kept = ""
        if r["status"] == "closed" and fl is not None:
            kept = (f"<hr><table><tr class='t'><td>الفكة اللي فضلت في الدرج</td><td>{m(fl)}</td></tr>{keep_rows}"
                    f"<tr class='t'><td>اتحوّل للخزنة</td><td>{m((r['counted_cash'] or 0) - fl)}</td></tr></table>")
        hint = ""
        if r["status"] == "closed" and diff:
            hint = "<p><small>دوّر على سبب الفرق من شاشة الشيفت ← «دوّر على الفرق» (قروش الفواتير، فتح الدرج، المرتجعات).</small></p>"
        page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>إغلاق الشيفت</title>
<link rel="stylesheet" href="{_static_v('receipt.css')}"></head><body>
<div class="r"><h1>{e(org)}</h1><p>تقرير إغلاق الشيفت<br>من {t(r['opened_at'])} ({e(r['opened_by_name'] or '')})<br>
إلى {t(r['closed_at'])} ({e(r['closed_by_name'] or '')}){dur}</p>
<table><tr><td>عدد الفواتير</td><td>{r['sales_count']}</td></tr><tr><td>إجمالي المبيعات</td><td>{m(r['sales_total'])}</td></tr>
{f"<tr><td>الخصومات</td><td>{m(r['discount_total'])}</td></tr>" if r['discount_total'] else ""}
{f"<tr><td>مرتجعات ({r['returns_count']})</td><td>{m(r['returns_total'])}</td></tr>" if r['returns_count'] else ""}
{f"<tr><td>منها مرتجع على البطاقة</td><td>{m(r['card_refunds'])}</td></tr>" if r['card_refunds'] else ""}
{f"<tr><td>منها مرتجع على حساب العميل</td><td>{m(r['credit_refunds'])}</td></tr>" if r['credit_refunds'] else ""}
{f"<tr><td>فواتير ملغاة</td><td>{r['voided_count']}</td></tr>" if r['voided_count'] else ""}</table><hr>
<table>{cash_rows}<tr class="t"><td>المتوقع في الخزينة</td><td>{m(r['expected_cash'])}</td></tr></table>
{f"<hr><table>{den_rows}</table>" if den_rows else ""}
<table><tr class="t"><td>المعدود</td><td>{m(r['counted_cash'])}</td></tr>{res}</table>
{f"<hr><table>{ch_rows}</table>" if ch_rows else ""}
{f"<table><tr><td>آجل</td><td>{m(r['credit_total'])}</td></tr>{credit_rows}</table>" if r['credit_total'] else ""}
{f"<hr><table>{disc_rows}</table>" if disc_rows else ""}
{kept}{hint}
{f"<p>ملاحظة: {e(r['close_note'])}</p>" if r.get('close_note') else ""}
{drawer}
<p class="f">توقيع الكاشير: ..................</p><p class="f">توقيع المستلم: ..................</p></div><script src="/static/print.js"></script></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    @handler
    async def print_receipt(req):
        ctx = ctx_of(req)
        await body(req)
        # a reprint never opens the drawer (#81): the automatic pulse belongs to the sale's completion only
        return J(await run_in_threadpool(printing.print_receipt, db, ctx, req.path_params["id"]))

    @handler
    async def print_drawer(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(await run_in_threadpool(printing.open_drawer, db, ctx, str(b.get("reason", "")),
                                         note=str(b.get("note", ""))))

    @handler
    async def print_printers(req):
        return J(await run_in_threadpool(printing.printers, db, ctx_of(req)))

    @handler
    async def print_test(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(await run_in_threadpool(printing.print_test, db, ctx, b.get("target") or None))

    @handler
    async def return_receipt(req):
        """Printed slip for a return: what came back, how much, how it was refunded, who and why."""
        from ..printing import settings as rs
        ctx = ctx_of(req)
        x = Sales(db).return_receipt(ctx, req.path_params["id"])
        r, sale = x["return"], x["sale"]
        cfg = rs.get(db, ctx.org_id)
        if req.query_params.get("w") in ("58", "80"):
            cfg["paper"] = int(req.query_params["w"])
        org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
        e = _html.escape
        m = lambda p: f"{(p or 0) / 100:,.2f}"  # noqa: E731

        def name(it):
            if it.get("hide_on_receipt"):       # the owner chose to keep this name off the paper
                return "—"
            if cfg["names"] == "ar":
                return e(it.get("name_ar") or it["name"])
            return e(it.get("name_en") or it["name"])
        rows = "".join(f"<tr><td>{name(l['item'])}<br><small>{l['returned_now']} {e(l['item']['base_unit'] if l['item']['base_unit'] not in (None, 'unit') else 'وحدة')}</small></td><td></td></tr>"
                       for l in x["lines"])
        when = rs.fmt_when(accounts.parse_iso(r["created_at"]).astimezone(), cfg["time_format"])
        sold = accounts.parse_iso(sale["completed_at"]).astimezone().strftime("%Y-%m-%d")
        MET = {"cash": "نقدًا من الخزينة", "card": "بالبطاقة", "credit": "على حساب العميل"}
        head = "".join(f"<p>{e(v)}</p>" for v in cfg["lines"] if v)
        info = [f"إيصال مرتجع — فاتورة رقم {sale['sale_no']}", f"بيعت <bdi dir='ltr'>{sold}</bdi> · أُرجعت <bdi dir='ltr'>{when}</bdi>",
                f"سجّله: {e(r['by_name'] or '')}"]
        if sale.get("customer"):
            info.append(f"العميل: {e(sale['customer']['name'])}")
        page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>مرتجع {sale['sale_no']}</title>
<link rel="stylesheet" href="{_static_v('receipt.css')}">{f'<link rel="stylesheet" href="{_static_v("receipt-58.css")}">' if cfg['paper'] == 58 else ''}</head><body class="w{cfg['paper']}">
<div class="r">{f"<p>{e(cfg['header'])}</p>" if cfg['header'] else ""}<h1>{e(cfg['title'] or org)}</h1>{head}<p class="tag">مرتجع</p><p>{'<br>'.join(info)}</p><hr>
<table>{rows}</table><hr><table>
<tr class="t"><td>المبلغ المرتجع</td><td>{m(r['total'])}</td></tr><tr><td>رُدّ</td><td>{MET.get(r['refund_method'], '')}</td></tr></table>
<p class="s">السبب: {e(r['reason'])}</p><p class="sig">توقيع العميل: ................</p></div><script src="/static/print.js"></script></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    @handler
    async def receipt(req):
        """Printable receipt for 80 / 58 mm thermal printers (browser print), as set in the program page."""
        from ..printing import settings as rs
        from ..sales.service import return_window_days
        ctx = ctx_of(req)
        sale = Sales(db).get(ctx, req.path_params["id"])
        if sale["status"] != "completed":
            raise ValidationFailed("الإيصال متاح للفواتير المكتملة فقط")
        cfg = rs.get(db, ctx.org_id)
        if req.query_params.get("w") in ("58", "80"):
            cfg["paper"] = int(req.query_params["w"])
        org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
        e = _html.escape
        m = lambda p: f"{(p or 0) / 100:,.2f}"  # noqa: E731

        def name(it):
            if it.get("hide_on_receipt"):       # the owner chose to keep this name off the paper
                return "—"
            if cfg["names"] == "ar":
                return e(it.get("name_ar") or it["name"])
            if cfg["names"] == "both" and it.get("name_ar") and it.get("name_en"):
                return f"{e(it['name_en'])}<br><small>{e(it['name_ar'])}</small>"
            return e(it.get("name_en") or it["name"])

        def qty(l):
            it, q = l["item"], l["qty_base"]
            unit = it["base_unit"] if it["base_unit"] not in (None, "unit") else "وحدة"
            pack = it.get("pack_unit") if it.get("pack_unit") not in (None, "pack") else "علبة"
            if it["pack_size"] > 1 and q % it["pack_size"] == 0:
                return f"{q // it['pack_size']} {e(pack)}"
            return f"{q} {e(unit)}"
        rows = []
        restricted = False
        for l in sale["lines"]:
            restricted |= bool(l["item"].get("no_return"))
            exp = ""
            if cfg["show_expiry"]:
                exps = sorted({(a.get("expiry_date") or "")[:7] for a in l["allocations"] if a.get("expiry_date")})
                exp = f" · صلاحية {' / '.join(exps)}" if exps else ""
            disc = f" · خصم {l['discount_bp'] / 100:g}%" if l["discount_bp"] else \
                f" · خصم {m(l['discount_amount'])} ج" if l.get("discount_amount") else ""
            star = " ✱" if l["item"].get("no_return") else ""
            rows.append(f"<tr><td>{name(l['item'])}{star}<br><small>{qty(l)}{exp}{disc}</small></td>"
                        f"<td>{m(l['line_total'])}</td></tr>")
        MET = {"cash": "نقدي", "card": "بطاقة", "transfer": "تحويل", "credit": "آجل"}
        pays = sale.get("payments") or []
        pay = "".join(f"<tr><td>{MET[p['method']]}{' — ' + e(p['channel']) if p.get('channel') else ''}</td><td>{m(p['amount'])}</td></tr>"
                      for p in pays)
        if not pays:
            pay = "".join(f"<tr><td>{lbl}</td><td>{m(sale[k])}</td></tr>" for k, lbl in
                          (("paid_cash", "نقدي"), ("paid_card", "بطاقة"), ("on_credit", "آجل")) if sale.get(k))
        cashier = db.one("SELECT full_name FROM users WHERE id=?", (sale["created_by"],))
        # F27: change due — only on the receipt opened right after checkout (?given=, never stored)
        try:
            given = int(req.query_params.get("given") or 0)
        except ValueError:
            given = 0
        cash = sale.get("paid_cash") or 0
        if cfg["show_change"] and cash and cash < given <= 100_000_000:
            pay += (f"<tr><td>المدفوع من العميل</td><td>{m(given)}</td></tr>"
                    f"<tr class='chg'><td>الباقي</td><td>{m(given - cash)}</td></tr>")
        count = (f"<tr><td>عدد الأصناف</td><td>{len(sale['lines'])}</td></tr>"
                 if cfg["show_item_count"] and sale["lines"] else "")
        hd = []
        if cfg["has_logo"]:
            from ..printing import logo as logo_mod
            png = logo_mod.get_png(db, ctx.org_id)
            if png:
                hd.append(f'<img alt="" src="data:image/png;base64,{base64.b64encode(png).decode()}">')
        from ..printing import qr as qr_mod, qrlinks

        def qr_fig(text, own):
            if not text:
                return ""
            try:
                code = qr_mod.svg(qr_mod.encode(text), 22 if cfg["paper"] == 80 else 17)
            except qr_mod.QRTooLong:
                return ""
            cap = qrlinks.caption(text, own)
            return f"<figure class='qr'>{code}{f'<figcaption>{e(cap)}</figcaption>' if cap else ''}</figure>"
        if cfg["qr_text"]:
            hd.append(qr_fig(cfg["qr_text"], cfg["qr_caption"]))
        hd = [x for x in hd if x]
        header = f'<div class="hd{" one" if len(hd) == 1 else ""}">{"".join(hd)}</div>' if hd else ""
        bottom_qr = f"<div class='hd one'>{qr_fig(cfg['qr2_text'], cfg['qr2_caption'])}</div>" if cfg["qr2_text"] else ""
        d = sale.get("delivery") or {}
        head = "".join(f"<p>{e(x)}</p>" for x in cfg["lines"] if x)
        head += "".join(f"<p>{lbl}: {e(cfg[k])}</p>" for k, lbl in
                        (("tax_no", "رقم التسجيل الضريبي"), ("cr_no", "السجل التجاري")) if cfg[k])
        when = rs.fmt_when(accounts.parse_iso(sale['completed_at']).astimezone(), cfg["time_format"])
        info = [f"فاتورة رقم {sale['sale_no']}", when]
        if cfg["show_cashier"] and cashier:
            info.append(f"البائع: {e(cashier['full_name'])}")
        if cfg["show_customer"] and sale.get("customer"):
            info.append(f"العميل: {e(sale['customer']['name'])}")
        if sale.get("kind") == "delivery":
            info.append("توصيل: " + e(" ".join(x for x in (d.get("address"), d.get("phone")) if x)))
        policy = ""
        if cfg["show_return_policy"]:
            n = return_window_days(db, ctx.org_id)       # Arabic number agreement: 7 أيام, 14 يومًا
            days = {1: "يوم واحد", 2: "يومين"}.get(n) or (f"{n} أيام" if 3 <= n <= 10 else f"{n} يومًا")
            policy = f"<p class='s'>المرتجع خلال {days} بهذه الفاتورة"
            policy += " · الأصناف التي عليها ✱ لا تُقبل إعادتها ولا استبدالها</p>" if restricted else "</p>"
        page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>إيصال {sale['sale_no']}</title>
<link rel="stylesheet" href="{_static_v('receipt.css')}">{f'<link rel="stylesheet" href="{_static_v("receipt-58.css")}">' if cfg['paper'] == 58 else ''}</head><body class="w{cfg['paper']}">
<div class="r">{f"<p>{e(cfg['header'])}</p>" if cfg['header'] else ""}{header}<h1>{e(cfg['title'] or org)}</h1>{head}<p>{'<br>'.join(info)}</p><hr>
<table>{''.join(rows)}</table><hr><table>{count}
<tr><td>الإجمالي</td><td>{m(sale['subtotal'])}</td></tr>
{f"<tr><td>خصم</td><td>{m(sale['discount_total'])}</td></tr>" if sale['discount_total'] else ""}
{f"<tr><td>رسوم التوصيل</td><td>{m(sale['delivery_fee'])}</td></tr>" if sale.get('delivery_fee') else ""}
{f"<tr><td>التقريب</td><td>{'+' if sale['rounding'] > 0 else '-'}{m(abs(sale['rounding']))}</td></tr>" if sale.get('rounding') else ""}
<tr class="t"><td>المطلوب</td><td>{m(sale['total'])}</td></tr>{pay}</table>
<p class="f">{e(cfg['footer'])}</p>{policy}{bottom_qr}</div><script src="/static/print.js"></script></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    @handler
    async def labels_page(req):
        """Barcode labels for browser printing: ?items=<id>:<count>,... — the owner's fields and size (#84),
        one label per page on a label roll, or a grid on an A4 sheet."""
        from ..printing import barcode as bc, labels as lb, service as ps, settings as rs
        ctx = ctx_of(req)
        ctx.require("items.read")
        cfg = rs.get(db, ctx.org_id)
        w, h = ps.label_size(cfg, req.query_params.get("size"))
        wanted = []
        for part in (req.query_params.get("items") or "").split(",")[:200]:
            iid, _, n = part.partition(":")
            if iid:
                wanted.append((iid, max(1, min(int(n) if n.isdigit() else 1, 500))))
        e = _html.escape
        cells = []
        for iid, n in wanted:
            it = db.one("SELECT * FROM items WHERE id=? AND org_id=?", (iid, ctx.org_id))
            if not it or (it["hidden"] and not ctx.can("items.hidden")):
                continue
            data = ps.label_data(db, ctx, it, cfg)
            try:
                on, _dropped = lb.layout(cfg["label_fields"], data, w, h, cfg["label_dpi"])
            except lb.PrinterError as err:
                raise ValidationFailed(str(err)) from err
            phone = e(data["phone"]) if on["phone"] else ""
            pharmacy = f'<span dir="rtl">{e(data["pharmacy"])}</span>' if on["pharmacy"] else ""
            top = f'<div class="t"><span>{phone}</span>{pharmacy}</div>' if on["phone"] or on["pharmacy"] else ""
            mark = f'<div class="b">{bc.svg(data["value"])[0]}</div>' if on["barcode"] else ""   # text-only: no 4 mm box
            name = f'<div class="n">{e(data["name"][:40])}</div>' if on["name"] else ""
            left = data["code"] if on["code"] else data["value"] if on["digits"] else ""
            bottom = (f'<div class="c"><span>{e(left)}</span><span>{e(data["expiry"]) if on["expiry"] else ""}</span>'
                      f'<b>{e(data["price"]) if on["price"] else ""}</b></div>') if (left or on["expiry"] or on["price"]) else ""
            cells += [f'<div class="lb">{top}{mark}{name}{bottom}</div>'] * n
        if not cells:
            raise ValidationFailed("اختر الأصناف")
        with db.transaction():
            audit.record(db, ctx, "print.labels", "item", None, after={"labels": len(cells), "size": f"{w:g}x{h:g}"})
        a4 = cfg["label_lang"] == "a4"
        css = f"/labels/page.css?w={w:g}&h={h:g}" + (f"&a4={cfg['label_a4']}" if a4 else "")
        page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>ملصقات باركود</title>
<link rel="stylesheet" href="{_static_v('labels.css')}"><link rel="stylesheet" href="{e(css)}"></head><body class="{'a4' if a4 else 'roll'}">{''.join(cells)}
<script src="/static/print.js"></script></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    async def labels_css(req):
        """Page and label size for the labels page: any size the settings accept, roll or A4 grid (#84)."""
        from ..printing import labels as lb, settings as rs
        q = req.query_params
        try:
            w, h = lb.parse_size(f"{q.get('w', '')}x{q.get('h', '')}") if not q.get("a4") else (0, 0)
        except lb.PrinterError:
            return Response("", status_code=400)
        if q.get("a4"):
            try:
                cols, rows = rs.a4_grid(q["a4"])
            except ValidationFailed:
                return Response("", status_code=400)
            w, h = lb.a4_cell(cols, rows)
            css = (f"@page {{ size: 210mm 297mm; margin: 0; }}\nbody.a4 {{ display: grid; grid-template-columns: repeat({cols}, {w:g}mm);"
                   f" grid-auto-rows: {h:g}mm; gap: 0; padding: 0; }}\n.a4 .lb {{ page-break-after: auto; break-inside: avoid; }}\n")
        else:
            css = f"@page {{ size: {w:g}mm {h:g}mm; margin: 0; }}\n"
        # the margin the bar-width check assumes (labels.PAD_MM): the barcode is never stretched thinner than checked
        css += f".lb {{ width: {w:g}mm; height: {h:g}mm; padding: {lb.PAD_MM:g}mm; }}\n"
        if h < 20:
            css += ".lb .n, .lb .t, .lb .c { font-size: 6pt; } .lb .c b { font-size: 6.5pt; }\n"
        return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})

    @handler
    async def label_preview(req):
        from ..printing import service as ps
        ctx = ctx_of(req)
        b = await body(req)
        png, dropped, info = await run_in_threadpool(ps.label_preview, db, ctx, b)
        return Response(png, media_type="image/png", headers={"X-Label-Dropped": ",".join(dropped),
                                                              "X-Label-Size": info["size"], "X-Label-Source": info["source"]})

    @handler
    async def print_label_test(req):
        from ..printing import service as ps
        ctx = ctx_of(req)
        b = await body(req)
        return J(await run_in_threadpool(ps.print_label_test, db, ctx, b if isinstance(b, dict) else {}))

    @handler
    async def print_label_calibrate(req):
        from ..printing import service as ps
        ctx = ctx_of(req)
        b = await body(req)
        return J(await run_in_threadpool(ps.calibrate_labels, db, ctx, b if isinstance(b, dict) else {}))

    @handler
    async def print_labels_api(req):
        from ..printing import service as ps
        ctx = ctx_of(req)
        b = await body(req)
        wanted = []
        for x in (b.get("items") or [])[:200]:
            if isinstance(x, dict) and x.get("id"):
                wanted.append((str(x["id"]), x.get("count", 1)))
        return J(await run_in_threadpool(ps.print_labels, db, ctx, wanted, size=b.get("size")))

    @handler
    async def receipt_logo(req):
        """The stored receipt logo as it will print (black/white), for the settings page preview (F27)."""
        from ..printing import logo as logo_mod
        ctx = ctx_of(req)
        png = logo_mod.get_png(db, ctx.org_id)
        if not png:
            return Response(status_code=404)
        return Response(logo_mod.print_look(png), media_type="image/png", headers={"Cache-Control": "no-store"})

    @handler
    async def receipt_settings(req):
        from ..printing import settings as rs
        ctx = ctx_of(req)
        if req.method == "PUT":
            return J(rs.save(db, ctx, await body(req)))
        return J(rs.get(db, ctx.org_id))

    @handler
    async def health(req):
        from ..version import __version__
        last = db.one("SELECT max(version) v FROM schema_migrations")["v"]
        return J({"ok": True, "version": __version__, "schema": last, "boot_id": os.environ.get("PHARM_BOOT_ID"),
                  "web": _web_build()})

    async def index(req):
        return HTMLResponse(_index_html(), headers={"Cache-Control": "no-cache"})

    async def sw(req):
        return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    routes = [
        Route("/api/health", health),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/setup", setup_status),
        Route("/api/system/update", system_update_status),
        Route("/api/system/update", system_update_run, methods=["POST"]),
        Route("/api/system/network", system_network, methods=["GET", "PUT"]),
        Route("/api/system/storage", system_storage),
        Route("/api/setup", setup_create, methods=["POST"]),
        Route("/api/setup/restore", setup_restore, methods=["POST"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/stock/alert", stock_alert_api),
        Route("/api/reports/stock-check", stock_check_report, methods=["POST"]),
        Route("/api/reports/stock-check/ack", stock_check_ack, methods=["POST"]),
        Route("/api/auth/quick-users", quick_users),
        Route("/api/auth/pin", pin_login, methods=["POST"]),
        Route("/api/me/pin", my_pin, methods=["POST"]),
        Route("/api/me/prefs", my_prefs, methods=["PUT"]),
        Route("/api/approvers", approvers_list),
        Route("/api/approvals/check", approval_check, methods=["POST"]),
        Route("/api/users", users_list),
        Route("/api/users", users_create, methods=["POST"]),
        Route("/api/users/{id}", users_get),
        Route("/api/users/{id}/{action}", users_action, methods=["POST"]),
        Route("/api/roles/{code}/discount", role_discount, methods=["POST"]),
        Route("/api/notifications", notifications_list),
        Route("/api/notifications/read", notifications_read, methods=["POST"]),
        Route("/api/settings/sales", sales_settings, methods=["GET", "PUT"]),
        Route("/api/settings/ui", ui_settings_api, methods=["GET", "PUT"]),
        Route("/api/settings/rounding", rounding_api, methods=["GET", "PUT"]),
        Route("/api/sales/{id}/attachments", sale_attach, methods=["POST"]),
        Route("/api/attachments/{id}", attachment_get),
        Route("/api/attachments/{id}/review", attachment_review, methods=["POST"]),
        Route("/api/reports/transfers", transfers_report),
        Route("/api/expense-schedules", exp_sched, methods=["GET", "POST"]),
        Route("/api/expense-schedules/{id}", exp_sched, methods=["POST"]),
        Route("/api/expenses", expenses_list),
        Route("/api/expenses", expenses_add, methods=["POST"]),
        Route("/api/expenses/{id}/decide", expenses_decide, methods=["POST"]),
        Route("/api/reports/profit", profit_report),
        Route("/api/reports/sales", reports_sales),
        Route("/api/reports/stock", reports_stock),
        Route("/api/home", home_view),
        Route("/api/feedback", feedback_view, methods=["GET", "POST"]),
        Route("/api/attendance", attendance_me, methods=["GET", "POST"]),
        Route("/api/staff", staff_sheet, methods=["GET", "POST"]),
        Route("/api/backup", backup_status, methods=["GET", "PUT"]),
        Route("/api/backup/run", backup_run, methods=["POST"]),
        Route("/api/backup/health", backup_health_view),
        Route("/api/health/day", day_health_view),
        Route("/api/items", items_list),
        Route("/api/items", items_create, methods=["POST"]),
        Route("/api/items/{id}", item_get),
        Route("/api/items/{id}", item_update, methods=["PATCH"]),
        Route("/api/items/{id}/card", item_card_get),
        Route("/api/items/{id}/pack-fix", item_pack_preview),
        Route("/api/items/{id}/pack-fix", item_pack_correct, methods=["POST"]),
        Route("/api/reports/item-health", item_health_api),
        Route("/api/items/{id}/branch-stock", item_branch_stock),
        Route("/api/items/{id}/movements", item_moves),
        Route("/api/audit", audit_list),
        Route("/api/stock/reasons", stock_reasons),
        Route("/api/items/flags", item_flags, methods=["POST"]),
        Route("/api/items/{id}/alternatives", item_alternatives),
        Route("/api/ingredients", ingredients_list),
        Route("/api/therapeutic-groups", groups_api, methods=["GET", "POST"]),
        Route("/api/therapeutic-groups/{id}", groups_api, methods=["PUT", "DELETE"]),
        Route("/api/pos/quick", pos_quick),
        Route("/api/reports/item-warnings", warnings_rep),
        Route("/api/reports/owner/{kind}", owner_report),
        Route("/api/reports/invoices", invoices_report),
        Route("/api/items/{id}/adjust", item_adjust, methods=["POST"]),
        Route("/api/reports/losses", losses_report),
        Route("/api/supplier-returns", supplier_returns_api, methods=["GET", "POST"]),
        Route("/api/supplier-returns/{id}", supplier_returns_api),
        Route("/supplier-return/{id}", supplier_return_page),
        Route("/api/categories/suggest", sections_suggest),
        Route("/api/contracts", contracts_api, methods=["GET", "POST"]),
        Route("/api/contracts/{id}", contracts_api, methods=["POST"]),
        Route("/api/contracts/{id}/report", contract_report),
        Route("/api/customers/{id}/contract", customer_contract, methods=["POST"]),
        Route("/api/items/{id}/{action}", item_action, methods=["POST"]),
        Route("/api/categories", categories_list),
        Route("/api/categories", categories_save, methods=["POST"]),
        Route("/api/categories/assign", categories_assign, methods=["POST"]),
        Route("/api/categories/{id}", categories_save, methods=["PATCH"]),
        Route("/api/suppliers", suppliers_list),
        Route("/api/suppliers", suppliers_create, methods=["POST"]),
        Route("/api/suppliers/all", suppliers_all),
        Route("/api/suppliers/statements", supplier_statements),
        Route("/api/suppliers/{id}", supplier_account, methods=["GET", "PATCH"]),
        Route("/api/suppliers/{id}/pay", supplier_pay, methods=["POST"]),
        Route("/api/suppliers/{id}/statement", supplier_statement),
        Route("/api/export/xlsx", export_xlsx, methods=["POST"]),
        Route("/api/shortages", shortages_list),
        Route("/api/purchases", purchases_list),
        Route("/api/purchases", purchases_manual, methods=["POST"]),
        Route("/api/purchases/import", purchases_import, methods=["POST"]),
        Route("/api/purchases/{id}", purchase_get),
        Route("/api/purchases/{id}", purchase_header, methods=["PATCH"]),
        Route("/api/purchases/{id}/lines", line_add, methods=["POST"]),
        Route("/api/purchases/{id}/lines/{line_id}", line_update, methods=["PATCH"]),
        Route("/api/purchases/{id}/lines/{line_id}", line_delete, methods=["DELETE"]),
        Route("/api/purchases/{id}/lines/{line_id}/new-item", line_new_item, methods=["POST"]),
        Route("/api/purchases/{id}/lines/{line_id}/merge", line_merge, methods=["POST"]),
        Route("/api/purchases/{id}/lines/{line_id}/split", line_split, methods=["POST"]),
        Route("/api/purchases/{id}/acknowledge", purchase_ack, methods=["POST"]),
        Route("/api/purchases/{id}/approve", purchase_approve, methods=["POST"]),
        Route("/api/purchases/{id}/cancel", purchase_cancel, methods=["POST"]),
        Route("/api/files/{id}", file_get),
        Route("/api/imports", import_analyze, methods=["POST"]),
        Route("/api/imports/templates/{kind}.xlsx", import_template),
        Route("/api/imports/{id}", import_get),
        Route("/api/imports/{id}/decide", import_decide, methods=["POST"]),
        Route("/api/imports/{id}/apply", import_apply, methods=["POST"]),
        Route("/api/imports/{id}/discard", import_discard, methods=["POST"]),
        Route("/api/export/items.xlsx", export_items),
        Route("/api/safety/check", safety_check, methods=["POST"]),
        Route("/api/items/{id}/ingredients", safety_ingredients, methods=["PUT"]),
        Route("/api/safety/rules", safety_rules_list),
        Route("/api/safety/rules", safety_rule_create, methods=["POST"]),
        Route("/api/safety/rules/{id}/approve", safety_rule_approve, methods=["POST"]),
        Route("/api/safety/rules/{id}/disable", safety_rule_disable, methods=["POST"]),
        Route("/api/safety/sources", safety_source_add, methods=["POST"]),
        Route("/api/procurement/suggestions", proc_suggest),
        Route("/api/procurement/suggestions.xlsx", proc_suggest_xlsx),
        Route("/api/procurement/health", proc_health),
        Route("/api/procurement/smart", proc_smart),
        Route("/api/procurement/order.xlsx", proc_order_xlsx, methods=["POST"]),
        Route("/api/procurement/suppliers/{id}", proc_supplier_costs),
        Route("/api/procurement/offer", proc_offer, methods=["POST"]),
        Route("/api/procurement/consumption", proc_consumption, methods=["POST"]),
        Route("/api/procurement/deadstock", proc_deadstock, methods=["POST"]),
        Route("/api/sales/change-fill", sales_change_fill),
        Route("/api/sales", sales_create, methods=["POST"]),
        Route("/api/sales", sales_list),
        Route("/api/sales/summary", sales_summary),
        Route("/api/sales/drafts", sales_drafts),
        Route("/api/deliveries", deliveries_open),
        Route("/api/payment-channels", channels_list),
        Route("/api/payment-channels", channels_save, methods=["POST"]),
        Route("/api/payment-channels/{id}", channels_save, methods=["PATCH"]),
        Route("/api/sales/{id}", sales_details, methods=["PATCH"]),
        Route("/api/sales/{id}/discard", sales_discard, methods=["POST"]),
        Route("/api/sales/{id}/delivery", sales_delivery, methods=["POST"]),
        Route("/api/sales/{id}/lines/{line_id}", sales_update_line, methods=["PATCH"]),
        Route("/api/sales/{id}/invoice-discount", sales_invoice_discount, methods=["PUT"]),
        Route("/api/sales/{id}", sales_get),
        Route("/api/sales/{id}/lines", sales_add_line, methods=["POST"]),
        Route("/api/sales/{id}/lines/{line_id}", sales_remove_line, methods=["DELETE"]),
        Route("/api/sales/{id}/check", sales_check, methods=["POST"]),
        Route("/api/sales/{id}/complete", sales_complete, methods=["POST"]),
        Route("/api/sales/{id}/return", sales_return, methods=["POST"]),
        Route("/api/sales/{id}/exchange", sales_exchange, methods=["POST"]),
        Route("/api/returns/find", returns_find),
        Route("/api/sales/{id}/void", sales_void, methods=["POST"]),
        Route("/api/estock/available", estock_available),
        Route("/api/estock/invoices", estock_invoices),
        Route("/api/estock/invoices/{kind}/{id}", estock_invoice),
        Route("/api/estock/customers/{id}/items", estock_customer_items),
        Route("/api/estock/items/{id}/history", estock_item_history),
        Route("/api/estock/statement/{kind}/{id}", estock_statement),
        Route("/api/estock/sales/{id}/return-lines", estock_return_lines),
        Route("/api/estock/sales/{id}/return", estock_return, methods=["POST"]),
        Route("/api/customers", customers_list),
        Route("/api/customers", customers_create, methods=["POST"]),
        Route("/api/customers/{id}/payments", customers_pay, methods=["POST"]),
        Route("/api/customers/all", customers_all),
        Route("/api/customers/phone-doubles", customers_phone_doubles),
        Route("/api/customers/{id}", customer_profile, methods=["GET", "PATCH"]),
        Route("/api/customers/{id}/refills", customer_refill, methods=["POST"]),
        Route("/receipt/{id}", receipt),
        Route("/return-receipt/{id}", return_receipt),
        Route("/labels", labels_page),
        Route("/labels/page.css", labels_css),
        Route("/api/settings/receipt", receipt_settings, methods=["GET", "PUT"]),
        Route("/api/settings/receipt/logo", receipt_logo),
        Route("/api/print/receipt/{id}", print_receipt, methods=["POST"]),
        Route("/api/print/drawer", print_drawer, methods=["POST"]),
        Route("/api/print/printers", print_printers),
        Route("/api/print/test", print_test, methods=["POST"]),
        Route("/api/print/labels", print_labels_api, methods=["POST"]),
        Route("/api/print/label-preview", label_preview, methods=["POST"]),
        Route("/api/print/label-test", print_label_test, methods=["POST"]),
        Route("/api/print/label-calibrate", print_label_calibrate, methods=["POST"]),
        Route("/api/cash/open", cash_open, methods=["POST"]),
        Route("/api/cash/current", cash_current),
        Route("/api/cash/movements", cash_move, methods=["POST"]),
        Route("/api/cash/close", cash_close, methods=["POST"]),
        Route("/api/cash/shifts", cash_shifts),
        Route("/api/system/slow-requests", slow_requests),
        Route("/api/reports/day-review", day_review_api),
        Route("/api/cash/shifts/{id}", cash_report),
        Route("/api/cash/shifts/{id}/hunt", cash_hunt),
        Route("/api/cash/float-target", cash_float_target, methods=["GET", "POST"]),
        Route("/api/cash/float-preview", cash_float_preview, methods=["POST"]),
        Route("/api/money/accounts", money_accounts_view, methods=["GET", "POST"]),
        Route("/api/money/accounts/{id}", money_account_one, methods=["GET", "PATCH"]),
        Route("/api/money/accounts/{id}/statement", money_statement_view),
        Route("/api/money/accounts/{id}/count", money_count_view, methods=["POST"]),
        Route("/api/money/accounts/{id}/adjust", money_adjust_view, methods=["POST"]),
        Route("/api/money/movements", money_movements_view),
        Route("/api/money/summary", money_summary_view),
        Route("/api/money/transfer", money_transfer_view, methods=["POST"]),
        Route("/api/money/owner", money_owner_view, methods=["POST"]),
        Route("/api/money/routes", money_routes_view, methods=["GET", "PUT"]),
        Route("/api/reconcile/parties", reconcile_parties_view),
        Route("/api/reconcile", reconcile_record_view, methods=["POST"]),
        Route("/api/reconcile/{party}/{id}", reconcile_history_view),
        Route("/api/opening-balances/{kind}", opening_corrections_view),
        Route("/api/opening-balances/{kind}/{id}", opening_correct_view, methods=["GET", "POST"]),
        Route("/shift-report/{id}", shift_report_page),
        Route("/api/counts", counts_list),
        Route("/api/counts", counts_create, methods=["POST"]),
        Route("/api/counts/{id}/entries", counts_entry, methods=["POST"]),
        Route("/api/counts/{id}/variances", counts_variances),
        Route("/api/counts/{id}/approve", counts_approve, methods=["POST"]),
        Route("/api/counts/{id}/cancel", counts_cancel, methods=["POST"]),
        Route("/api/recalls/extract", recall_extract, methods=["POST"]),
        Route("/api/recalls", recall_add, methods=["POST"]),
        Route("/api/recalls/alerts", recall_alerts),
        Route("/api/recalls/alerts/{id}", recall_decide, methods=["POST"]),
        Route("/sw.js", sw),
        Route("/", index),
        Mount("/static", StaticFiles(directory=WEB_DIR), name="static"),
    ]
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        from ..core.daily import DailyChecks
        daily = DailyChecks(db)
        if auto_backup:
            autob.start()
            daily.start()
            # #202: stock integrity check on every start, in the background so the screens open at once
            from ..inventory import integrity
            threading.Thread(target=integrity.check_all, args=(db, "startup"), daemon=True,
                             name="stock-integrity").start()
        try:
            yield
        finally:
            if auto_backup:
                daily.stop()
                await run_in_threadpool(autob.stop)

    from .hostcheck import HostAllowlist
    from ..core.smart_update import InFlight
    app = Starlette(routes=routes, middleware=[Middleware(HostAllowlist, extra_hosts=s.allowed_hosts),
                                               Middleware(SecurityHeaders), Middleware(InFlight)], lifespan=lifespan)
    app.state.db = db
    app.state.autobackup = autob
    return app
