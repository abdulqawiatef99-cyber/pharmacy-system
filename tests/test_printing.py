"""Printing tests. The bytes are verified against the ESC/POS command layout and
delivered to a FAKE network printer (local socket). A real printer, the
Windows queue (pywin32) and the drawer pulse still need manual checks:
docs/WINDOWS_ACCEPTANCE.md."""
import unittest
import os
import socket
import threading
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Forbidden, ValidationFailed
from app.inventory import service as inv
from app.printing import escpos
from app.printing import service as printing
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase


class FakePrinter:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.data = b""
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        conn, _ = self.sock.accept()
        with conn:
            while chunk := conn.recv(65536):
                self.data += chunk
        self.sock.close()


NO_RAQM = "raqm not available here: receipts go to browser printing (checked by selftest)"


@unittest.skipUnless(escpos.shaping_available(), NO_RAQM)
class TestEscPos(DBTestCase):
    def test_raster_header_and_size(self):
        img = escpos.render_receipt([("title", "صيدلية", ""), ("row", "بانادول", "40.00")])
        r = escpos.raster_bytes(img)
        self.assertEqual(r[:4], b"\x1dv0\x00")
        wb, h = r[4] | r[5] << 8, r[6] | r[7] << 8
        self.assertEqual((wb, h), (72, img.size[1]))            # 576 dots = 72 bytes per row
        self.assertEqual(len(r), 8 + wb * h)
        self.assertTrue(any(r[8:]))                              # something is printed (not blank)

    def test_long_names_truncated_not_overlapping(self):
        img = escpos.render_receipt([("row", "اسم صنف طويل جدا " * 6, "1234.50")])
        self.assertEqual(img.size[0], escpos.DOTS_80MM)

    def test_job_with_drawer_and_cut(self):
        img = escpos.render_receipt([("row", "x", "1")])
        job = escpos.receipt_job(img, open_drawer=True)
        self.assertTrue(job.startswith(escpos.INIT))
        self.assertIn(escpos.CUT, job)
        self.assertTrue(job.endswith(escpos.DRAWER_KICK))
        self.assertNotIn(escpos.DRAWER_KICK, escpos.receipt_job(img))

    def test_transports(self):
        fp = FakePrinter()
        escpos.send(f"tcp:127.0.0.1:{fp.port}", b"HELLO")
        fp.t.join(2)
        self.assertEqual(fp.data, b"HELLO")
        out = self.tmp / "lp0"
        escpos.send(f"file:{out}", b"ABC")
        self.assertEqual(out.read_bytes(), b"ABC")
        with self.assertRaises(escpos.PrinterError):
            escpos.send("none", b"x")
        with self.assertRaises(escpos.PrinterError):              # printer off / wrong IP
            escpos.send("tcp:127.0.0.1:1", b"x", timeout=0.5)


class TestPrintService(DBTestCase):
    def setUp(self):
        super().setUp()
        it = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "public_price": 4000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=3, movement_type="opening",
                              unit_cost=Decimal("20"))
        CashDrawer(self.db).open(self.ctx, 0)
        s = Sales(self.db)
        self.sid = s.create(self.ctx)
        d = s.add_line(self.ctx, self.sid, it, units=1)
        f = s.safety_check(self.ctx, self.sid)
        s.complete(self.ctx, self.sid, paid_cash=d["total"], safety_decisions={i: "x" for i in range(len(f))})

    @unittest.skipUnless(escpos.shaping_available(), NO_RAQM)
    def test_receipt_to_network_printer_with_drawer(self):
        fp = FakePrinter()
        printing.print_receipt(self.db, self.ctx, self.sid, open_drawer=True, target=f"tcp:127.0.0.1:{fp.port}")
        fp.t.join(2)
        self.assertTrue(fp.data.startswith(escpos.INIT) and fp.data.endswith(escpos.DRAWER_KICK))
        self.assertEqual(self.db.one("SELECT count(*) FROM audit_log WHERE action='print.receipt'")[0], 1)

    def test_printer_down_is_a_clear_error_and_sale_stays(self):
        with self.assertRaises(printing.PrintFailed):
            printing.print_receipt(self.db, self.ctx, self.sid, target="tcp:127.0.0.1:1")
        self.assertEqual(Sales(self.db).get(self.ctx, self.sid)["status"], "completed")

    def test_drawer_open_needs_permission_and_reason(self):
        from app.core import accounts
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        with self.assertRaises(Forbidden):
            printing.open_drawer(self.db, actx, "فكة")
        with self.assertRaises(ValidationFailed):
            printing.open_drawer(self.db, self.ctx, "")
        out = self.tmp / "drawer"
        printing.open_drawer(self.db, self.ctx, "فكة للعميل", target=f"file:{out}")
        self.assertTrue(out.read_bytes().endswith(escpos.DRAWER_KICK))
