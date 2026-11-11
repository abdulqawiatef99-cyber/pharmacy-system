"""#140 cheap hardening: request size limits before reading, spreadsheet bombs, 422 instead of 500 on
bad fields, logout CSRF, and no query strings (phone numbers, names) in the server log."""
import asyncio
import io
import logging
import logging.config
import zipfile
from dataclasses import replace
from datetime import date
from unittest import mock

import openpyxl
from starlette.testclient import TestClient

from app.api.app import COOKIE, MAX_JSON, create_app
from app.core.accesslog import PathOnly, uvicorn_log_config
from app.core.errors import ValidationFailed
from app.procurement import deadstock
from app.purchasing.importers import files
from tests.fixtures.make_invoices import excel_invoice
from tests.test_api import FUT, APICase, H


def bomb_xlsx(dimension: str, last_cell: str) -> bytes:
    """A tiny workbook that declares a huge sheet: read_only openpyxl pads every row to it."""
    wb = openpyxl.Workbook()
    wb.active["A1"] = 1
    b = io.BytesIO()
    wb.save(b)
    zin, out = zipfile.ZipFile(io.BytesIO(b.getvalue())), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            d = zin.read(i.filename)
            if i.filename == "xl/worksheets/sheet1.xml":
                row = "".join(ch for ch in last_cell if ch.isdigit())
                d = d.replace(b'<dimension ref="A1:A1"/>', f'<dimension ref="{dimension}"/>'.encode())
                d = d.replace(b"</sheetData>", f'<row r="{row}"><c r="{last_cell}"><v>1</v></c></row></sheetData>'.encode())
            zout.writestr(i, d)
    return out.getvalue()


class TestRequestSize(APICase):
    def test_json_body_refused_from_content_length(self):
        r = self.c.post("/api/auth/login", headers={**H, "Content-Type": "application/json"},
                        content=b"{" + b" " * (MAX_JSON + 10) + b"}")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "too_large")

    def test_body_without_length_is_not_read_past_the_limit(self):
        """A chunked body with no Content-Length: reading stops at the limit, not at the end."""
        app, chunk, total = self.app, b" " * 65536, 60
        sent, msgs = [0], []

        async def receive():
            if sent[0] >= total:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent[0] += 1
            return {"type": "http.request", "body": chunk, "more_body": True}

        async def send(m):
            msgs.append(m)

        scope = {"type": "http", "method": "POST", "path": "/api/auth/login", "raw_path": b"/api/auth/login",
                 "query_string": b"", "root_path": "", "scheme": "http", "http_version": "1.1",
                 "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 5000),
                 "headers": [(b"host", b"127.0.0.1:8000"), (b"content-type", b"application/json"),
                             (b"x-requested-with", b"pharm")]}
        asyncio.run(app(scope, receive, send))
        self.assertEqual(msgs[0]["status"], 422)
        self.assertLessEqual(sent[0], MAX_JSON // len(chunk) + 2)

    def _small_app(self):
        return TestClient(create_app(self.db, replace(self.s, max_upload_bytes=2000)))

    def test_upload_routes_refuse_oversized_from_content_length(self):
        c = self._small_app()
        self.http_login(client=c)
        big = {"file": ("a.xlsx", b"x" * 300_000, "x")}
        routes = ["/api/imports", "/api/purchases/import", "/api/procurement/consumption",
                  f"/api/sales/none/attachments"]
        for path in routes:
            r = c.post(path, headers=H, files=big)
            self.assertEqual(r.status_code, 422, path)
            self.assertEqual(r.json()["error"]["code"], "too_large", path)
        three = {k: (f"{k}.xlsx", b"x" * 300_000, "x") for k in ("stock", "sales", "purchases")}
        r = c.post("/api/procurement/deadstock", headers=H, files=three)
        self.assertEqual(r.json()["error"]["code"], "too_large")

    def test_multipart_upload_with_idempotency_key_still_works(self):
        self.http_login()
        data = excel_invoice([("PANADOL 500 MG 24 TAB", 1, 0, 40, 20, 32, 32, "P1", FUT, None)])
        r = self.c.post("/api/purchases/import", headers={**H, "Idempotency-Key": "k-upload-1"},
                        files={"file": ("i.xlsx", data, "x")})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["invoice"]["id"])     # the body read once for the key is reused by the form


class TestSpreadsheetBomb(APICase):
    def test_declared_huge_sheet_refused_quickly(self):
        with mock.patch.object(files, "MAX_XLSX_CELLS", 10_000, create=True):
            with self.assertRaises(ValidationFailed) as e:
                files.read_excel_sheets(bomb_xlsx("A1:Z1000", "Z1000"))
            self.assertEqual(e.exception.code, "too_large")
            with self.assertRaises(ValidationFailed):
                deadstock._rows(bomb_xlsx("A1:Z1000", "Z1000"))

    def test_unpacked_size_limit(self):
        with mock.patch.object(files, "MAX_XLSX_UNPACKED", 1000, create=True):
            with self.assertRaises(ValidationFailed):
                files.read_excel_sheets(bomb_xlsx("A1:B2", "B2"))

    def test_normal_workbook_still_reads(self):
        rows = files.read_excel_sheets(bomb_xlsx("A1:C3", "C3"))[0][1]
        self.assertEqual(len(rows), 2)


class TestBadFields(APICase):
    def test_import_bad_mapping_and_header_row_answer_422(self):
        self.http_login()
        f = {"file": ("a.csv", "code,name_en\n1,X\n".encode(), "text/csv")}
        for fields in ({"mapping": "not json"}, {"mapping": "[1]"}, {"mapping": '{"a": "name_en"}'},
                       {"mapping": '{"0": ["x"]}'}, {"header_row": "x"}):
            r = self.c.post("/api/imports", headers=H, files=f, data=fields)
            self.assertEqual(r.status_code, 422, (fields, r.text))

    def test_item_flags_with_non_string_ids_answers_422(self):
        self.http_login()
        r = self.c.post("/api/items/flags", headers=H, json={"item_ids": [{"x": 1}], "rx_required": True})
        self.assertEqual(r.status_code, 422)


class TestLogout(APICase):
    def test_cookie_logout_needs_csrf_header(self):
        self.http_login()
        r = self.c.post("/api/auth/logout")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.c.get("/api/me").status_code, 200)       # still signed in
        self.assertEqual(self.c.post("/api/auth/logout", headers=H).status_code, 200)
        self.assertEqual(self.c.get("/api/me").status_code, 401)


class TestAccessLog(APICase):
    def test_query_string_cut_from_access_log(self):
        rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                ("127.0.0.1:5000", "GET", "/api/customers?q=01012345678", "1.1", 200), None)
        self.assertTrue(PathOnly().filter(rec))
        self.assertNotIn("0101", rec.getMessage())
        self.assertIn("/api/customers", rec.getMessage())

    def test_serve_installs_the_filter(self):
        cfg = uvicorn_log_config()
        self.assertIn("path_only", cfg["handlers"]["access"]["filters"])
        logging.config.dictConfig(cfg)
        logger = logging.getLogger("uvicorn.access")
        buf = io.StringIO()
        logger.handlers[0].stream = buf
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/api/customers?q=Ahmed", "1.1", 200)
        self.assertNotIn("Ahmed", buf.getvalue())
