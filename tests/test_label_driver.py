"""#100: labels through the printer's Windows driver, like E-Stock. On the pharmacy's Xprinter XP-235B the raw
TSPL job printed early, straddled two labels and skipped every other one; E-Stock, printing the same roll
through the Windows driver (its "Printing preferences": 38 x 25 mm, portrait 180°), is exact to the millimetre.
These tests fake pywin32 and Pillow's ImageWin; the installer build prints through a real driver
(scripts/ci/driver_print_smoke.py)."""
import unittest
from unittest import mock

from app.core.errors import ValidationFailed
from app.printing import driver, escpos, service as ps, settings as rs
from app.printing.service import PrintFailed, calibrate_labels, label_preview, print_label_test, print_labels
from tests.test_cashier_acceptance import Base
from tests.test_spooler import Clock

XP235B = (304, 200, 203, 203)          # 38 x 25 mm at 203 dpi, as the pharmacy's driver reports it
A4_600 = (4960, 7016, 600, 600)


class FakeWindows:
    """win32gui (CreateDC/DeleteDC), win32print (GDI calls + the queue for the #98 check) and ImageWin.Dib."""
    JOB_CONTROL_DELETE = 5

    def __init__(self, caps=XP235B, queue="takes", fail_page=None, no_printer=False):
        self.caps = dict(zip((driver.HORZRES, driver.VERTRES, driver.LOGPIXELSX, driver.LOGPIXELSY), caps))
        self.queue, self.fail_page, self.no_printer = queue, fail_page, no_printer
        self.events, self.pages, self.images, self.jobs, self.cancelled = [], [], [], {}, []
        fake = self

        class Dib:
            def __init__(self, img):
                self.img = img

            def draw(self, hdc, rect):
                fake.pages.append((self.img.size, self.img.mode, hdc, rect))
                fake.images.append(self.img)
        self.ImageWin = type("ImageWin", (), {"Dib": Dib})

    def modules(self):
        return self, self, self.ImageWin

    # win32gui
    def CreateDC(self, drv, device, devmode):
        if self.no_printer:
            raise RuntimeError("(1801, 'CreateDC', 'The printer name is invalid.')")
        self.events.append(("CreateDC", drv, device, devmode))
        return 77

    def DeleteDC(self, hdc):
        self.events.append(("DeleteDC", hdc))

    # win32print, GDI part
    def GetDeviceCaps(self, hdc, index):
        return self.caps[index]

    def StartDoc(self, hdc, info):
        self.events.append(("StartDoc", info))
        self.jobs[41] = 0 if self.queue == "keeps" else 0x10
        return 41

    def StartPage(self, hdc):
        if self.fail_page is not None and len(self.pages) == self.fail_page:
            raise RuntimeError("(5, 'StartPage', 'Access is denied.')")
        self.events.append(("StartPage",))

    def EndPage(self, hdc):
        self.events.append(("EndPage",))

    def EndDoc(self, hdc):
        self.events.append(("EndDoc",))

    def AbortDoc(self, hdc):
        self.events.append(("AbortDoc",))

    # win32print, the queue
    def OpenPrinter(self, name):
        self.events.append(("OpenPrinter", name))
        return "h"

    def ClosePrinter(self, h):
        self.events.append(("ClosePrinter",))

    def EnumJobs(self, h, first, n, level):
        if self.queue == "takes":
            self.jobs.pop(41, None)                 # gone: the printer took it
        return [{"JobId": j, "Status": st} for j, st in self.jobs.items()]

    def SetJob(self, h, job, level, info, command):
        if command == self.JOB_CONTROL_DELETE:
            self.cancelled.append(job)
            self.jobs.pop(job, None)

    def names(self):
        return [e[0] for e in self.events]


class DriverCase(Base):
    def setUp(self):
        super().setUp()
        ps._PAGE_CACHE.clear()
        self.addCleanup(ps._PAGE_CACHE.clear)
        rs.save(self.db, self.ctx, {"label_lang": "driver", "label_target": "win:Xprinter XP-235B",
                                    "label_size": "38x13", "label_gap": 3, "label_shift": -2})

    def use(self, fake):
        p = mock.patch.object(driver, "_modules", fake.modules)
        p.start()
        self.addCleanup(p.stop)
        clock = Clock()
        real = driver.print_pages
        q = mock.patch.object(driver, "print_pages",
                              lambda *a, **k: real(*a, **{"clock": clock, "sleep": clock.sleep, **k}))
        q.start()
        self.addCleanup(q.stop)
        return fake


class TestDriverLabels(DriverCase):
    def test_prints_on_the_driver_page_one_page_per_label(self):
        f = self.use(FakeWindows())
        r = print_labels(self.db, self.ctx, [(self.pan, 2)])
        self.assertEqual((r["labels"], r["size"]), (2, "38x25"))          # Windows' size, not the 38x13 setting
        self.assertEqual(f.events[0], ("CreateDC", "WINSPOOL", "Xprinter XP-235B", None))   # its own preferences
        self.assertEqual(f.pages, [((304, 200), "RGB", 77, (0, 0, 304, 200))] * 2)   # drawn at the page size
        self.assertEqual(f.names().count("StartPage"), 2)
        self.assertEqual(f.names().count("StartDoc"), 1)
        self.assertLess(f.names().index("EndDoc"), f.names().index("DeleteDC"))
        self.assertIn("OpenPrinter", f.names())                            # #98: the queue is checked
        self.assertNotIn("AbortDoc", f.names())
        row = self.db.one("SELECT after_json FROM audit_log WHERE action='print.labels'")
        self.assertIn('"lang": "driver"', row["after_json"])
        self.assertIn('"size": "38x25"', row["after_json"])

    def test_label_test_prints_three_and_saves_nothing(self):
        rs.save(self.db, self.ctx, {"label_lang": "browser"})
        rs.save(self.db, self.ctx, {"label_variant": "tear"})           # a TSPL experiment left saved
        f = self.use(FakeWindows())
        r = print_label_test(self.db, self.ctx, {"label_lang": "driver", "label_target": "win:Xprinter XP-235B"})
        self.assertEqual(r, {"ok": True, "labels": 3, "variant": "current", "size": "38x25"})
        self.assertEqual(len(f.pages), 3)
        self.assertEqual(rs.get(self.db, self.ctx.org_id)["label_lang"], "browser")   # nothing saved

    def test_flip_turns_the_label_over(self):
        """The owner: «اطبع الملصق صحيح مش مقلوب». The pharmacy's driver prints portrait 180°."""
        f = self.use(FakeWindows())
        print_labels(self.db, self.ctx, [(self.pan, 1)])
        rs.save(self.db, self.ctx, {"label_flip": True})
        print_labels(self.db, self.ctx, [(self.pan, 1)])
        upright, flipped = f.images
        self.assertNotEqual(upright.tobytes(), flipped.tobytes())
        self.assertEqual(upright.rotate(180).tobytes(), flipped.tobytes())
        print_label_test(self.db, self.ctx, {"label_flip": False})            # the screen's unsaved value wins
        plain = f.images[-1]
        print_label_test(self.db, self.ctx, {})                                # the saved one: flipped
        self.assertEqual(plain.rotate(180).tobytes(), f.images[-1].tobytes())
        for bad in ("yes", 1, None):
            with self.assertRaises(ValidationFailed, msg=repr(bad)):
                rs.save(self.db, self.ctx, {"label_flip": bad})
        self.assertTrue(rs.get(self.db, self.ctx.org_id)["label_flip"])

    def test_printer_switched_off_leaves_nothing_in_the_queue(self):
        f = self.use(FakeWindows(queue="keeps"))
        with self.assertRaises(PrintFailed):
            print_labels(self.db, self.ctx, [(self.pan, 1)])
        self.assertEqual(f.cancelled, [41])
        self.assertIsNone(self.db.one("SELECT 1 FROM audit_log WHERE action='print.labels'"))

    def test_driver_error_aborts_the_job(self):
        f = self.use(FakeWindows(fail_page=1))
        with self.assertRaises(PrintFailed) as cm:
            print_labels(self.db, self.ctx, [(self.pan, 3)])
        self.assertIn("تعريف ويندوز", str(cm.exception))
        self.assertIn("AbortDoc", f.names())
        self.assertEqual(f.names()[-1], "DeleteDC")
        self.assertNotIn("OpenPrinter", f.names())         # aborted: no job to wait for

    def test_sheet_sized_driver_is_refused_before_printing(self):
        f = self.use(FakeWindows(caps=A4_600))
        with self.assertRaises(PrintFailed) as cm:
            print_labels(self.db, self.ctx, [(self.pan, 1)])
        self.assertIn("مش مقاس ملصق", str(cm.exception))
        self.assertNotIn("StartDoc", f.names())
        self.assertEqual(f.names()[-1], "DeleteDC")

    def test_unknown_printer(self):
        self.use(FakeWindows(no_printer=True))
        with self.assertRaises(PrintFailed) as cm:
            print_labels(self.db, self.ctx, [(self.pan, 1)])
        self.assertIn("Xprinter XP-235B", str(cm.exception))

    def test_off_windows_says_so(self):
        try:
            import win32gui  # noqa: F401
            self.skipTest("pywin32 is installed")
        except ImportError:
            pass
        with self.assertRaises(PrintFailed) as cm:
            print_labels(self.db, self.ctx, [(self.pan, 1)])
        self.assertIn("ويندوز", str(cm.exception))


class TestDriverRules(DriverCase):
    def test_driver_needs_a_windows_printer(self):
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"label_target": "tcp:192.168.1.50:9100"})
        rs.save(self.db, self.ctx, {"label_lang": "tspl", "label_target": "tcp:192.168.1.50:9100"})
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"label_lang": "driver"})
        with self.assertRaises(ValidationFailed) as cm:
            print_label_test(self.db, self.ctx, {"label_lang": "driver", "label_target": "tcp:192.168.1.50:9100"})
        self.assertEqual(cm.exception.code, "driver_needs_windows_printer")

    def test_calibration_is_done_in_windows(self):
        with self.assertRaises(ValidationFailed) as cm:
            calibrate_labels(self.db, self.ctx, {})
        self.assertEqual(cm.exception.code, "calibrate_in_windows")

    def test_preview_draws_the_driver_page(self):
        self.use(FakeWindows())
        png, dropped, info = label_preview(self.db, self.ctx, {"label_lang": "driver"})
        self.assertEqual(info, {"size": "38x25", "source": "windows"})
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_preview_falls_back_to_the_settings_size(self):
        with mock.patch.object(driver, "_modules", side_effect=escpos.PrinterError("not Windows")):
            _, _, info = label_preview(self.db, self.ctx, {"label_lang": "driver"})
        self.assertEqual(info, {"size": "38x13", "source": "settings"})
        _, _, info = label_preview(self.db, self.ctx, {"label_lang": "tspl"})
        self.assertEqual(info["source"], "settings")

    def test_page_size_text(self):
        self.assertEqual(driver.Page(*XP235B).size_text(), "38x25")
        self.assertEqual(driver.Page(304, 104, 203, 203).size_text(), "38x13")
        with self.assertRaises(escpos.PrinterError):
            driver.check_label_page(driver.Page(*A4_600))


if __name__ == "__main__":
    unittest.main()


class TestTwoLabelsPerPage(DriverCase):
    """#100 on the PC, 12/11: the driver's 38x25 mm page covers two 38x12.5 labels; one label per page
    printed across both. With label_rows=2 each page carries two labels, each on its own half."""

    def test_two_labels_share_one_page_with_the_gap_between(self):
        rs.save(self.db, self.ctx, {"label_rows": 2, "label_gap": 3})
        f = self.use(FakeWindows())
        r = print_labels(self.db, self.ctx, [(self.pan, 3)])
        self.assertEqual(r["labels"], 3)
        self.assertEqual(len(f.pages), 2)                       # 3 labels -> 2 pages, the last half empty
        page = f.images[0].convert("1")
        self.assertEqual(page.size, (304, 200))
        row = round((25.025 - 3) / 2 * 203 / 25.4)
        gap_rows = range(row + 2, round((row * 25.4 / 203 + 3) * 203 / 25.4) - 2)
        self.assertTrue(all(page.getpixel((x, y)) for y in gap_rows for x in range(304)))   # the gap stays blank
        top, bottom = page.crop((0, 0, 304, row)), page.crop((0, 200 - row, 304, 200))
        self.assertLess(top.histogram()[0], top.size[0] * top.size[1])                   # something drawn
        self.assertLess(bottom.histogram()[0], bottom.size[0] * bottom.size[1])
        last = f.images[1].convert("1").crop((0, 200 - row, 304, 200))
        self.assertEqual(last.histogram()[0], 0)                                         # odd label: half empty

    def test_rows_must_be_one_to_four(self):
        for bad in (0, 5, "2", True):
            with self.assertRaises(ValidationFailed, msg=repr(bad)):
                rs.save(self.db, self.ctx, {"label_rows": bad})

    def test_preview_shows_one_label_of_the_page(self):
        rs.save(self.db, self.ctx, {"label_rows": 2, "label_gap": 3})
        self.use(FakeWindows())
        _, _, info = label_preview(self.db, self.ctx, {"label_lang": "driver"})
        self.assertEqual(info, {"size": "38x11", "source": "windows"})


class TestLabelLanguage(DriverCase):
    """Owner, 12/11: the label's words in English (default) or Arabic; text shrinks before it is cut."""

    def test_english_by_default_arabic_on_request(self):
        from app.printing.service import label_data
        rs.save(self.db, self.ctx, {"label_pharmacy": "صيدلية تجربة", "label_pharmacy_en": "Test Pharmacy"})
        cfg = rs.get(self.db, self.ctx.org_id)
        self.assertEqual(cfg["label_text_lang"], "en")
        it = self.db.one("SELECT * FROM items WHERE id=?", (self.pan,))
        en = label_data(self.db, self.ctx, it, cfg)
        self.assertEqual(en["pharmacy"], "Test Pharmacy")
        self.assertTrue(en["price"].endswith(" EGP"))
        rs.save(self.db, self.ctx, {"label_text_lang": "ar"})
        ar = label_data(self.db, self.ctx, it, rs.get(self.db, self.ctx.org_id))
        self.assertEqual(ar["pharmacy"], "صيدلية تجربة")
        self.assertTrue(ar["price"].endswith(" ج"))
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"label_text_lang": "fr"})

    def test_long_text_shrinks_before_it_is_cut(self):
        from PIL import ImageDraw, ImageFont
        from app.printing import labels as lb
        captured = []
        real = lb._fit

        def spy(d, text, font, width, **kw):
            captured.append((text, font.size))
            return real(d, text, font, width, **kw)
        data = {"name": "X", "value": "6221025012340", "code": "", "price": "40.00 EGP", "expiry": "10/27",
                "pharmacy": "Abdul Qawi Pharmacy", "phone": "01040300507"}
        with mock.patch.object(lb, "_fit", spy):
            lb.render_label(data, 38, 11.5, 203, {**lb.DEFAULT_FIELDS, "pharmacy": True, "phone": True})
        sizes = dict(captured)
        self.assertLess(sizes["Abdul Qawi Pharmacy"], max(s for _, s in captured))   # made smaller, not just cut


class TestRowsAndShrinkEdges(DriverCase):
    def test_rows_keep_their_place_when_dpi_differs(self):
        rs.save(self.db, self.ctx, {"label_rows": 2, "label_gap": 2})
        f = self.use(FakeWindows(caps=(450, 590, 300, 600)))       # 38.1 x 25.0 mm at 300 x 600 dpi
        print_labels(self.db, self.ctx, [(self.pan, 2)])
        page = f.images[0]
        self.assertEqual(page.size, (450, round(590 * 25.4 / 600 * 300 / 25.4)))   # square at 300 dpi
        self.assertEqual(f.pages[0][3], (0, 0, 450, 590))                          # stretched to the page

    def test_shrinking_stops_at_seventy_percent(self):
        from app.printing import labels as lb
        sizes = []
        real = lb._fit

        def spy(d, text, font, width, **kw):
            sizes.append((text, font.size))
            return real(d, text, font, width, **kw)
        data = {"name": "A very long medicine name that cannot fit on a small label at all " * 2, "value": "6221025012340",
                "code": "", "price": "", "expiry": "", "pharmacy": "", "phone": ""}
        with mock.patch.object(lb, "_fit", spy):
            lb.render_label(data, 38, 25, 300, lb.DEFAULT_FIELDS)
        start = max(10, min(round(25 * 300 / 25.4) // 7, round(3.2 * 300 / 25.4)))
        self.assertGreaterEqual(dict(sizes)[data["name"]], max(10, int(start * 0.7)))


class TestGapOnScreen(DriverCase):
    def test_preview_follows_the_gap_on_the_screen(self):
        rs.save(self.db, self.ctx, {"label_rows": 2, "label_gap": 2})
        self.use(FakeWindows())
        _, _, a = label_preview(self.db, self.ctx, {"label_lang": "driver", "label_gap": 0})
        _, _, b = label_preview(self.db, self.ctx, {"label_lang": "driver", "label_gap": 4})
        self.assertEqual((a["size"], b["size"]), ("38x12.5", "38x10.5"))


class TestLabelClarity(DriverCase):
    """Owner's photo, 12/11: thin, small type and the right edge cut on the 38x11.5 mm labels."""

    def test_bold_face_when_it_exists_regular_otherwise(self):
        import tempfile
        from pathlib import Path
        from app.printing import labels as lb
        with tempfile.TemporaryDirectory() as d:
            reg, bold = Path(d) / "tahoma.ttf", Path(d) / "tahomabd.ttf"
            reg.write_bytes(b"x")
            self.assertEqual(lb.bold_font(str(reg)), str(reg))           # no bold file: the regular one
            bold.write_bytes(b"x")
            self.assertEqual(lb.bold_font(str(reg)), str(bold))
            other = Path(d) / "custom.ttf"
            self.assertEqual(lb.bold_font(str(other)), str(other))

    def test_type_is_a_sixth_of_the_label_with_tight_rows(self):
        from app.printing import labels as lb
        self.assertEqual(lb.text_metrics(11.5, 203), (15, 16))          # was 13 and 15
        line, row = lb.text_metrics(11.5, 203)
        self.assertGreaterEqual(lb.dots(11.5, 203) - 2 * max(3, lb.dots(lb.PAD_MM, 203)) - 3 * row,
                                lb.dots(lb.MIN_BAR_MM, 203))                  # the 4 mm barcode still fits

    def _edges(self, img):
        bw = img.convert("1")
        cols = [x for x in range(bw.size[0]) if any(bw.getpixel((x, y)) == 0 for y in range(bw.size[1]))]
        return cols[0], cols[-1]

    def test_driver_text_stays_1_5_mm_from_the_sides(self):
        from app.printing import labels as lb
        edge = lb.dots(1.5, 203)
        for rows in (1, 2):
            with self.subTest(rows=rows):
                rs.save(self.db, self.ctx, {"label_rows": rows, "label_fields": {"barcode": False, "phone": True,
                                                                                  "pharmacy": True, "price": True}})
                f = self.use(FakeWindows())
                print_labels(self.db, self.ctx, [(self.pan, rows)])
                left, right = self._edges(f.images[-1])
                self.assertGreaterEqual(left, edge - 1)
                self.assertLessEqual(right, f.images[-1].size[0] - edge)
