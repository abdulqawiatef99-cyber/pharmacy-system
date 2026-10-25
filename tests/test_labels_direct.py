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
