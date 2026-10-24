from dataclasses import replace
from datetime import date, timedelta

from starlette.testclient import TestClient

from app import config
from app.api.app import COOKIE, create_app
from app.catalog import service as cat
from app.purchasing.service import Purchasing
from tests.fixtures.make_invoices import excel_invoice
from tests.helpers import DBTestCase, OWNER_PW

FUT = (date.today() + timedelta(days=700)).strftime("%m/%Y")
H = {"X-Requested-With": "pharm"}


class APICase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.s = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None)
        self.app = create_app(self.db, self.s, Purchasing(self.db, self.s))
        self.c = TestClient(self.app)
        cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما")
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab",
                                                       "pack_size": 2, "public_price": 4000})

    def http_login(self, username="owner", password=OWNER_PW, client=None):
        c = client or self.c
        r = c.post("/api/auth/login", json={"username": username, "password": password}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r


class TestAPI(APICase):
    def test_requires_login(self):
        self.assertEqual(self.c.get("/api/purchases").status_code, 401)
        h = self.c.get("/api/health").json()
        self.assertTrue(h["ok"] and h["version"] and h["schema"].startswith("00"))

    def test_login_sets_httponly_cookie_and_headers(self):
        r = self.http_login()
        cookie = r.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie.replace("Strict", "strict"))
        self.assertNotIn("token", r.json())                     # not exposed to JS
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_csrf_header_required_for_cookie_writes(self):
        self.http_login()
        r = self.c.post("/api/suppliers", json={"name": "X"})        # no X-Requested-With
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "csrf")
        r = self.c.post("/api/suppliers", json={"name": "Pharma Overseas"}, headers=H)
        self.assertEqual(r.status_code, 201)

    def test_bearer_token_for_mobile_clients(self):
        r = self.c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW,
                                                 "token_in_body": True}, headers=H)
        tok = r.json()["token"]
        fresh = TestClient(self.app)                                 # no cookies
        r = fresh.post("/api/suppliers", json={"name": "Abda"},
                       headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 201)

    def test_bad_login(self):
        r = self.c.post("/api/auth/login", json={"username": "owner", "password": "nope-nope-1"},
                        headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "invalid_credentials")

    def test_invoice_round_trip(self):
        self.http_login()
        data = excel_invoice([("PANADOL 500 MG 24 TAB", 10, 0, 40, 20, 32, 320, "P1", FUT, None)])
        r = self.c.post("/api/purchases/import", headers=H,
                        files={"file": ("inv.xlsx", data, "application/octet-stream")})
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()
        inv_id, ver = d["invoice"]["id"], d["invoice"]["version"]
        self.assertTrue(d["can_approve"], d["issues"])
        # source file is served back only to this tenant, as a download for spreadsheets
        f = self.c.get(f"/api/files/{d['invoice']['source_file_id']}")
        self.assertEqual(f.status_code, 200)
        self.assertIn("attachment", f.headers["content-disposition"])
        line = d["lines"][0]
        r = self.c.patch(f"/api/purchases/{inv_id}/lines/{line['id']}", headers=H,
                         json={"changes": {"batch_no": "P9"}, "version": ver})
        self.assertEqual(r.status_code, 200)
        ver2 = r.json()["invoice"]["version"]
        r = self.c.post(f"/api/purchases/{inv_id}/approve", headers=H, json={"version": ver})
        self.assertEqual(r.status_code, 409)                        # stale version
        r = self.c.post(f"/api/purchases/{inv_id}/approve", headers=H, json={"version": ver2})
        self.assertEqual(r.status_code, 200, r.text)
        item = self.c.get(f"/api/items/{self.pan}").json()
        self.assertEqual(item["stock"]["total_qty"], 20)

    def test_upload_garbage_rejected(self):
        self.http_login()
        r = self.c.post("/api/purchases/import", headers=H,
                        files={"file": ("x.exe", b"MZ\x00\x00binary", "application/octet-stream")})
        self.assertEqual(r.status_code, 422)

    def test_cross_tenant_via_api(self):
        self.http_login()
        data = excel_invoice([("PANADOL 500 MG 24 TAB", 1, 0, 40, 20, 32, 32, "P1", FUT, None)])
        inv_id = self.c.post("/api/purchases/import", headers=H,
                             files={"file": ("i.xlsx", data, "x")}).json()["invoice"]["id"]
        self.second_tenant()
        other = TestClient(self.app)
        self.http_login("other", "Other-Pass-2026", client=other)
        self.assertEqual(other.get(f"/api/purchases/{inv_id}").status_code, 404)
        self.assertEqual(other.get(f"/api/items/{self.pan}").status_code, 404)
        self.assertEqual(other.get("/api/purchases").json(), [])

    def test_static_shell(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('dir="rtl"', r.text)
        self.assertEqual(self.c.get("/static/app.js").status_code, 200)
        self.assertEqual(self.c.get("/sw.js").status_code, 200)

    def test_logout(self):
        self.http_login()
        self.assertEqual(self.c.post("/api/auth/logout", headers=H).status_code, 200)
        self.assertEqual(self.c.get("/api/me").status_code, 401)

    def test_internal_errors_do_not_leak(self):
        self.http_login()
        r = self.c.patch("/api/purchases/x/lines/y", headers=H, content=b"[1,2]")
        self.assertEqual(r.status_code, 422)


class TestImportAPI(APICase):
    def test_import_flow_and_export(self):
        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["كود الصنف", "اسم الصنف", "سعر الجمهور", "عدد الوحدات"])
        ws.append([501, "Concor 5mg 30 tab", 52, 3])
        b = io.BytesIO()
        wb.save(b)
        self.http_login()
        r = self.c.post("/api/imports", headers=H, data={"kind": "items"},
                        files={"file": ("items.xlsx", b.getvalue(), "x")})
        self.assertEqual(r.status_code, 201, r.text)
        run = r.json()
        self.assertEqual(run["report"]["counts"], {"create": 1})
        r = self.c.post(f"/api/imports/{run['id']}/apply", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.get("/api/export/items.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])


class TestSafetyAPI(APICase):
    def test_check_endpoint(self):
        self.http_login()
        r = self.c.post("/api/safety/check", headers=H, json={"lines": [{"item_id": self.pan}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([f["code"] for f in r.json()["findings"]],
                         ["insufficient_data", "interaction_check_unavailable"])
        r = self.c.put(f"/api/items/{self.pan}/ingredients", headers=H,
                       json={"ingredients": [{"name": "Paracetamol", "strength": "500mg"}],
                             "verify": True})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.post("/api/safety/check", headers=H, json={"lines": [{"item_id": self.pan}]})
        self.assertEqual([f["code"] for f in r.json()["findings"]],
                         ["interaction_check_unavailable"])  # never an empty "all clear"


class TestRecallAPI(APICase):
    def test_extract_and_add(self):
        self.http_login()
        r = self.c.post("/api/recalls/extract", headers=H, json={"text": "سحب التشغيلة 2400905"})
        self.assertEqual(r.json()["batches"], ["2400905"])
        r = self.c.post("/api/recalls", headers=H, json={"title": "t", "items": [
            {"product_name": "Panadol", "batch_numbers": ["2400905"]}]})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.c.get("/api/recalls/alerts").json(), [])


class TestProcurementAPI(APICase):
    def test_endpoints(self):
        self.http_login()
        self.assertEqual(self.c.get("/api/procurement/suggestions").status_code, 200)
        self.assertEqual(self.c.get("/api/procurement/health").status_code, 200)
        r = self.c.get("/api/procurement/suggestions.xlsx")
        self.assertEqual(r.status_code, 200)
        r = self.c.post("/api/procurement/offer", headers=H,
                        json={"item_id": self.pan, "packs": 5, "discount_pct": 30})
        self.assertEqual(r.json()["verdict"], "no")     # no consumption history


class TestSalesAPI(APICase):
    def test_sale_flow_and_receipt(self):
        from decimal import Decimal
        from app.inventory import service as inv
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=6, movement_type="opening",
                              unit_cost=Decimal("15"))
        self.http_login()
        self.assertEqual(self.c.post("/api/cash/open", headers=H, json={"opening_cash": 0}).status_code, 201)
        s = self.c.post("/api/sales", headers=H, json={}).json()
        s = self.c.post(f"/api/sales/{s['id']}/lines", headers=H, json={"item_id": self.pan, "packs": 1}).json()
        self.assertEqual(s["total"], 4000)
        r = self.c.post(f"/api/sales/{s['id']}/complete", headers=H,
                        json={"paid_cash": 4000, "version": s["version"]})
        self.assertEqual(r.status_code, 200, r.text)
        rc = self.c.get(f"/receipt/{s['id']}")
        self.assertEqual(rc.status_code, 200)
        self.assertIn("40.00", rc.text)
        self.assertIn("script-src 'self'", rc.headers["content-security-policy"])
        self.assertEqual(self.c.get("/api/sales/summary").json()["total"], 4000)


class TestItemsStock(APICase):
    def test_list_shows_stock_in_base_units(self):
        from decimal import Decimal
        from app.inventory import service as inv
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=5, movement_type="opening",
                              unit_cost=Decimal("16"))
        self.http_login()
        rows = self.c.get("/api/items?q=panadol").json()
        self.assertEqual(rows[0]["stock_qty"], 5)
