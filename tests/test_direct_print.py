"""Trial #1 + #8 (#36): receipt printer chosen on the screen, the drawer opens even when the receipt
cannot be drawn here (no Arabic shaping), browser printing is not an error, printable pages can be
framed by the app itself (hidden-frame printing, no pop-up)."""
import os
import unittest
from decimal import Decimal
from unittest import mock

from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.inventory import service as inv
from app.printing import escpos, settings as rs
from app.printing import service as printing
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase
from tests.test_api import APICase, H
from tests.test_printing import NO_RAQM, FakePrinter


class TestReceiptPrinterSetting(DBTestCase):
    def test_saved_on_screen_wins_over_env_and_is_validated(self):
        with mock.patch.dict(os.environ, {"PHARM_PRINTER": "tcp:10.0.0.9:9100"}):
            self.assertEqual(printing.printer_target(self.db, self.ctx.org_id), "tcp:10.0.0.9:9100")
            rs.save(self.db, self.ctx, {"receipt_target": "win:POS-80C"})
            self.assertEqual(printing.printer_target(self.db, self.ctx.org_id), "win:POS-80C")
        for bad in ("file:C:/Windows/win.ini", "file:/dev/usb/lp0", "lpt1", "win:\x00x"):
            with self.assertRaises(ValidationFailed):
                rs.save(self.db, self.ctx, {"receipt_target": bad})
        rs.save(self.db, self.ctx, {"receipt_target": ""})
        with mock.patch.dict(os.environ, {"PHARM_PRINTER": ""}):
            self.assertEqual(printing.printer_target(self.db, self.ctx.org_id), "none")

    def test_browser_on_purpose_beats_the_env_printer(self):
        # Codex on #37: clearing the field falls back to PHARM_PRINTER, so "browser" must exist on its own
        with mock.patch.dict(os.environ, {"PHARM_PRINTER": "tcp:10.0.0.9:9100"}):
            rs.save(self.db, self.ctx, {"receipt_target": ""})
            self.assertEqual(printing.printer_target(self.db, self.ctx.org_id), "tcp:10.0.0.9:9100")
            self.assertEqual(rs.save(self.db, self.ctx, {"receipt_target": "browser"})["receipt_target"], "browser")
            self.assertEqual(printing.printer_target(self.db, self.ctx.org_id), "none")
        with self.assertRaises(ValidationFailed):                     # only the receipt printer takes it
            rs.save(self.db, self.ctx, {"label_target": "browser"})


class TestDrawerWithoutDirectReceipt(DBTestCase):
    def setUp(self):
        super().setUp()
        it = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "public_price": 4000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=3, movement_type="opening", unit_cost=Decimal("20"))
        CashDrawer(self.db).open(self.ctx, 0)
        s = Sales(self.db)
        self.sid = s.create(self.ctx)
        d = s.add_line(self.ctx, self.sid, it, units=1)
        f = s.safety_check(self.ctx, self.sid)
        s.complete(self.ctx, self.sid, paid_cash=d["total"], safety_decisions={i: "x" for i in range(len(f))})

    def audits(self, action):
        return self.db.one("SELECT count(*) FROM audit_log WHERE action=?", (action,))[0]

    def test_no_shaping_still_opens_the_drawer(self):
        fp = FakePrinter()
        rs.save(self.db, self.ctx, {"receipt_target": f"tcp:127.0.0.1:{fp.port}"})
        with mock.patch.object(escpos, "shaping_available", return_value=False):
            with self.assertRaises(printing.PrintFailed) as cm:
                printing.print_receipt(self.db, self.ctx, self.sid, open_drawer=True)
        fp.t.join(2)
        self.assertEqual(fp.data, escpos.INIT + escpos.DRAWER_KICK)
        self.assertEqual(cm.exception.code, "print_failed")
        self.assertTrue(cm.exception.details["drawer_opened"])
        self.assertEqual(self.audits("cash.drawer_auto"), 1)
        self.assertEqual(self.audits("print.receipt"), 0)

    def test_no_shaping_without_cash_sends_nothing(self):
        out = self.tmp / "lp"
        with mock.patch.object(escpos, "shaping_available", return_value=False):
            with self.assertRaises(printing.PrintFailed) as cm:
                printing.print_receipt(self.db, self.ctx, self.sid, open_drawer=False, target=f"file:{out}")
        self.assertFalse(out.exists())
        self.assertFalse(cm.exception.details["drawer_opened"])

    def test_printer_down_is_not_tried_twice(self):
        from PIL import Image
        # the receipt is drawn (shaping or not on this machine); only the printer is unreachable
        with mock.patch.object(escpos, "render_receipt", return_value=Image.new("L", (8, 8), 255)), \
                mock.patch.object(escpos, "send", side_effect=escpos.PrinterError("off")) as send:
            with self.assertRaises(printing.PrintFailed) as cm:
                printing.print_receipt(self.db, self.ctx, self.sid, open_drawer=True, target="tcp:127.0.0.1:1")
        self.assertEqual(send.call_count, 1)
        self.assertFalse(cm.exception.details["drawer_opened"])
        self.assertEqual(self.audits("cash.drawer_auto"), 0)

    def test_browser_printing_chosen_is_its_own_code(self):
        with mock.patch.dict(os.environ, {"PHARM_PRINTER": "none"}):
            with self.assertRaises(printing.PrintFailed) as cm:
                printing.print_receipt(self.db, self.ctx, self.sid, open_drawer=True)
        self.assertEqual(cm.exception.code, "no_printer")
        self.assertEqual(self.audits("cash.drawer_auto"), 0)

    @unittest.skipUnless(escpos.shaping_available(), NO_RAQM)
    def test_saved_printer_gets_receipt_and_drawer(self):
        fp = FakePrinter()
        rs.save(self.db, self.ctx, {"receipt_target": f"tcp:127.0.0.1:{fp.port}"})
        printing.print_receipt(self.db, self.ctx, self.sid, open_drawer=True)
        fp.t.join(2)
        self.assertTrue(fp.data.startswith(escpos.INIT) and fp.data.endswith(escpos.DRAWER_KICK))

    def test_drawer_button_uses_saved_printer(self):
        out = self.tmp / "drawer"
        with mock.patch.object(rs, "check_target", side_effect=lambda v, *a, **k: str(v)):   # a file only in this test
            rs.save(self.db, self.ctx, {"receipt_target": f"file:{out}"})
        printing.open_drawer(self.db, self.ctx, "تجربة الدرج")
        self.assertEqual(out.read_bytes(), escpos.INIT + escpos.DRAWER_KICK)


class TestPrintAPI(APICase):
    def test_printers_test_print_and_framing(self):
        self.http_login()
        r = self.c.get("/api/print/printers", headers=H).json()
        self.assertIsInstance(r["printers"], list)
        self.assertIn("shaping", r)
        r = self.c.post("/api/print/test", json={"target": "file:C:/x"}, headers=H)
        self.assertEqual(r.status_code, 422)
        if escpos.shaping_available():
            fp = FakePrinter()
            r = self.c.post("/api/print/test", json={"target": f"tcp:127.0.0.1:{fp.port}"}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            fp.t.join(2)
            self.assertTrue(fp.data.startswith(escpos.INIT))
        self.assertEqual(self.c.put("/api/settings/receipt", json={"receipt_target": "win:POS-80C"},
                                    headers=H).json()["receipt_target"], "win:POS-80C")
        # printable pages may be framed by the app (hidden-frame printing); the app itself may not
        page = self.c.get("/receipt/x", headers=H)
        self.assertIn("frame-ancestors 'self'", page.headers["content-security-policy"])
        self.assertEqual(page.headers["x-frame-options"], "SAMEORIGIN")
        home = self.c.get("/", headers=H)
        self.assertIn("frame-ancestors 'none'", home.headers["content-security-policy"])
        self.assertEqual(home.headers["x-frame-options"], "DENY")


if __name__ == "__main__":
    unittest.main()
