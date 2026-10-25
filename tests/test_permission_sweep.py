"""Security review: a signed-in person with NO permissions calls every route of the app (with real ids).
Only a short list of personal routes may answer 2xx; everything else must refuse. The route list is read
from the app, so a new route without a permission check fails this test."""
import re

from starlette.routing import Route
from starlette.testclient import TestClient

from app.core import people
from app.core.permissions import PERMISSIONS
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.test_api import APICase, H

PW = "Nobody-Pass-2026"
# routes a person may use with no permission at all: his own account, screen settings he reads, health
PERSONAL = {"/api/me", "/api/me/prefs", "/api/me/pin", "/api/me/password", "/api/auth/logout", "/api/health",
            "/api/setup", "/api/auth/quick-users", "/api/settings/ui", "/api/attendance", "/api/feedback",
            "/api/approvals/check", "/api/system/update-badge", "/api/version",
            # harmless to anyone signed in: approver names for the approval box, fixed reason lists, screen
            # settings, and the home page (which shows only the person's own figures without permissions)
            "/api/approvers", "/api/stock/reasons", "/api/settings/sales", "/api/settings/receipt", "/api/home"}


class TestPermissionSweep(APICase):
    def test_no_permission_no_access(self):
        ctx = self.ctx
        CashDrawer(self.db).open(ctx, 0)
        s = Sales(self.db)
        sale = s.create(ctx)
        from decimal import Decimal
        from app.inventory import service as inv
        with self.db.transaction():
            inv.post_movement(self.db, ctx, item_id=self.pan, qty_delta=10, movement_type="opening", unit_cost=Decimal("15"))
        s.add_line(ctx, sale, self.pan, units=1)
        cust = s.create_customer(ctx, name="عميل", credit_limit=1000)
        uid = people.add_user(self.db, ctx, username="nobody1", full_name="بدون صلاحيات", password=PW, role_code="assistant")
        for p in PERMISSIONS:
            people.set_exception(self.db, ctx, uid, p, "deny")
        c = TestClient(self.app)
        self.http_login("nobody1", PW, client=c)
        before = {t: self.db.one(f"SELECT count(*) FROM {t}")[0] for t in
                  ("items", "stock_movements", "sales", "sale_lines", "customers", "customer_ledger", "cash_movements",
                   "expenses", "suppliers", "audit_log")}
        ids = [self.pan, sale, cust, "x"]
        opened = []
        for r in self.app.routes:
            if not isinstance(r, Route) or not r.path.startswith("/api/") or r.path in PERSONAL:
                continue
            params = re.findall(r"{(\w+)}", r.path)
            for method in sorted(r.methods - {"HEAD"}):
                for value in (ids if params else [None]):
                    path = r.path
                    for p in params:
                        path = path.replace("{" + p + "}", value or "x")
                    resp = c.request(method, path, headers=H, json={"name": "x", "amount": 100, "reason": "اختبار صلاحيات",
                                                                     "items": [{"id": self.pan, "count": 1}]})
                    if resp.status_code < 300 and not path.endswith(".xlsx"):
                        opened.append((method, path, resp.status_code))
        self.assertEqual(opened, [])
        after = {t: self.db.one(f"SELECT count(*) FROM {t}")[0] for t in before}
        before.pop("audit_log"), after.pop("audit_log")          # a refused attempt may be logged
        self.assertEqual(before, after)
