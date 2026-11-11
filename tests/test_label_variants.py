"""#100 follow-up: TSPL job variants for the XP-235B test visit, and the label roll calibration action.
The exact bytes of every variant, the default job unchanged, input checks, permission and audit."""
import json
import re
from unittest import mock

from app.core import people
from app.core.errors import Forbidden, ValidationFailed
from app.printing import labels as lb, settings as rs
from app.printing.escpos import PrinterError
from app.printing.service import PrintFailed, calibrate_labels, print_label_test, print_labels
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PW = "Staff-Pass-2026"
ROLL = {"label_lang": "tspl", "label_target": "win:Xprinter XP-235B", "label_size": "38x13", "label_gap": 3,
        "label_dpi": 203}


def _img(h_dots=104):
    from PIL import Image
    img = Image.new("1", (304, h_dots), 1)
    img.putpixel((0, h_dots - 1), 0)                 # a black dot on the last row: shows whether it was cut
    return img


def _bitmap(job: bytes) -> bytes:
    m = re.search(rb"BITMAP 0,0,(\d+),(\d+),0,", job)
    return job[m.end():m.end() + int(m.group(1)) * int(m.group(2))]


def _text(job: bytes) -> str:
    """The job with every BITMAP's data replaced by <data>, so the command lines can be compared exactly."""
    out, pos = b"", 0
    for m in re.finditer(rb"BITMAP 0,0,(\d+),(\d+),0,", job):
        n = int(m.group(1)) * int(m.group(2))
        out += job[pos:m.end()] + b"<data>"
        pos = m.end() + n
    return (out + job[pos:]).decode("ascii")


HEAD = "SIZE 38 mm,13 mm\r\nGAP 3 mm,0 mm\r\n"
BODY = "CLS\r\nBITMAP 0,0,38,{h},0,<data>"


class TestVariantBytes(Base):
    def job(self, variant, shift=0, copies=3, imgs=None):
        return lb.job("tspl", imgs or [(_img(), copies)], 38, 13, 3, shift, variant, 203)

    def test_current_is_the_old_job_byte_for_byte(self):
        img = _img()
        old = lb.tspl_job(img, 3, 38, 13, 3, 40)                           # the pre-variant call signature
        self.assertEqual(self.job("current", 40), old)
        self.assertEqual(lb.job("tspl", [(img, 3)], 38, 13, 3, 40), old)     # default variant = current
        self.assertEqual(_text(old), HEAD + "SHIFT 40\r\nDIRECTION 1\r\nREFERENCE 0,0\r\n" + BODY.format(h=104)
                         + "\r\nPRINT 1,3\r\n")
        self.assertEqual(_text(self.job("current")), HEAD + "DIRECTION 1\r\nREFERENCE 0,0\r\n" + BODY.format(h=104)
                         + "\r\nPRINT 1,3\r\n")                              # no SHIFT line at 0, as before

    def test_each_variant_exact_commands(self):
        tear = "SET PEEL OFF\r\nSET TEAR ON\r\nOFFSET 0 mm\r\n"
        ref = "DIRECTION 1\r\nREFERENCE 0,0\r\n"
        one, per = BODY.format(h=104) + "\r\nPRINT 1,3\r\n", (BODY.format(h=104) + "\r\nPRINT 1\r\n") * 3
        clamped = 104 - 4                                                    # 0.5 mm margin at shift 0
        want = {
            "tear": HEAD + tear + "SHIFT 0\r\n" + ref + one,
            "clamp": HEAD + ref + BODY.format(h=clamped) + "\r\nPRINT 1,3\r\n",
            "tear_clamp": HEAD + tear + "SHIFT 0\r\n" + ref + BODY.format(h=clamped) + "\r\nPRINT 1,3\r\n",
            "per_label": HEAD + ref + per,
            "tear_clamp_per_label": HEAD + tear + "SHIFT 0\r\n" + ref + (BODY.format(h=clamped) + "\r\nPRINT 1\r\n") * 3,
            "home": HEAD + ref + "HOME\r\n" + one,
            "all": HEAD + tear + "SHIFT 0\r\n" + ref + "HOME\r\n" + (BODY.format(h=clamped) + "\r\nPRINT 1\r\n") * 3,
        }
        self.assertEqual(set(want) | {"current"}, set(lb.VARIANTS))
        for v, text in want.items():
            self.assertEqual(_text(self.job(v)), text, v)

    def test_clamp_follows_a_downward_shift_and_keeps_the_top(self):
        img = _img()
        j = self.job("clamp", shift=40)                                     # +5 mm at 203 dpi
        self.assertIn("BITMAP 0,0,38,60,0,", _text(j))                      # 104 - 40 - 4
        self.assertEqual(_bitmap(j), _bitmap(lb.tspl_job(img.crop((0, 0, 304, 60)), 3, 38, 13, 3, 40)))
        self.assertIn("BITMAP 0,0,38,100,0,", _text(self.job("clamp", shift=-16)))   # upward: only the margin
        self.assertEqual(lb.clamp_rows(13, 203, 500), 1)                     # never an empty bitmap

    def test_setup_lines_once_per_run_with_several_items(self):
        j = _text(self.job("all", imgs=[(_img(), 2), (_img(), 1)]))
        self.assertEqual((j.count("SET TEAR ON"), j.count("HOME"), j.count("OFFSET 0 mm")), (1, 1, 1))
        self.assertEqual((j.count("SIZE 38 mm,13 mm"), j.count("SHIFT 0"), j.count("PRINT 1\r\n")), (2, 2, 3))

    def test_zpl_is_the_same_for_every_variant_and_unknown_is_refused(self):
        z = lb.job("zpl", [(_img(), 2)], 38, 13, 3, 0)
        for v in lb.VARIANTS:
            self.assertEqual(lb.job("zpl", [(_img(), 2)], 38, 13, 3, 0, v), z)
        with self.assertRaises(PrinterError):
            lb.job("tspl", [(_img(), 2)], 38, 13, 3, 0, "fast")

    def test_calibrate_bytes(self):
        self.assertEqual(lb.calibrate_job(38, 13, 3, 203), b"SIZE 38 mm,13 mm\r\nGAP 3 mm,0 mm\r\nGAPDETECT 104,24\r\n")
        self.assertEqual(lb.calibrate_job(38, 13, 2, 300), b"SIZE 38 mm,13 mm\r\nGAP 2 mm,0 mm\r\nGAPDETECT 154,24\r\n")
        self.assertEqual(lb.calibrate_job(38, 13, 3, 203, "auto"), b"GAPDETECT\r\n")
        for bad in ((38, 13, 0, 203, "sized"), (38, 13, 3, 203, "x")):
            with self.assertRaises(PrinterError):
                lb.calibrate_job(*bad)


class Sent(Base):
    def setUp(self):
        super().setUp()
        self.sent = []
        p = mock.patch("app.printing.escpos.send", side_effect=lambda dest, data, *a, **k: self.sent.append((dest, data)))
        p.start()
        self.addCleanup(p.stop)

    def assistant(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        return self.login("asst", PW)


class TestLabelTestVariants(Sent):
    def test_variant_on_the_test_button_not_saved(self):
        r = print_label_test(self.db, self.ctx, {**ROLL, "label_shift": 5, "label_variant": "tear_clamp"})
        self.assertEqual(r, {"ok": True, "labels": 3, "variant": "tear_clamp"})
        t = _text(self.sent[0][1])
        self.assertIn("SET TEAR ON\r\nOFFSET 0 mm\r\nSHIFT 40\r\n", t)
        self.assertIn("BITMAP 0,0,38,60,0,", t)
        self.assertEqual(rs.get(self.db, self.ctx.org_id)["label_variant"], "current")        # nothing saved
        a = json.loads(self.db.one("SELECT after_json FROM audit_log WHERE action='print.label_test'")["after_json"])
        self.assertEqual((a["variant"], a["shift_mm"], a["gap"]), ("tear_clamp", 5, 3))

    def test_default_test_job_unchanged(self):
        print_label_test(self.db, self.ctx, {**ROLL, "label_shift": -2})
        t = _text(self.sent[0][1])
        self.assertTrue(t.startswith(HEAD + "SHIFT -16\r\nDIRECTION 1\r\nREFERENCE 0,0\r\nCLS\r\nBITMAP 0,0,38,104,0,"), t)
        self.assertTrue(t.endswith("PRINT 1,3\r\n"))

    def test_refusals(self):
        for bad in ("fast", "", None, 3, ["tear"], "CURRENT"):
            with self.assertRaises(ValidationFailed, msg=bad):
                print_label_test(self.db, self.ctx, {**ROLL, "label_variant": bad})
        with self.assertRaises(ValidationFailed) as e:
            print_label_test(self.db, self.ctx, {**ROLL, "label_lang": "zpl", "label_variant": "tear"})
        self.assertEqual(e.exception.code, "variant_tspl_only")
        self.assertEqual(self.sent, [])
        with self.assertRaises(Forbidden):
            print_label_test(self.db, self.assistant(), {**ROLL, "label_variant": "tear"})


class TestSavedVariant(Sent):
    def test_saved_variant_drives_item_labels(self):
        rs.save(self.db, self.ctx, {k: v for k, v in ROLL.items()})
        print_labels(self.db, self.ctx, [(self.pan, 2)])
        self.assertNotIn("SET TEAR", _text(self.sent[-1][1]))                 # default: as before
        self.assertTrue(_text(self.sent[-1][1]).endswith("PRINT 1,2\r\n"))
        n = rs.save(self.db, self.ctx, {"label_variant": "tear_clamp_per_label"})
        self.assertEqual(n["label_variant"], "tear_clamp_per_label")
        print_labels(self.db, self.ctx, [(self.pan, 2)])
        t = _text(self.sent[-1][1])
        self.assertIn("SET TEAR ON", t)
        self.assertEqual(t.count("PRINT 1\r\n"), 2)
        a = json.loads(self.db.one("SELECT after_json FROM audit_log WHERE action='settings.receipt' ORDER BY rowid DESC LIMIT 1")["after_json"])
        self.assertEqual(a["label_variant"], "tear_clamp_per_label")

    def test_save_refuses_unknown_and_stale_value_falls_back(self):
        for bad in ("fast", None, 1):
            with self.assertRaises(ValidationFailed, msg=bad):
                rs.save(self.db, self.ctx, {"label_variant": bad})
        self.assertEqual(rs.get(self.db, self.ctx.org_id)["label_variant"], "current")
        rs.save(self.db, self.ctx, dict(ROLL))
        with self.db.transaction():                                         # a value from a newer/older version
            r = self.db.one("SELECT value FROM org_settings WHERE org_id=? AND key='receipt'", (self.ctx.org_id,))
            v = json.loads(r["value"])
            v["label_variant"] = "gone"
            self.db.execute("UPDATE org_settings SET value=? WHERE org_id=? AND key='receipt'", (json.dumps(v), self.ctx.org_id))
        print_labels(self.db, self.ctx, [(self.pan, 1)])
        self.assertNotIn("SET TEAR", _text(self.sent[-1][1]))


class TestCalibrate(Sent):
    def test_sends_gapdetect_for_the_screen_roll_and_audits(self):
        r = calibrate_labels(self.db, self.ctx, dict(ROLL))
        self.assertEqual(r, {"ok": True, "mode": "sized"})
        self.assertEqual(self.sent, [("win:Xprinter XP-235B", b"SIZE 38 mm,13 mm\r\nGAP 3 mm,0 mm\r\nGAPDETECT 104,24\r\n")])
        a = json.loads(self.db.one("SELECT after_json FROM audit_log WHERE action='print.label_calibrate'")["after_json"])
        self.assertEqual(a, {"target": "win:Xprinter XP-235B", "size": "38x13", "gap": 3, "dpi": 203, "mode": "sized"})
        calibrate_labels(self.db, self.ctx, {**ROLL, "mode": "auto"})
        self.assertEqual(self.sent[-1][1], b"GAPDETECT\r\n")
        self.assertEqual(rs.get(self.db, self.ctx.org_id)["label_lang"], "browser")             # nothing saved

    def test_uses_saved_settings_when_screen_sends_nothing(self):
        rs.save(self.db, self.ctx, {**{k: v for k, v in ROLL.items() if k != "label_gap"}, "label_gap": 2})
        calibrate_labels(self.db, self.ctx, {})
        self.assertEqual(self.sent[-1][1], b"SIZE 38 mm,13 mm\r\nGAP 2 mm,0 mm\r\nGAPDETECT 104,16\r\n")

    def test_permission_and_refusals(self):
        with self.assertRaises(Forbidden):
            calibrate_labels(self.db, self.assistant(), dict(ROLL))
        for bad in ({"mode": "fast"}, {"mode": None}, {"label_gap": 0}, {"label_gap": 11}, {"label_gap": True},
                    {"label_target": "file:C:/x"}, {"label_size": "5x5"}, {"label_dpi": 600}, {"label_lang": "zpl"},
                    {"label_lang": "epl"}, {"label_target": ""}):
            with self.assertRaises(ValidationFailed, msg=bad):
                calibrate_labels(self.db, self.ctx, {**ROLL, **bad})
        with self.assertRaises(ValidationFailed) as e:
            calibrate_labels(self.db, self.ctx, {})                          # still "browser"
        self.assertEqual(e.exception.code, "labels_in_browser")
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.db.one("SELECT 1 FROM audit_log WHERE action='print.label_calibrate'"))

    def test_printer_error_is_reported_without_audit(self):
        with mock.patch("app.printing.escpos.send", side_effect=PrinterError("offline")):
            with self.assertRaises(PrintFailed):
                calibrate_labels(self.db, self.ctx, dict(ROLL))
        self.assertIsNone(self.db.one("SELECT 1 FROM audit_log WHERE action='print.label_calibrate'"))


class TestReceiptUntouched(Sent):
    def test_receipt_and_drawer_bytes_do_not_depend_on_label_variant(self):
        from app.printing import service as ps
        rs.save(self.db, self.ctx, {"receipt_target": "win:POS-80C"})
        ps.print_test(self.db, self.ctx, "win:POS-80C")
        before = list(self.sent)
        rs.save(self.db, self.ctx, {**ROLL, "label_variant": "all", "label_shift": 5})
        self.sent.clear()
        ps.print_test(self.db, self.ctx, "win:POS-80C")
        self.assertEqual(self.sent, before)
        self.assertFalse(any(b"GAPDETECT" in d or b"SET TEAR" in d for _, d in self.sent))


class TestCalibrateApi(APICase):
    def test_route_answers_browser_mode_and_refuses_bad_mode(self):
        self.http_login()
        r = self.c.post("/api/print/label-calibrate", json={}, headers=H)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "labels_in_browser")
        r = self.c.post("/api/print/label-calibrate", json={**ROLL, "mode": "fast"}, headers=H)
        self.assertEqual(r.status_code, 422)
        with mock.patch("app.printing.escpos.send") as send:
            r = self.c.post("/api/print/label-calibrate", json=ROLL, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(send.call_args[0][1], b"SIZE 38 mm,13 mm\r\nGAP 3 mm,0 mm\r\nGAPDETECT 104,24\r\n")
            r = self.c.post("/api/print/label-test", json={**ROLL, "label_variant": "per_label"}, headers=H)
            self.assertEqual(r.json()["variant"], "per_label")
