"""#97: shift-close report with a denomination table (الفئة | العدد | المبلغ) and the shortage (العجز) and
surplus (الزيادة) rows always shown, on the browser page and straight to the receipt printer."""
import unittest
from unittest import mock

from app.core import people
from app.core.errors import Forbidden
from app.printing import escpos
from app.printing import settings as rs
from app.printing import shift_report as sr
from app.printing.service import PrintFailed
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base
from tests.test_printing import NO_RAQM

PW = "Staff-Pass-2026"
PRINTER = "tcp:127.0.0.1:9100"


def rows(lines):
    """{label: value} for the two-column rows, and the denomination table rows."""
    two = {ln[1]: ln[2] for ln in lines if ln[0] in ("row", "total")}
    den = [ln[1:] for ln in lines if ln[0] == "den"]
    return two, den


class TestHelpers(unittest.TestCase):
    def test_money_is_formatted_from_integer_piastres(self):
        self.assertEqual([sr.egp(p) for p in (0, None, 5, 25, 50, 100, 123456, -400, 1000000099)],
                         ["0.00", "0.00", "0.05", "0.25", "0.50", "1.00", "1,234.56", "-4.00", "10,000,000.99"])

    def test_shortage_and_surplus_are_both_non_negative(self):
        self.assertEqual(sr.shortage_surplus(0), (0, 0))
        self.assertEqual(sr.shortage_surplus(None), (0, 0))
        self.assertEqual(sr.shortage_surplus(-425), (425, 0))
        self.assertEqual(sr.shortage_surplus(1050), (0, 1050))

    def test_denomination_rows_largest_first_counted_only(self):
        got = sr.denomination_rows({"25": 3, "20000": 2, "500": 0, "50": 1})
        self.assertEqual(got, [(20000, "200", 2, 40000), (50, "نصف", 1, 50), (25, "ربع", 3, 75)])
        self.assertEqual(sr.denomination_rows(None), [])
        self.assertEqual(sr.denomination_rows({}), [])


class ShiftBase(Base):
    def setUp(self):
        super().setUp()
        self.cd.open(self.ctx, 10000)                                     # opening 100.00

    def close(self, den, note=None):
        return self.cd.close(self.ctx, denominations=den, note=note)

    def lines(self, r):
        return sr.lines("صيدلية تجربة", r)


class TestReportLines(ShiftBase):
    def test_exact_match_prints_both_rows_as_zero(self):
        self.sell()                                                       # cash 40.00 → expected 140.00
        r = self.close({"10000": 1, "2000": 2})
        two, den = rows(self.lines(r))
        self.assertEqual(r["expected_cash"], 14000)
        self.assertEqual((two["المتوقع في الخزينة"], two["المعدود"], two["العجز"], two["الزيادة"]),
                         ("140.00", "140.00", "0.00", "0.00"))
        self.assertEqual(den, [("100", "1", "100.00"), ("20", "2", "40.00")])
        self.assertIn(("small", "✓ الخزينة مطابقة"), self.lines(r))

    def test_shortage(self):
        self.sell()
        r = self.close({"10000": 1, "2000": 1, "1000": 1, "500": 1, "25": 4}, note="فكة")     # 136.00
        two, _ = rows(self.lines(r))
        self.assertEqual(r["difference"], -400)
        self.assertEqual((two["المعدود"], two["العجز"], two["الزيادة"]), ("136.00", "4.00", "0.00"))

    def test_surplus(self):
        r = self.close({"10000": 1, "50": 1}, note="زيادة نص جنيه")                            # 100.50
        two, _ = rows(self.lines(r))
        self.assertEqual(r["difference"], 50)
        self.assertEqual((two["العجز"], two["الزيادة"]), ("0.00", "0.50"))

    def test_zero_counts_no_table_and_shortage_of_everything(self):
        r = self.close({}, note="الخزينة فاضية")
        lines = self.lines(r)
        two, den = rows(lines)
        self.assertEqual((den, [ln for ln in lines if ln[0] == "den_head"]), ([], []))
        self.assertEqual((two["المعدود"], two["العجز"], two["الزيادة"]), ("0.00", "100.00", "0.00"))

    def test_only_coins(self):
        self.cd.close(self.ctx, denominations={"10000": 1})
        self.cd.open(self.ctx, 0)
        self.sell(units=1)                                                # 20.00 cash
        r = self.close({"100": 19, "50": 1, "25": 2})                    # 19 + 0.50 + 0.50 = 20.00
        two, den = rows(self.lines(r))
        self.assertEqual(den, [("1", "19", "19.00"), ("نصف", "1", "0.50"), ("ربع", "2", "0.50")])
        self.assertEqual((two["المعدود"], two["العجز"], two["الزيادة"]), ("20.00", "0.00", "0.00"))
        self.assertEqual(sum(a for *_, a in sr.denomination_rows(r["counted_detail"])), r["counted_cash"])

    def test_returns_and_drawer_payouts_use_the_saved_expected_cash(self):
        a = self.sell()                                                   # +40.00
        self.s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 1}, refund_method="cash", reason="مرتجع")  # −20.00
        self.cd.movement(self.ctx, 1500, "payout", "مواصلات")              # −15.00
        expected = self.cd._expected(self.cd.summary(self.ctx)["id"])
        self.assertEqual(expected, 10000 + 4000 - 2000 - 1500)
        r = self.close({"10000": 1, "500": 1})                            # 105.00 = exact
        two, _ = rows(self.lines(r))
        self.assertEqual(r["expected_cash"], expected)
        self.assertEqual((two["مبيعات نقدي"], two["مرتجع نقدي"], two["مصروفات من الخزينة"]), ("40.00", "-20.00", "-15.00"))
        self.assertEqual((two["المتوقع في الخزينة"], two["العجز"], two["الزيادة"]), ("105.00", "0.00", "0.00"))
        self.assertEqual(two["مرتجعات (1)"], "20.00")

    def test_card_and_credit_refunds_are_listed_but_not_in_the_drawer(self):
        a = self.sell(units=4)                                            # +80.00 cash
        self.s.return_items(self.ctx, a["id"], {a["lines"][0]["id"]: 1}, refund_method="card", reason="مرتجع")
        r = self.close({"10000": 1, "5000": 1, "2000": 1, "1000": 1})      # 180.00: card refund leaves the drawer alone
        two, _ = rows(self.lines(r))
        self.assertEqual(r["card_refunds"], 2000)
        self.assertEqual(two["منها مرتجع على البطاقة"], "20.00")
        self.assertNotIn("منها مرتجع على حساب العميل", two)
        self.assertEqual((two["المتوقع في الخزينة"], two["العجز"], two["الزيادة"]), ("180.00", "0.00", "0.00"))

    def test_open_shift_has_no_result_rows(self):
        r = self.cd.report(self.ctx, self.cd.summary(self.ctx)["id"])
        two, _ = rows(self.lines(r))
        self.assertNotIn("العجز", two)
        self.assertEqual(two["المتوقع في الخزينة"], "100.00")


@unittest.skipUnless(escpos.shaping_available(), NO_RAQM)
class TestDirectPrint(ShiftBase):
    def setUp(self):
        super().setUp()
        self.sell()
        self.r = self.close({"10000": 1, "2000": 1}, note="عجز 20")
        rs.save(self.db, self.ctx, {"receipt_target": PRINTER})
        self.sent = []

    def audits(self):
        return [a["after_json"] for a in self.db.all("SELECT after_json FROM audit_log WHERE action='print.shift_report'")]

    def test_goes_to_the_receipt_printer_without_drawer_pulse(self):
        with mock.patch.object(escpos, "send", lambda t, d: self.sent.append((t, d))):
            self.assertEqual(sr.print_report(self.db, self.ctx, self.r["id"]), {"ok": True})
        (target, data), = self.sent
        self.assertEqual(target, PRINTER)
        self.assertTrue(data.startswith(escpos.INIT) and data.endswith(escpos.CUT))
        self.assertNotIn(escpos.DRAWER_KICK, data)
        self.assertEqual(len(self.audits()), 1)
        self.assertIn('"sent"', self.audits()[0])

    def test_image_fits_80_and_58_mm(self):
        for width in (escpos.DOTS_80MM, escpos.DOTS_58MM):
            img = sr.render("صيدلية تجربة", self.r, width)
            self.assertEqual(img.width, width)
            self.assertGreater(img.height, 400)

    def test_browser_printing_chosen_is_no_printer(self):
        rs.save(self.db, self.ctx, {"receipt_target": "browser"})
        with self.assertRaises(PrintFailed) as e:
            sr.print_report(self.db, self.ctx, self.r["id"])
        self.assertEqual(e.exception.code, "no_printer")
        self.assertEqual(self.audits(), [])

    def test_printer_down_is_reported_and_audited(self):
        def down(t, d):
            raise escpos.PrinterError("تعذر الوصول للطابعة")
        with mock.patch.object(escpos, "send", down), self.assertRaises(PrintFailed) as e:
            sr.print_report(self.db, self.ctx, self.r["id"])
        self.assertIn("المتصفح", e.exception.message)
        self.assertIn("not_printed", self.audits()[0])

    def test_someone_elses_shift_is_refused(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        with mock.patch.object(escpos, "send", lambda t, d: self.sent.append(d)), self.assertRaises(Forbidden):
            sr.print_report(self.db, a, self.r["id"])
        self.assertEqual(self.sent, [])


class TestPageAndAPI(APICase):
    def test_page_has_the_table_and_both_rows(self):
        self.http_login()
        self.c.post("/api/cash/open", json={"opening_cash": 5000}, headers=H)
        r = self.c.post("/api/cash/close", json={"denominations": {"2000": 2, "25": 2}, "note": "عجز"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        page = self.c.get(f"/shift-report/{r.json()['id']}").text
        self.assertIn("<th>الفئة</th><th>العدد</th><th>المبلغ</th>", page)
        self.assertIn("<tr><td>20</td><td>2</td><td>40.00</td></tr>", page)
        self.assertIn("<tr><td>ربع</td><td>2</td><td>0.50</td></tr>", page)
        self.assertIn("<tr><td>العجز</td><td>9.50</td></tr>", page)
        self.assertIn("<tr><td>الزيادة</td><td>0.00</td></tr>", page)
        self.assertNotIn("receipt-58.css", page)
        rs.save(self.db, self.ctx, {"paper": 58})
        page = self.c.get(f"/shift-report/{r.json()['id']}").text
        self.assertIn("receipt-58.css", page)
        self.assertIn('<body class="w58">', page)

    def test_print_endpoint_falls_back_when_browser_printing(self):
        self.http_login()
        self.c.post("/api/cash/open", json={"opening_cash": 0}, headers=H)
        sid = self.c.post("/api/cash/close", json={"denominations": {}}, headers=H).json()["id"]
        rs.save(self.db, self.ctx, {"receipt_target": "browser"})
        r = self.c.post(f"/api/print/shift-report/{sid}", json={}, headers=H)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (502, "no_printer"))

    @unittest.skipUnless(escpos.shaping_available(), NO_RAQM)
    def test_print_endpoint_sends(self):
        self.http_login()
        self.c.post("/api/cash/open", json={"opening_cash": 0}, headers=H)
        sid = self.c.post("/api/cash/close", json={"denominations": {}}, headers=H).json()["id"]
        rs.save(self.db, self.ctx, {"receipt_target": PRINTER})
        sent = []
        with mock.patch.object(escpos, "send", lambda t, d: sent.append(t)):
            r = self.c.post(f"/api/print/shift-report/{sid}", json={}, headers=H)
        self.assertEqual((r.status_code, sent), (200, [PRINTER]))


class TestScreenSource(unittest.TestCase):
    def test_close_prints_direct_then_falls_back_to_the_page(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        body = js[js.index("async function printShiftReport(id)"):js.index("// ---------------------------------"
                                                                            "--------------------------------- end #97")]
        self.assertIn("/api/print/shift-report/${id}", body)
        self.assertIn('err.code !== "no_printer"', body)
        self.assertIn("printInFrame(`/shift-report/${id}`)", body)
        self.assertIn("printShiftReport(r.id);", js)
