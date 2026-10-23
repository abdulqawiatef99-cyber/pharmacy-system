"""HTTP API (Starlette) + static PWA hosting.

Auth: opaque session token, sent either as an HttpOnly SameSite=Strict cookie
(browser/PWA) or as `Authorization: Bearer <token>` (future mobile app /
integrations). Cookie-authenticated state-changing requests must carry the
`X-Requested-With: pharm` header (CSRF defence; cross-site forms cannot set it).
All business rules and permission checks live in the service layer; routes
only translate HTTP <-> service calls.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from decimal import Decimal
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import config as config_mod
from ..catalog import service as catalog
from ..core import accounts
from ..core.errors import DomainError, Unauthorized, ValidationFailed
from ..db import Database
from ..inventory import service as inventory
from ..purchasing.service import Purchasing

log = logging.getLogger("pharm.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
COOKIE = "pharm_sid"
MAX_JSON = 256 * 1024

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
               purchasing: Purchasing | None = None) -> Starlette:
    s = settings or config_mod.load()
    if db is None:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(s.db_path)
        db.migrate()
    purch = purchasing or Purchasing(db, s)
    login_limiter = RateLimiter(10, 60)

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
        return accounts.resolve_session(db, token, ip=client_ip(req))

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

    def handler(fn):
        async def wrapped(req: Request):
            try:
                return await fn(req)
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
        return J(accounts.me(db, ctx_of(req)))

    # ------------------------------------------------------------- catalog
    @handler
    async def items_list(req):
        ctx = ctx_of(req)
        return J(catalog.search_items(db, ctx, req.query_params.get("q", ""),
                                      min(int(req.query_params.get("limit", 20)), 100)))

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
        inv_id = purch.import_file(ctx, data, up.filename or "upload",
                                   form.get("supplier_id") or None)
        return J(purch.get(ctx, inv_id), status_code=201)

    @handler
    async def purchases_manual(req):
        ctx = ctx_of(req)
        b = await body(req)
        return J(purch.get(ctx, purch.create_manual(ctx, supplier_id=b.get("supplier_id"))),
                 status_code=201)

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

    @handler
    async def health(req):
        db.one("SELECT 1")
        return J({"ok": True})

    async def index(req):
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def sw(req):
        return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    routes = [
        Route("/api/health", health),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/items", items_list),
        Route("/api/items", items_create, methods=["POST"]),
        Route("/api/items/{id}", item_get),
        Route("/api/items/{id}", item_update, methods=["PATCH"]),
        Route("/api/suppliers", suppliers_list),
        Route("/api/suppliers", suppliers_create, methods=["POST"]),
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
        Route("/sw.js", sw),
        Route("/", index),
        Mount("/static", StaticFiles(directory=WEB_DIR), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)])
    app.state.db = db
    return app
