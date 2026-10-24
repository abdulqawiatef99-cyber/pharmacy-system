"""HTTP API (Starlette) + static PWA hosting.

Auth: opaque session token, sent either as an HttpOnly SameSite=Strict cookie
(browser/PWA) or as `Authorization: Bearer <token>` (future mobile app /
integrations). Cookie-authenticated state-changing requests must carry the
`X-Requested-With: pharm` header (CSRF defence; cross-site forms cannot set it).
All business rules and permission checks live in the service layer; routes
only translate HTTP <-> service calls.
"""
from __future__ import annotations

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
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import config as config_mod
from ..catalog import service as catalog
from ..core import accounts, audit, notify, people
from ..core.errors import Conflict, DomainError, Forbidden, Unauthorized, ValidationFailed
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
from ..sales.cash import CashDrawer
from ..printing import service as printing
import html as _html

log = logging.getLogger("pharm.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
COOKIE = "pharm_sid"
MAX_JSON = 256 * 1024

# URL prefix -> entitlement. A tool switched off for a pharmacy answers 403
# without affecting the others.
FEATURE_PREFIXES = (("/api/sales", "sales"), ("/api/customers", "sales"), ("/receipt", "sales"),
                    ("/api/cash", "sales"), ("/api/print", "sales"),
                    ("/api/purchases", "purchasing"), ("/api/imports", "dataimport"),
                    ("/api/counts", "counting"), ("/api/procurement", "procurement"),
                    ("/api/recalls", "recalls"), ("/api/safety", "safety"))
IDEMPOTENCY_TTL_DAYS = 7

CSP = ("default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self';"
       " object-src 'none'; frame-src 'self' blob:; base-uri 'none'; frame-ancestors 'none';"
       " form-action 'self'")


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if hasattr(o, "__dict__"):
        return o.__dict__
    raise TypeError(type(o))


class J(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=_json_default).encode("utf-8")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


class RateLimiter:
    def __init__(self, limit: int, window_s: int):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise DomainError("محاولات كثيرة، انتظر دقيقة", code="rate_limited")
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

    def client_ip(req: Request) -> str:
        return req.client.host if req.client else "?"

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
        approver = req.headers.get("x-approver", "").strip()
        if approver:
            approver_limiter.check(client_ip(req))
            ctx = accounts.with_approval(db, ctx, approver[:64], req.headers.get("x-approver-pin", "")[:6])
        for prefix, feature in FEATURE_PREFIXES:
            if req.url.path.startswith(prefix):
                accounts.require_feature(db, ctx, feature)
                break
        return ctx

    async def body(req: Request) -> dict:
        raw = await req.body()
        if len(raw) > MAX_JSON:
            raise ValidationFailed("الطلب كبير جدًا")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValidationFailed("صيغة الطلب غير صحيحة")
        if not isinstance(data, dict):
            raise ValidationFailed("صيغة الطلب غير صحيحة")
        return data

    async def _idempotent(req: Request, fn):
        """Replay the stored response for a repeated POST with the same
        Idempotency-Key (client retry after a network drop / double click)."""
        key = req.headers.get("idempotency-key", "").strip()
        if req.method != "POST" or not key or len(key) > 100 or req.url.path.startswith("/api/auth/"):
            return await fn(req)
        ctx = ctx_of(req)
        raw = await req.body()
        rhash = hashlib.sha256(req.method.encode() + req.url.path.encode() + raw).hexdigest()
        hit = db.one("SELECT * FROM idempotency_keys WHERE org_id=? AND user_id=? AND key=?",
                     (ctx.org_id, ctx.user_id, key))
        if hit:
            if hit["request_hash"] != rhash:
                raise ValidationFailed("نفس مفتاح الطلب استُخدم لطلب مختلف", code="idempotency_mismatch")
            resp = Response(hit["response"], status_code=hit["status"], media_type="application/json")
            resp.headers["Idempotent-Replay"] = "true"
            return resp
        resp = await fn(req)
        if 200 <= resp.status_code < 300 and resp.media_type == "application/json":
            with db.transaction():
                db.execute("DELETE FROM idempotency_keys WHERE created_at < datetime('now', ?)",
                           (f"-{IDEMPOTENCY_TTL_DAYS} days",))
                db.execute("INSERT OR IGNORE INTO idempotency_keys (org_id, user_id, key, method, path,"
                           " request_hash, status, response) VALUES (?,?,?,?,?,?,?,?)",
                           (ctx.org_id, ctx.user_id, key, req.method, req.url.path, rhash,
                            resp.status_code, resp.body.decode("utf-8")))
        return resp

    def handler(fn):
        async def wrapped(req: Request):
            try:
                return await _idempotent(req, fn)
            except DomainError as e:
                status = 429 if e.code == "rate_limited" else e.status
                return J({"error": {"code": e.code, "message": e.message, "details": e.details}},
                         status_code=status)
            except Exception:   # never leak internals
                log.exception("unhandled error on %s %s", req.method, req.url.path)
                return J({"error": {"code": "internal", "message": "حصل خطأ غير متوقع"}},
                         status_code=500)
        return wrapped

    # ------------------------------------------------------------- auth
    @handler
    async def login(req):
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

    @handler
    async def setup_status(req):
        return J({"needed": _setup_needed()})

    @handler
    async def setup_create(req):
        """First run only: creates the pharmacy and its owner, then logs in. Refused once any
        pharmacy exists, and (unless PHARM_ALLOW_REMOTE_SETUP=1) from any machine but this PC."""
        import os as _os
        if client_ip(req) not in ("127.0.0.1", "::1", "localhost", "testclient") and \
                _os.environ.get("PHARM_ALLOW_REMOTE_SETUP") != "1":
            raise Forbidden("الإعداد الأول بيتعمل من جهاز الصيدلية نفسه بس")
        login_limiter.check(client_ip(req))
        if not _setup_needed():
            raise Conflict("البرنامج متجهز بالفعل؛ سجّل دخول", code="already_setup")
        b = await body(req)
        name, user, pw = (str(b.get(k, "")).strip() for k in ("pharmacy", "username", "password"))
        if len(name) < 2 or len(user) < 3:
            raise ValidationFailed("اكتب اسم الصيدلية واسم مستخدم (3 حروف على الأقل)")
        with setup_lock:
            if not _setup_needed():
                raise Conflict("البرنامج متجهز بالفعل؛ سجّل دخول", code="already_setup")
            accounts.create_organization(db, name=name, owner_username=user,
                                         owner_full_name=str(b.get("full_name") or user).strip(),
                                         owner_password=pw)
            item_card.ensure_default_categories(db)
            pay_channels.ensure_defaults(db)
            fin.ensure_defaults(db)
        res = accounts.login(db, user, pw, ip=client_ip(req), user_agent=req.headers.get("user-agent"))
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
        cur = updater.installed(s.data_dir).get("sha")
        out = {"version": __version__, "installed": cur, "latest": None, "available": False,
               "restarts_itself": os.environ.get("PHARM_LAUNCHER") == "1"}
        try:
            out["latest"] = updater.latest_sha(updater.DEFAULT_REPO, "main", updater.read_token())
            out["available"] = out["latest"] != cur
        except updater.UpdateError as e:
            out["error"] = str(e)
        upd_cache.update(at=now, v=out)
        return out

    @handler
    async def system_update_status(req):
        ctx_of(req).require("settings.manage")
        return J(await run_in_threadpool(_update_status))

    @handler
    async def system_update_run(req):
        ctx = ctx_of(req)
        ctx.require("settings.manage")
        import subprocess
        import sys as _sys
        root = Path(__file__).resolve().parents[2]
        proc = await run_in_threadpool(lambda: subprocess.run(
            [_sys.executable, str(root / "manage.py"), "update"], cwd=os.getcwd(), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=900,
            env={**os.environ, "PYTHONUTF8": "1"}))
        ok = proc.returncode == 0
        with db.transaction():
            audit.record(db, ctx, "system.update", "system", None,
                         after={"ok": ok, "tail": (proc.stdout or "")[-500:]})
        upd_cache.clear()
        restart = ok and os.environ.get("PHARM_LAUNCHER") == "1"
        if restart:          # the launcher starts the new version when we exit with 75
            threading.Timer(1.5, lambda: os._exit(75)).start()
        return J({"ok": ok, "restarting": restart,
                  "output": ((proc.stdout or "") + (proc.stderr or ""))[-3000:]}, status_code=200 if ok else 500)

    @handler
    async def setup_restore(req):
        """First run only: bring an existing pharmacy over from a backup .zip (moving to a new PC
        or to the installed version). The file is verified now and applied by the launcher on
        restart, when no database connection is open."""
        from ..core import backup as backup_mod
        if client_ip(req) not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise Forbidden("الاسترجاع بيتعمل من جهاز الصيدلية نفسه بس")
        if not _setup_needed():
            raise Conflict("البرنامج فيه بيانات بالفعل؛ الاسترجاع من هنا لأول تشغيل بس", code="already_setup")
        if os.environ.get("PHARM_LAUNCHER") != "1":
            raise ValidationFailed("الاسترجاع من هنا بيشتغل في النسخة المتثبتة بس. استخدم أمر restore")
        # a backup of a real pharmacy is far above the normal upload limit: stream it to disk
        if "multipart/form-data" not in req.headers.get("content-type", ""):
            raise ValidationFailed("اختار ملف النسخة الاحتياطية")
        form = await req.form(max_files=1, max_fields=2)
        up = form.get("file")
        if up is None or not hasattr(up, "read"):
            raise ValidationFailed("اختار ملف النسخة الاحتياطية")
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
            info = {"problems": [f"الملف مش نسخة احتياطية ({type(e).__name__})"]}
        if info["problems"]:
            pending.unlink(missing_ok=True)
            raise ValidationFailed("ملف النسخة الاحتياطية مش سليم: " + "؛ ".join(info["problems"][:3]))
        threading.Timer(1.0, lambda: os._exit(75)).start()
        return J({"ok": True, "restarting": True})

    @handler
    async def logout(req):
        token = req.cookies.get(COOKIE) or req.headers.get("authorization", "")[7:]
        if token:
            accounts.logout(db, token)
        resp = J({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @handler
    async def me(req):
        ctx = ctx_of(req)
        return J({**accounts.me(db, ctx), "notifications_unread": notify.unread_count(db, ctx)})

    # ------------------------------------------------------------- people: PIN, approval, users
    def _local_only(req):
        if client_ip(req) not in ("127.0.0.1", "::1", "localhost", "testclient") and \
                os.environ.get("PHARM_ALLOW_REMOTE_QUICK") != "1":
            raise Forbidden("الدخول السريع من جهاز الصيدلية بس", code="local_only")

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
            raise Unauthorized("الرقم السري غلط", code="invalid_pin")
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
        if not _sec.verify_password(str(b.get("password", "")), u["password_hash"]):
            raise ValidationFailed("كلمة المرور غلط")
        people.set_pin(db, ctx, ctx.user_id, str(b.get("pin", "")))
        return J({"ok": True})

    @handler
    async def my_prefs(req):
        ctx = ctx_of(req)
        return J(people.set_prefs(db, ctx, await body(req)))

    @handler
    async def item_card_get(req):
        return J(item_card.card(db, ctx_of(req), req.path_params["id"]))

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
        return J(sup.pay(db, ctx, req.path_params["id"], _int(b.get("amount"), "amount"), str(b.get("method", "")),
                         b.get("note")))

    @handler
    async def shortages_list(req):
        from ..procurement import suppliers as sup
        return J({"items": sup.shortages(db, ctx_of(req))})

    @handler
    async def customers_all(req):
        from ..sales import customers as cu
        qp = req.query_params
        return J({"customers": cu.listing(db, ctx_of(req), qp.get("q", ""), qp.get("only") or None)})

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
        if b.get("remove"):
            return J(cu.remove_refill(db, ctx, cid, str(b.get("item_id", ""))))
        return J(cu.save_refill(db, ctx, cid, str(b.get("item_id", "")), b.get("qty_base"), b.get("every_days", 30)))

    @handler
    async def expenses_list(req):
        ctx = ctx_of(req)
        qp = req.query_params
        return J({"expenses": fin.listing(db, ctx, status=qp.get("status") or None, month=qp.get("month") or None),
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
        return J(fin.profit(db, ctx_of(req), qp.get("from", ""), qp.get("to", "")))

    @handler
    async def approvers_list(req):
        return J({"approvers": people.approvers(db, ctx_of(req))})

    @handler
    async def approval_check(req):
        """The approver typed his PIN: confirm it before the screen retries the action."""
        ctx = ctx_of(req)
        if not ctx.approver_id:
            raise ValidationFailed("اختار مين هيوافق")
        perm = str((await body(req)).get("permission", ""))
        ok = ctx.allowed(perm) if perm in accounts.PERMISSIONS else perm == "discount"
        return J({"ok": ok, "discount_limit_bp": ctx.discount_limit_bp})

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
                                limit=int(req.query_params.get("limit", 50) or 50)))

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
        from ..sales.service import return_window_days
        ctx = ctx_of(req)
        if req.method == "PUT":
            ctx.require("settings.manage")
            b = await body(req)
            try:
                days = int(b.get("return_window_days"))
            except (TypeError, ValueError):
                raise ValidationFailed("اكتب عدد الأيام")
            if not 0 <= days <= 365:
                raise ValidationFailed("مدة المرتجع من 0 لـ 365 يوم")
            with db.transaction():
                before = return_window_days(db, ctx.org_id)
                db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'return_window_days', ?)"
                           " ON CONFLICT(org_id, key) DO UPDATE SET value=excluded.value", (ctx.org_id, str(days)))
                audit.record(db, ctx, "settings.return_window", "organization", ctx.org_id,
                             before={"days": before}, after={"days": days})
        return J({"return_window_days": return_window_days(db, ctx.org_id)})

    # ------------------------------------------------------------- catalog
    @handler
    async def items_list(req):
        ctx = ctx_of(req)
        res = item_search(db, ctx, req.query_params.get("q", ""), min(int(req.query_params.get("limit", 20)), 200),
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
            for r in rows:
                r["stock_qty"] = qty.get(r["id"], 0)     # base units
        if req.query_params.get("meta") == "1":
            return J({"items": rows, "hint": res["hint"]})
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
        return J(purch.list(ctx_of(req), req.query_params.get("status")))

    @handler
    async def purchases_import(req):
        ctx = ctx_of(req)
        ctype = req.headers.get("content-type", "")
        if "multipart/form-data" not in ctype:
            raise ValidationFailed("ارفع الملف كـ multipart/form-data")
        if int(req.headers.get("content-length") or 0) > s.max_upload_bytes + 64 * 1024:
            raise ValidationFailed("حجم الملف أكبر من المسموح")
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
        mapping = json.loads(fields["mapping"]) if fields.get("mapping") else None
        options = {"qty_unit": fields.get("qty_unit") or "pack"}
        if fields.get("header_row"):
            options["header_row"] = int(fields["header_row"])
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
        ctx_of(req)
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
        sid = counting.create(ctx, title=str(b.get("title", "")), scope=b.get("scope", "full"),
                              locations=b.get("locations"), item_ids=b.get("item_ids"),
                              blind=bool(b.get("blind", True)))
        return J({"id": sid}, status_code=201)

    @handler
    async def counts_entry(req):
        ctx = ctx_of(req)
        b = await body(req)
        try:
            qty = int(b.get("qty"))
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
        return J(counting.approve(ctx, req.path_params["id"], zero_uncounted=bool(b.get("zero_uncounted")),
                                  reason=b.get("reason")))

    @handler
    async def counts_cancel(req):
        ctx = ctx_of(req)
        counting.cancel(ctx, req.path_params["id"])
        return J({"ok": True})

    # ------------------------------------------------------------- procurement
    def _sugg_params(req):
        qp = req.query_params
        try:
            return {"cover_days": int(qp.get("cover_days", 30)), "lead_days": int(qp.get("lead_days", 2)),
                    "safety_days": int(qp.get("safety_days", 5)), "window_days": int(qp.get("window_days", 90))}
        except ValueError:
            raise ValidationFailed("قيم غير صحيحة")

    @handler
    async def proc_suggest(req):
        ctx = ctx_of(req)
        return J(Procurement(db).suggest(ctx, **_sugg_params(req)))

    @handler
    async def proc_suggest_xlsx(req):
        ctx = ctx_of(req)
        rows = Procurement(db).suggest(ctx, **_sugg_params(req))
        return Response(suggestions_xlsx(rows, db, ctx), media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            headers={"Content-Disposition": "attachment; filename=order-suggestions.xlsx"})

    @handler
    async def proc_health(req):
        return J(Procurement(db).stock_health(ctx_of(req)))

    @handler
    async def proc_supplier_costs(req):
        return J(Procurement(db).supplier_costs(ctx_of(req), req.path_params["id"]))

    @handler
    async def proc_offer(req):
        ctx = ctx_of(req)
        b = await body(req)
        try:
            return J(Procurement(db).evaluate_offer(
                ctx, str(b.get("item_id", "")), packs=int(b.get("packs", 0)),
                discount_pct=float(b.get("discount_pct", -1)), expiry_date=b.get("expiry_date") or None,
                cash_available=float(b["cash_available"]) if b.get("cash_available") else None))
        except (TypeError, ValueError):
            raise ValidationFailed("قيم غير صحيحة")

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
        form = await req.form(max_files=3, max_fields=4)
        files = {}
        for k in ("stock", "sales", "purchases"):
            up = form.get(k)
            if up is None or not hasattr(up, "read"):
                raise ValidationFailed("لازم الـ 3 ملفات: الأرصدة، والمبيعات، والمشتريات")
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
            return int(v or 0)
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
            raise ValidationFailed("رقم الفاتورة لازم يكون رقم")
        return J(Sales(db).list_sales(ctx_of(req), day=qp.get("day") or None, sale_no=int(no) if no else None))

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
                                       qty_base=b.get("qty_base"), discount_bp=b.get("discount_bp")))

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
        return J(Sales(db).complete(ctx, req.path_params["id"], paid_cash=_int(b.get("paid_cash"), "cash"),
                                    paid_card=_int(b.get("paid_card"), "card"),
                                    on_credit=_int(b.get("on_credit"), "credit"),
                                    safety_decisions=decisions, version=b.get("version"),
                                    request_id=(str(b["request_id"])[:64] if b.get("request_id") else None),
                                    confirm_below_cost=bool(b.get("confirm_below_cost")),
                                    payments=b.get("payments") if isinstance(b.get("payments"), list) else None))

    @handler
    async def sales_return(req):
        ctx = ctx_of(req)
        b = await body(req)
        lines = {str(k): _int(v, "qty") for k, v in (b.get("lines") or {}).items()}
        return J(Sales(db).return_items(ctx, req.path_params["id"], lines,
                                        refund_method=str(b.get("refund_method", "")),
                                        reason=str(b.get("reason", ""))))

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
        q = "%" + req.query_params.get("q", "") + "%"
        rows = db.all("SELECT id, name, phone, credit_limit FROM customers WHERE org_id=? AND is_active=1"
                      " AND (name LIKE ? OR coalesce(phone,'') LIKE ?) ORDER BY name LIMIT 30",
                      (ctx.org_id, q, q))
        sales = Sales(db)
        return J([{**dict(r), "balance": sales.balance(ctx, r["id"])} for r in rows])

    @handler
    async def customers_create(req):
        ctx = ctx_of(req)
        b = await body(req)
        cid = Sales(db).create_customer(ctx, name=str(b.get("name", "")), phone=b.get("phone") or None,
                                        credit_limit=_int(b.get("credit_limit"), "limit"), notes=b.get("notes"))
        return J({"id": cid}, status_code=201)

    @handler
    async def customers_pay(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J({"balance": Sales(db).customer_payment(ctx, req.path_params["id"],
                                                         _int(b.get("amount"), "amount"), b.get("note"),
                                                         method=str(b.get("method", "cash")))})

    @handler
    async def cash_open(req):
        ctx = ctx_of(req)
        b = await body(req)
        sid = CashDrawer(db).open(ctx, _int(b.get("opening_cash"), "opening_cash"))
        return J(CashDrawer(db).summary(ctx, sid), status_code=201)

    @handler
    async def cash_current(req):
        return J(CashDrawer(db).summary(ctx_of(req)))

    @handler
    async def cash_move(req):
        ctx = ctx_of(req)
        b = await body(req)
        CashDrawer(db).movement(ctx, _int(b.get("amount"), "amount"), str(b.get("kind", "")), str(b.get("note", "")))
        return J(CashDrawer(db).summary(ctx))

    @handler
    async def cash_close(req):
        ctx = ctx_of(req)
        b = await body(req)
        if isinstance(b.get("denominations"), dict):
            return J(CashDrawer(db).close(ctx, note=b.get("note"), denominations=b["denominations"]))
        return J(CashDrawer(db).close(ctx, _int(b.get("counted_cash"), "counted_cash"), b.get("note")))

    @handler
    async def cash_shifts(req):
        return J({"shifts": CashDrawer(db).recent(ctx_of(req))})

    @handler
    async def cash_report(req):
        return J(CashDrawer(db).report(ctx_of(req), req.path_params["id"]))

    @handler
    async def shift_report_page(req):
        """Closing report sized for the 80mm receipt printer (browser print)."""
        from ..sales.cash import DENOMINATIONS
        ctx = ctx_of(req)
        r = CashDrawer(db).report(ctx, req.path_params["id"])
        org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
        e = _html.escape
        m = lambda p: f"{(p or 0) / 100:,.2f}"  # noqa: E731
        t = lambda iso: accounts.parse_iso(iso).astimezone().strftime("%Y-%m-%d %H:%M") if iso else "—"  # noqa: E731
        k = r["cash_by_kind"]
        rows = [("رصيد البداية", r["opening_cash"]), ("مبيعات نقدي", k.get("sale", 0)),
                ("تحصيل آجل", k.get("customer_payment", 0)), ("مرتجع نقدي", k.get("refund", 0)),
                ("إلغاء فواتير", k.get("void", 0)), ("مصروفات من الدرج", k.get("payout", 0)), ("إيداع", k.get("deposit", 0))]
        cash_rows = "".join(f"<tr><td>{a}</td><td>{m(v)}</td></tr>" for a, v in rows if v or a == "رصيد البداية")
        ch_rows = "".join(f"<tr><td>{'فيزا' if c['method'] == 'card' else 'تحويل'} — {e(c['channel'])}</td><td>{m(c['amount'])}</td></tr>"
                          for c in r["by_channel"])
        den = r["counted_detail"] or {}
        den_rows = "".join(f"<tr><td>{d / 100:g} × {den[str(d)]}</td><td>{m(d * den[str(d)])}</td></tr>"
                           for d in DENOMINATIONS if den.get(str(d)))
        diff = r["difference"]
        res = ("" if r["status"] != "closed" else
               f"<tr class='t'><td>{'✓ مظبوط' if not diff else ('✕ عجز' if diff < 0 else '⚠ زيادة')}</td><td>{m(abs(diff or 0))}</td></tr>")
        page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>تقفيل الشيفت</title>
<link rel="stylesheet" href="/static/receipt.css"></head><body>
<div class="r"><h1>{e(org)}</h1><p>تقرير تقفيل الشيفت<br>من {t(r['opened_at'])} ({e(r['opened_by_name'] or '')})<br>
لحد {t(r['closed_at'])} ({e(r['closed_by_name'] or '')})</p>
<table><tr><td>عدد الفواتير</td><td>{r['sales_count']}</td></tr><tr><td>إجمالي المبيعات</td><td>{m(r['sales_total'])}</td></tr>
{f"<tr><td>الخصومات</td><td>{m(r['discount_total'])}</td></tr>" if r['discount_total'] else ""}
{f"<tr><td>مرتجعات ({r['returns_count']})</td><td>{m(r['returns_total'])}</td></tr>" if r['returns_count'] else ""}
{f"<tr><td>فواتير ملغاة</td><td>{r['voided_count']}</td></tr>" if r['voided_count'] else ""}</table><hr>
<table>{cash_rows}<tr class="t"><td>المفروض في الدرج</td><td>{m(r['expected_cash'])}</td></tr></table>
{f"<hr><table>{den_rows}</table>" if den_rows else ""}
<table><tr class="t"><td>المعدود</td><td>{m(r['counted_cash'])}</td></tr>{res}</table>
{f"<hr><table>{ch_rows}</table>" if ch_rows else ""}
{f"<table><tr><td>آجل</td><td>{m(r['credit_total'])}</td></tr></table>" if r['credit_total'] else ""}
{f"<p>ملاحظة: {e(r['close_note'])}</p>" if r.get('close_note') else ""}
<p class="f">توقيع المستلم: ..................</p></div><script src="/static/print.js"></script></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    @handler
    async def print_receipt(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(await run_in_threadpool(printing.print_receipt, db, ctx, req.path_params["id"],
                                         open_drawer=bool(b.get("open_drawer"))))

    @handler
    async def print_drawer(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(await run_in_threadpool(printing.open_drawer, db, ctx, str(b.get("reason", ""))))

    @handler
    async def receipt(req):
        """Printable receipt sized for 80mm thermal printers (browser print).
        Raw ESC/POS printing + cash drawer need a local print agent (not built)."""
        ctx = ctx_of(req)
        sale = Sales(db).get(ctx, req.path_params["id"])
        if sale["status"] != "completed":
            raise ValidationFailed("الإيصال متاح للفواتير المكتملة فقط")
        org = db.one("SELECT name FROM organizations WHERE id=?", (ctx.org_id,))["name"]
        e = _html.escape
        m = lambda p: f"{p / 100:.2f}"  # noqa: E731
        rows = "".join(
            f"<tr><td>{e(l['item']['name'])}<br><small>{l['qty_base']} {e(l['item']['base_unit'])}</small></td>"
            f"<td>{m(l['line_total'])}</td></tr>" for l in sale["lines"])
        pay = []
        if sale["paid_cash"]:
            pay.append(f"<tr><td>نقدي</td><td>{m(sale['paid_cash'])}</td></tr>")
        if sale["paid_card"]:
            pay.append(f"<tr><td>فيزا</td><td>{m(sale['paid_card'])}</td></tr>")
        if sale.get("paid_transfer"):
            pay.append(f"<tr><td>تحويل</td><td>{m(sale['paid_transfer'])}</td></tr>")
        if sale.get("delivery_fee"):
            pay.append(f"<tr><td>رسوم التوصيل</td><td>{m(sale['delivery_fee'])}</td></tr>")
        if sale["on_credit"]:
            pay.append(f"<tr><td>آجل</td><td>{m(sale['on_credit'])}</td></tr>")
        page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>إيصال {sale['sale_no']}</title>
<link rel="stylesheet" href="/static/receipt.css"></head><body>
<div class="r"><h1>{e(org)}</h1><p>فاتورة رقم {sale['sale_no']}<br>{e(accounts.parse_iso(sale['completed_at']).astimezone().strftime('%Y-%m-%d %H:%M'))}</p>
<table>{rows}</table><hr><table>
<tr><td>الإجمالي</td><td>{m(sale['subtotal'])}</td></tr>
{f"<tr><td>خصم</td><td>{m(sale['discount_total'])}</td></tr>" if sale['discount_total'] else ""}
<tr class="t"><td>المطلوب</td><td>{m(sale['total'])}</td></tr>{''.join(pay)}</table>
<p class="f">شكرًا لزيارتكم</p></div><script src="/static/print.js"></script></body></html>"""
        return Response(page, media_type="text/html; charset=utf-8")

    @handler
    async def health(req):
        from ..version import __version__
        last = db.one("SELECT max(version) v FROM schema_migrations")["v"]
        return J({"ok": True, "version": __version__, "schema": last})

    async def index(req):
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def sw(req):
        return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    routes = [
        Route("/api/health", health),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/setup", setup_status),
        Route("/api/system/update", system_update_status),
        Route("/api/system/update", system_update_run, methods=["POST"]),
        Route("/api/setup", setup_create, methods=["POST"]),
        Route("/api/setup/restore", setup_restore, methods=["POST"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/me", me),
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
        Route("/api/expenses", expenses_list),
        Route("/api/expenses", expenses_add, methods=["POST"]),
        Route("/api/expenses/{id}/decide", expenses_decide, methods=["POST"]),
        Route("/api/reports/profit", profit_report),
        Route("/api/backup", backup_status, methods=["GET", "PUT"]),
        Route("/api/backup/run", backup_run, methods=["POST"]),
        Route("/api/items", items_list),
        Route("/api/items", items_create, methods=["POST"]),
        Route("/api/items/{id}", item_get),
        Route("/api/items/{id}", item_update, methods=["PATCH"]),
        Route("/api/items/{id}/card", item_card_get),
        Route("/api/items/{id}/{action}", item_action, methods=["POST"]),
        Route("/api/categories", categories_list),
        Route("/api/categories", categories_save, methods=["POST"]),
        Route("/api/categories/assign", categories_assign, methods=["POST"]),
        Route("/api/categories/{id}", categories_save, methods=["PATCH"]),
        Route("/api/suppliers", suppliers_list),
        Route("/api/suppliers", suppliers_create, methods=["POST"]),
        Route("/api/suppliers/all", suppliers_all),
        Route("/api/suppliers/{id}", supplier_account, methods=["GET", "PATCH"]),
        Route("/api/suppliers/{id}/pay", supplier_pay, methods=["POST"]),
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
        Route("/api/procurement/suppliers/{id}", proc_supplier_costs),
        Route("/api/procurement/offer", proc_offer, methods=["POST"]),
        Route("/api/procurement/consumption", proc_consumption, methods=["POST"]),
        Route("/api/procurement/deadstock", proc_deadstock, methods=["POST"]),
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
        Route("/api/sales/{id}", sales_get),
        Route("/api/sales/{id}/lines", sales_add_line, methods=["POST"]),
        Route("/api/sales/{id}/lines/{line_id}", sales_remove_line, methods=["DELETE"]),
        Route("/api/sales/{id}/check", sales_check, methods=["POST"]),
        Route("/api/sales/{id}/complete", sales_complete, methods=["POST"]),
        Route("/api/sales/{id}/return", sales_return, methods=["POST"]),
        Route("/api/sales/{id}/void", sales_void, methods=["POST"]),
        Route("/api/customers", customers_list),
        Route("/api/customers", customers_create, methods=["POST"]),
        Route("/api/customers/{id}/payments", customers_pay, methods=["POST"]),
        Route("/api/customers/all", customers_all),
        Route("/api/customers/{id}", customer_profile, methods=["GET", "PATCH"]),
        Route("/api/customers/{id}/refills", customer_refill, methods=["POST"]),
        Route("/receipt/{id}", receipt),
        Route("/api/print/receipt/{id}", print_receipt, methods=["POST"]),
        Route("/api/print/drawer", print_drawer, methods=["POST"]),
        Route("/api/cash/open", cash_open, methods=["POST"]),
        Route("/api/cash/current", cash_current),
        Route("/api/cash/movements", cash_move, methods=["POST"]),
        Route("/api/cash/close", cash_close, methods=["POST"]),
        Route("/api/cash/shifts", cash_shifts),
        Route("/api/cash/shifts/{id}", cash_report),
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
        if auto_backup:
            autob.start()
        try:
            yield
        finally:
            if auto_backup:
                await run_in_threadpool(autob.stop)

    app = Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)], lifespan=lifespan)
    app.state.db = db
    app.state.autobackup = autob
    return app
