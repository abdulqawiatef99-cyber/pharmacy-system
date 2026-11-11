"""Direct label printing: TSPL (Xprinter/TSC) and ZPL (Zebra) jobs, settings rules, service."""
import os
import re
import tempfile

from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.printing import labels as lb, settings as rs
from app.printing.service import print_labels
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base


class TestJobs(Base):
    def test_render_and_jobs(self):
        img = lb.render("Panadol 500mg", "6221025012345", "40.00 ج", 38, 25, 203)
        self.assertEqual(img.size, (lb.dots(38, 203), lb.dots(25, 203)))       # 303 x 200 dots
        self.assertEqual(img.mode, "1")
        black = img.convert("L").histogram()[0]
        self.assertGreater(black, 1000)                                       # bars were drawn
        t = lb.tspl_job(img, 3, 38, 25)
        self.assertTrue(t.startswith(b"SIZE 38 mm,25 mm\r\nGAP 2 mm,0 mm"))
        wb = (img.size[0] + 7) // 8
        m = re.search(rb"BITMAP 0,0,(\d+),(\d+),0,", t)
        self.assertEqual((int(m.group(1)), int(m.group(2))), (wb, img.size[1]))
        data = t[m.end():t.rindex(b"\r\nPRINT")]
        self.assertEqual(len(data), wb * img.size[1])
        self.assertTrue(t.endswith(b"PRINT 1,3\r\n"))
        z = lb.zpl_job(img, 2).decode()
        self.assertTrue(z.startswith(f"^XA^PW{img.size[0]}^LL{img.size[1]}"))
        hexdata = re.search(r"\^GFA,(\d+),(\d+),(\d+),([0-9A-F]+)\^FS", z)
        self.assertEqual(int(hexdata.group(1)), wb * img.size[1])
        self.assertEqual(len(hexdata.group(4)), 2 * wb * img.size[1])
        self.assertIn("^PQ2,", z)
        # the two languages mark black dots the opposite way
        self.assertEqual(bytes(255 - b for b in data), bytes.fromhex(hexdata.group(4)))

    def test_too_long_code(self):
        from app.printing.escpos import PrinterError
        with self.assertRaises(PrinterError):
            lb.render("X", "A" * 60, "", 38, 25, 203)

    def test_settings_rules(self):
        n = rs.save(self.db, self.ctx, {"label_lang": "zpl", "label_target": "tcp:192.168.1.50:9100", "label_dpi": 203, "label_gap": 3})
        self.assertEqual((n["label_lang"], n["label_target"], n["label_gap"]), ("zpl", "tcp:192.168.1.50:9100", 3))
        for bad in ({"label_lang": "epl"}, {"label_target": "file:C:/Windows/x"}, {"label_target": "tcp:host"},
                    {"label_dpi": 600}, {"label_gap": 20}):
            with self.assertRaises(ValidationFailed, msg=bad):
                rs.save(self.db, self.ctx, bad)
        rs.save(self.db, self.ctx, {"label_target": "win:Xprinter XP-365B"})

    def test_service_writes_job(self):
        with self.assertRaises(ValidationFailed):
            print_labels(self.db, self.ctx, [(self.pan, 1)])                   # still "browser"
        rs.save(self.db, self.ctx, {"label_lang": "tspl"})
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "job.bin")
            r = print_labels(self.db, self.ctx, [(self.pan, 2)], target=f"file:{out}")
            self.assertEqual(r["labels"], 2)
            with open(out, "rb") as f:
                job = f.read()
            self.assertIn(b"PRINT 1,2", job)
        h = cat.create_item(self.db, self.ctx, {"name_en": "Secret", "public_price": 100, "hidden": True})
        with self.assertRaises(ValidationFailed):
            print_labels(self.db, self.ctx, [(self.pan, 501)], target="file:/dev/null")
        self.assertTrue(h)


class TestApi(APICase):
    def test_browser_mode_answers_clearly(self):
        self.http_login()
        r = self.c.post("/api/print/labels", json={"items": [{"id": self.pan, "count": 1}]}, headers=H)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "labels_in_browser")


class TestShift(Base):
    """#100: XP-235B, 38x13 mm: the print started several mm low and every printed label was followed by a
    blank one. The job had no way to move the print; the shift setting and a test run fix that from the app."""

    def test_no_shift(self):
        img = lb.render("Panadol", "6221025012345", "40.00 ج", 38, 13, 203)
        self.assertNotIn(b"SHIFT", lb.tspl_job(img, 1, 38, 13))              # TSPL job unchanged at 0
        self.assertIn("^LL104^LT0^LH0,0", lb.zpl_job(img, 1).decode())      # ZPL: explicit reset (Codex on #110)

    def test_zpl_shift_back_to_zero_resets_the_printer(self):
        """A test run at -2 mm, then the setting back to 0: the next ZPL job must reset ^LT, not omit it."""
        img = lb.render("Panadol", "6221025012345", "40.00 ج", 38, 13, 203)
        first = lb.job("zpl", [(img, 1)], 38, 13, 2, lb.shift_dots(-2, 203)).decode()
        back = lb.job("zpl", [(img, 1)], 38, 13, 2, lb.shift_dots(0, 203)).decode()
        self.assertIn("^LT-16^", first)
        self.assertIn("^LT0^", back)
        self.assertEqual(back.count("^LT"), 1)

    def test_shift_in_tspl_and_zpl(self):
        img = lb.render("Panadol", "6221025012345", "40.00 j", 38, 13, 203)
        t = lb.tspl_job(img, 5, 38, 13, 2, lb.shift_dots(-2.5, 203))
        self.assertTrue(t.startswith(b"SIZE 38 mm,13 mm\r\nGAP 2 mm,0 mm\r\nSHIFT -20\r\nDIRECTION 1\r\n"))
        self.assertEqual(lb.shift_dots(1, 300), 12)
        self.assertEqual(lb.shift_dots(-50, 203), -lb.dots(10, 203))       # clamped to the ±10 mm the settings allow
        self.assertIn("^LL104^LT-20^LH0,0", lb.zpl_job(img, 1, -20).decode())
        self.assertIn("^LT120^", lb.zpl_job(img, 1, 300).decode())           # ZPL's own limit
        both = lb.job("tspl", [(img, 1), (img, 2)], 38, 13, 2, 16)
        self.assertEqual(both.count(b"SHIFT 16\r\n"), 2)                     # every label of the job

    def test_setting_rules(self):
        self.assertEqual(rs.get(self.db, self.ctx.org_id)["label_shift"], 0)
        self.assertEqual(rs.save(self.db, self.ctx, {"label_shift": -2.5})["label_shift"], -2.5)
        self.assertEqual(rs.save(self.db, self.ctx, {"label_shift": 3})["label_shift"], 3.0)
        for bad in (11, -10.5, "2", True, None, float("nan")):
            with self.assertRaises(ValidationFailed, msg=repr(bad)):
                rs.save(self.db, self.ctx, {"label_shift": bad})
        self.assertEqual(rs.get(self.db, self.ctx.org_id)["label_shift"], 3.0)

    def test_saved_shift_reaches_the_printer(self):
        rs.save(self.db, self.ctx, {"label_lang": "tspl", "label_size": "38x13", "label_shift": -3})
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "job.bin")
            print_labels(self.db, self.ctx, [(self.pan, 5)], target=f"file:{out}")
            with open(out, "rb") as f:
                job = f.read()
        self.assertIn(b"SIZE 38 mm,13 mm\r\nGAP 2 mm,0 mm\r\nSHIFT -24\r\n", job)
        self.assertIn(b"PRINT 1,5", job)


class TestLabelTest(Base):
    """#100: the test button prints 3 sample labels with the values on the screen, saved or not."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        self.sent = []
        p = mock.patch("app.printing.escpos.send", side_effect=lambda dest, data, *a, **k: self.sent.append((dest, data)))
        p.start()
        self.addCleanup(p.stop)

    def test_uses_unsaved_screen_values(self):
        from app.printing.service import print_label_test
        r = print_label_test(self.db, self.ctx, {"label_lang": "tspl", "label_target": "win:Xprinter XP-235B",
                                                  "label_size": "38x13", "label_gap": 3, "label_shift": -2, "label_dpi": 203})
        self.assertEqual(r, {"ok": True, "labels": 3})
        dest, data = self.sent[0]
        self.assertEqual(dest, "win:Xprinter XP-235B")
        self.assertTrue(data.startswith(b"SIZE 38 mm,13 mm\r\nGAP 3 mm,0 mm\r\nSHIFT -16\r\n"))
        self.assertTrue(data.endswith(b"PRINT 1,3\r\n"))
        saved = rs.get(self.db, self.ctx.org_id)
        self.assertEqual((saved["label_lang"], saved["label_shift"], saved["label_gap"]), ("browser", 0, 2))   # nothing saved
        row = self.db.one("SELECT after_json FROM audit_log WHERE action='print.label_test'")
        self.assertIn('"shift_mm": -2', row["after_json"])

    def test_refusals(self):
        from app.printing.service import print_label_test
        with self.assertRaises(ValidationFailed):
            print_label_test(self.db, self.ctx, {})                                  # still "browser"
        for bad in ({"label_shift": 20}, {"label_gap": -1}, {"label_target": "file:C:/x"}, {"label_dpi": 600},
                    {"label_size": "5x5"}, {"label_lang": "epl"}):
            with self.assertRaises(ValidationFailed, msg=bad):
                print_label_test(self.db, self.ctx, {"label_lang": "tspl", "label_target": "win:X", **bad})
        with self.assertRaises(ValidationFailed):
            print_label_test(self.db, self.ctx, {"label_lang": "tspl", "label_target": ""})   # no printer chosen
        self.assertEqual(self.sent, [])

    def test_printer_error_is_reported(self):
        from unittest import mock
        from app.printing.escpos import PrinterError
        from app.printing.service import PrintFailed, print_label_test
        with mock.patch("app.printing.escpos.send", side_effect=PrinterError("offline")):
            with self.assertRaises(PrintFailed):
                print_label_test(self.db, self.ctx, {"label_lang": "tspl", "label_target": "win:X"})
        self.assertIsNone(self.db.one("SELECT 1 FROM audit_log WHERE action='print.label_test'"))



class TestLabelTestApi(APICase):
    def test_needs_settings_permission_and_answers_browser_mode(self):
        self.http_login()
        r = self.c.post("/api/print/label-test", json={}, headers=H)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "labels_in_browser")
