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
