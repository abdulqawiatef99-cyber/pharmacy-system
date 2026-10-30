"""#81: receipt only when asked (or by the amount rule, decided on the screen), the cash drawer opens on any
cash part of a committed store sale with or without a receipt, once per sale; delivery prints one receipt and
never opens the drawer; reprints never open it; manual opening needs cash.manage and a reason and is
audited with its shift and outcome; the shift report shows manual and automatic commands apart."""
from datetime import datetime
from decimal import Decimal
from unittest import mock

from PIL import Image

from app.catalog import service as cat
from app.core import accounts
from app.core.errors import Forbidden, ValidationFailed
from app.inventory import service as inv
from app.printing import escpos, settings as rs
from app.printing import service as printing
from app.sales.cash import CashDrawer
from tests.helpers import DBTestCase
from tests.test_api import APICase, H

KICK = escpos.DRAWER_KICK
PRINTER = "tcp:127.0.0.1:9100"


class TestSettingsAndJob(DBTestCase):
    def test_defaults_keep_existing_values_and_validate(self):
        c = rs.get(self.db, self.ctx.org_id)
        self.assertEqual((c["auto_receipt"], c["auto_receipt_min"], c["drawer_on_cash"], c["drawer_timing"],
                          c["time_format"], c["header"], c["tax_no"], c["cr_no"], c["copies"]),
                         (False, 0, True, "after", "12", "", "", "", 1))
        rs.save(self.db, self.ctx, {"footer": "شكرًا", "title": "صيدلية تجربة"})
        c = rs.save(self.db, self.ctx, {"auto_receipt": True, "auto_receipt_min": 2000, "drawer_timing": "before"})
        self.assertEqual((c["footer"], c["title"], c["auto_receipt_min"]), ("شكرًا", "صيدلية تجربة", 2000))
        for bad in ({"auto_receipt_min": -1}, {"auto_receipt_min": 20.5}, {"auto_receipt_min": True},
                    {"auto_receipt_min": "20"}, {"drawer_timing": "later"}, {"time_format": "12h"}, {"copies": 0}):
            with self.assertRaises(ValidationFailed, msg=bad):
                rs.save(self.db, self.ctx, bad)
        self.assertEqual(rs.save(self.db, self.ctx, {"auto_receipt_min": 0})["auto_receipt_min"], 0)

    def test_time_format(self):
        f = lambda h, m, t="12": rs.fmt_when(datetime(2026, 10, 30, h, m), t)  # noqa: E731
        self.assertEqual(f(22, 6), "2026-10-30 10:06 م")
        self.assertEqual(f(0, 5), "2026-10-30 12:05 ص")
        self.assertEqual(f(12, 0), "2026-10-30 12:00 م")
        self.assertEqual(f(11, 59), "2026-10-30 11:59 ص")
        self.assertEqual(f(22, 6, "24"), "2026-10-30 22:06")

    def test_drawer_timing_and_copies(self):
        img = Image.new("L", (16, 8), 255)
        body = escpos.raster_bytes(img)
        before = escpos.receipt_job(img, True, "before")
        self.assertTrue(before.startswith(escpos.INIT + KICK + body))
        with_ = escpos.receipt_job(img, True, "with")
        self.assertTrue(with_.startswith(escpos.INIT + body + KICK) and with_.endswith(escpos.CUT))
        after = escpos.receipt_job(img, True, "after", copies=2)
        self.assertTrue(after.endswith(escpos.CUT + KICK))
        self.assertEqual(after.count(escpos.CUT), 2)
        for job in (before, with_, after, escpos.receipt_job(img, True, "with", 3)):
            self.assertEqual(job.count(KICK), 1)
        self.assertNotIn(KICK, escpos.receipt_job(img, False, "before", 2))

    def test_receipt_lines_show_new_optional_fields_only_when_set(self):
        sale = {"sale_no": 7, "lines": [], "discount_total": 0, "total": 100}
        plain = escpos.receipt_lines("ص", sale, "w", rs.get(self.db, self.ctx.org_id))
        self.assertFalse([x for x in plain if "السجل" in x[1] or "الضريبي" in x[1]])
        cfg = {**rs.get(self.db, self.ctx.org_id), "header": "يوجد خدمة توصيل", "tax_no": "123", "cr_no": "456"}
        lines = escpos.receipt_lines("ص", sale, "w", cfg)
        self.assertEqual(lines[0], ("small", "يوجد خدمة توصيل", ""))
        self.assertIn(("small", "رقم التسجيل الضريبي: 123", ""), lines)
        self.assertIn(("small", "السجل التجاري: 456", ""), lines)


class CheckoutCase(APICase):
    def setUp(self):
        super().setUp()
        it = cat.create_item(self.db, self.ctx, {"name_en": "Brufen 400mg 20 tab", "public_price": 3000})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=50, movement_type="opening", unit_cost=Decimal("10"))
        self.item = it
        CashDrawer(self.db).open(self.ctx, 0)
        self.http_login()
        rs.save(self.db, self.ctx, {"receipt_target": PRINTER})
        self.cust = self.c.post("/api/customers", json={"name": "عميل تجربة", "credit_limit": 100000}, headers=H).json()["id"]
        chans = self.c.post("/api/payment-channels", json={"kind": "card", "name": "جهاز تجربة"}, headers=H).json()["channels"]
        self.card = next(c["id"] for c in chans if c["kind"] == "card")
        self.sent = []
        p1 = mock.patch.object(escpos, "send", side_effect=lambda dest, data, timeout=5.0: self.sent.append((dest, data)))
        p2 = mock.patch.object(escpos, "render_receipt", return_value=Image.new("L", (16, 8), 255))
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)

    def draft(self, delivery=False):
        sid = self.c.post("/api/sales", json={}, headers=H).json()["id"]
        self.c.post(f"/api/sales/{sid}/lines", json={"item_id": self.item, "packs": 0, "units": 1}, headers=H)
        if delivery:
            self.c.patch(f"/api/sales/{sid}", json={"kind": "delivery", "delivery": {"phone": "0100"}}, headers=H)
        return sid

    def complete(self, sid, payments, receipt=False, rid="r1", **extra):
        f = self.c.post(f"/api/sales/{sid}/check", json={}, headers=H).json()["findings"]
        body = {"payments": payments, "receipt": receipt, "request_id": rid,
                "safety_decisions": {str(i): "x" for i in range(len(f))}, **extra}
        r = self.c.post(f"/api/sales/{sid}/complete", json=body, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def audits(self, action, outcome=None):
        q = "SELECT count(*) FROM audit_log WHERE action=?" + (" AND json_extract(after_json,'$.outcome')=?" if outcome else "")
        return self.db.one(q, (action, outcome) if outcome else (action,))[0]

    def kicks(self):
        return sum(d.count(KICK) for _, d in self.sent)


class TestCheckoutDrawer(CheckoutCase):
    def test_cash_sale_without_receipt_opens_the_drawer_once_even_on_retry(self):
        sid = self.draft()
        out = self.complete(sid, [{"method": "cash", "amount": 3000}])
        self.assertEqual(out["devices"], {"receipt": "none", "drawer": "sent", "message": None})
        self.assertEqual(self.sent, [(PRINTER, escpos.INIT + KICK)])         # a standalone pulse: no feed, no cut
        again = self.complete(sid, [{"method": "cash", "amount": 3000}])     # lost response, same request_id
        self.assertTrue(again["replayed"])
        self.assertNotIn("devices", again)
        self.assertEqual(len(self.sent), 1)
        r = self.c.post(f"/api/sales/{sid}/complete", json={"payments": [{"method": "cash", "amount": 3000}],
                                                               "request_id": "other"}, headers=H)
        self.assertEqual(r.status_code, 409)                                  # a new click cannot complete it again
        self.assertEqual((len(self.sent), self.audits("cash.drawer_auto"), self.audits("cash.drawer_open")), (1, 1, 0))

    def test_card_and_credit_never_open_it_mixed_does(self):
        self.complete(self.draft(), [{"method": "card", "amount": 3000, "channel_id": self.card}])
        sid = self.draft()
        self.c.patch(f"/api/sales/{sid}", json={"customer_id": self.cust}, headers=H)
        self.complete(sid, [{"method": "credit", "amount": 3000}], rid="r2")
        self.assertEqual(self.sent, [])
        out = self.complete(self.draft(), [{"method": "cash", "amount": 1000}, {"method": "card", "amount": 2000, "channel_id": self.card}], rid="r3")
        self.assertEqual(out["devices"]["drawer"], "sent")
        self.assertEqual(self.kicks(), 1)

    def test_setting_off_means_no_pulse(self):
        rs.save(self.db, self.ctx, {"drawer_on_cash": False})
        out = self.complete(self.draft(), [{"method": "cash", "amount": 3000}])
        self.assertEqual((out["devices"]["drawer"], self.sent), ("none", []))

    def test_receipt_and_drawer_in_one_job_with_the_chosen_timing(self):
        rs.save(self.db, self.ctx, {"drawer_timing": "before"})
        out = self.complete(self.draft(), [{"method": "cash", "amount": 3000}], receipt=True)
        self.assertEqual((out["devices"]["receipt"], out["devices"]["drawer"]), ("sent", "sent"))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][1].startswith(escpos.INIT + KICK))
        self.assertEqual((self.audits("print.receipt"), self.audits("cash.drawer_auto", "sent")), (1, 1))

    def test_reprint_never_opens_the_drawer(self):
        sid = self.draft()
        self.complete(sid, [{"method": "cash", "amount": 3000}])
        self.sent.clear()
        for _ in range(2):                                                   # intentional reprints are allowed
            r = self.c.post(f"/api/print/receipt/{sid}", json={"open_drawer": True}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((len(self.sent), self.kicks()), (2, 0))

    def test_printer_failure_keeps_the_sale_once_and_says_so(self):
        with mock.patch.object(escpos, "send", side_effect=escpos.PrinterError("الطابعة مغلقة")):
            sid = self.draft()
            out = self.complete(sid, [{"method": "cash", "amount": 3000}], receipt=True)
        self.assertEqual((out["status"], out["devices"]["receipt"], out["devices"]["drawer"]), ("completed", "failed", "failed"))
        self.assertIn("الطابعة مغلقة", out["devices"]["message"])
        self.assertEqual(self.db.one("SELECT count(*) FROM sales WHERE status='completed'")[0], 1)
        self.assertEqual((self.audits("cash.drawer_auto", "sent"), self.audits("cash.drawer_auto", "failed")), (0, 1))
        r = self.c.post(f"/api/print/receipt/{sid}", json={}, headers=H)     # the retry button
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.kicks(), 0)

    def test_unexpected_device_error_never_fails_the_sale(self):
        with mock.patch.object(printing, "print_receipt", side_effect=RuntimeError("boom")):
            out = self.complete(self.draft(), [{"method": "cash", "amount": 3000}], receipt=True)
        self.assertEqual((out["status"], out["devices"]["receipt"]), ("completed", "failed"))

    def test_browser_printing_cannot_open_the_drawer_and_says_so(self):
        rs.save(self.db, self.ctx, {"receipt_target": "browser"})
        out = self.complete(self.draft(), [{"method": "cash", "amount": 3000}], receipt=True)
        self.assertEqual(out["devices"], {"receipt": "browser", "drawer": "unsupported", "message": None})
        self.assertEqual((self.sent, self.audits("cash.drawer_auto")), ([], 0))


class TestDeliveryReceipt(CheckoutCase):
    def test_one_receipt_on_completion_no_drawer_no_repeat(self):
        sid = self.draft(delivery=True)
        self.c.patch(f"/api/sales/{sid}", json={"delivery": {"address": "شارع تجربة"}}, headers=H)   # edits print nothing
        self.assertEqual(self.sent, [])
        out = self.complete(sid, [{"method": "cash", "amount": 3000}])
        self.assertEqual((out["devices"]["receipt"], out["devices"]["drawer"]), ("sent", "none"))
        self.complete(sid, [{"method": "cash", "amount": 3000}])            # retry / refresh
        self.assertEqual((len(self.sent), self.kicks()), (1, 0))
        self.c.post(f"/api/print/receipt/{sid}", json={}, headers=H)         # an intentional reprint
        self.assertEqual((len(self.sent), self.kicks()), (2, 0))


class TestManualDrawer(CheckoutCase):
    def test_reasons_permission_audit_and_shift_report(self):
        accounts.add_user(self.db, self.ctx, username="asst", full_name="مساعد", password="Assist-Pass-2026",
                          role_code="assistant")
        with self.assertRaises(Forbidden):
            printing.open_drawer(self.db, self.login("asst", "Assist-Pass-2026"), "change")
        with self.assertRaises(ValidationFailed):
            printing.open_drawer(self.db, self.ctx, "other", note="")
        r = self.c.post("/api/print/drawer", json={"reason": "change"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.post("/api/print/drawer", json={"reason": "other", "note": "تبديل ورقة"}, headers=H).status_code, 200)
        with mock.patch.object(escpos, "send", side_effect=escpos.PrinterError("off")):
            r = self.c.post("/api/print/drawer", json={"reason": "cash_check"}, headers=H)
        self.assertEqual(r.status_code, 502)                                 # never reported as opened
        self.complete(self.draft(), [{"method": "cash", "amount": 3000}])   # one automatic pulse
        shift = CashDrawer(self.db).summary(self.ctx)
        rep = CashDrawer(self.db).report(self.ctx, shift.get("shift_id") or shift.get("id"))
        self.assertEqual((rep["drawer_manual_count"], rep["drawer_auto_count"]), (3, 1))
        self.assertEqual([(m["reason"], m["note"], m["outcome"]) for m in rep["drawer_manual"]],
                         [("فكة", "", "sent"), ("أخرى", "تبديل ورقة", "sent"), ("مراجعة النقدية", "", "failed")])
        self.assertTrue(all(m["by"] for m in rep["drawer_manual"]))

    def test_browser_mode_manual_open_is_a_clear_failure(self):
        rs.save(self.db, self.ctx, {"receipt_target": "browser"})
        r = self.c.post("/api/print/drawer", json={"reason": "change"}, headers=H)
        self.assertEqual(r.status_code, 502)
        self.assertIn("المتصفح", r.json()["error"]["message"])
        self.assertEqual(self.sent, [])


class TestShiftReportPage(CheckoutCase):
    def test_page_lists_manual_commands_apart_from_automatic(self):
        self.c.post("/api/print/drawer", json={"reason": "other", "note": "فكة لعميل"}, headers=H)
        self.complete(self.draft(), [{"method": "cash", "amount": 3000}])
        shift = self.db.one("SELECT id FROM cash_shifts WHERE status='open'")["id"]
        page = self.c.get(f"/shift-report/{shift}", headers=H).text
        self.assertIn("أوامر فتح الدرج مع البيع النقدي</td><td>1", page)
        self.assertIn("أوامر فتح الدرج يدويًا</td><td>1", page)
        self.assertIn("أخرى : فكة لعميل", page)
