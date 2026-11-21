"""#98: a Windows print job for a printer that is switched off waits in the queue, Windows says the printer
is ready, and the receipt and its drawer pulse fire by themselves when the printer comes back. The job must
leave the queue (or be printing) within a few seconds, else it is cancelled and the screen warns."""
import sys
import unittest
from unittest import mock

from app.printing import escpos
from app.printing import service as printing, settings as rs
from tests.test_checkout_devices import KICK, CheckoutCase

REAL_SEND = escpos.send          # CheckoutCase replaces escpos.send in setUp; these tests need the real one


class FakeSpooler:
    """Enough of pywin32's win32print for _send_windows. mode: takes (the job leaves after `polls` looks),
    keeps (stays, status 0 as on the pharmacy PC), printing, offline (printing + offline), enum_fails,
    cancel_fails."""
    JOB_CONTROL_DELETE = 5

    def __init__(self, mode="takes", polls=1):
        self.mode, self.polls = mode, polls
        self.jobs, self.data, self.deleted, self.looks, self.closed = {}, [], [], 0, 0

    def OpenPrinter(self, name):
        self.name = name
        return "h"

    def StartDocPrinter(self, h, level, info):
        self.doc = info
        self.jobs[7] = {"printing": 0x10, "offline": 0x30, "printed": 0x80, "complete": 0x1000,
                        "printed_offline": 0xA0}.get(self.mode, 0)
        return 7

    def StartPagePrinter(self, h): pass
    def EndPagePrinter(self, h): pass
    def EndDocPrinter(self, h): pass

    def WritePrinter(self, h, data):
        self.data.append(data)

    def ClosePrinter(self, h):
        self.closed += 1

    def EnumJobs(self, h, first, n, level):
        if self.mode == "enum_fails":
            raise RuntimeError("access denied")
        self.looks += 1
        if self.mode == "takes" and self.looks > self.polls:
            self.jobs.pop(7, None)
        return [{"JobId": j, "Status": st} for j, st in self.jobs.items()]

    def SetJob(self, h, job, level, info, command):
        if self.mode == "cancel_fails":
            raise RuntimeError("denied")
        if command == self.JOB_CONTROL_DELETE:
            self.deleted.append(job)
            self.jobs.pop(job, None)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def send(fake, wait=2.0, data=b"x"):
    clock = Clock()
    with mock.patch.dict(sys.modules, {"win32print": fake}):
        escpos._send_windows("POS-80C", data, wait=wait, clock=clock, sleep=clock.sleep)
    return clock


class TestSendWindows(unittest.TestCase):
    def test_job_taken_by_the_printer(self):
        f = FakeSpooler("takes", polls=3)
        c = send(f, data=b"abc")
        self.assertEqual((f.data, f.deleted, f.closed, f.doc[2]), ([b"abc"], [], 1, "RAW"))
        self.assertLess(c.t, 2.0)

    def test_printing_without_error_counts_as_taken(self):
        f = FakeSpooler("printing")              # a long label run still printing when the wait ends
        send(f)
        self.assertEqual(f.deleted, [])

    def test_finished_job_kept_in_queue_counts_as_taken(self):
        # Codex review on #111: with "Keep printed documents" on, a finished receipt stays queued as PRINTED
        # or COMPLETE. It was printed (and the drawer opened), so it must not be cancelled or reported failed.
        for mode in ("printed", "complete"):
            f = FakeSpooler(mode)
            c = send(f, wait=2.0)
            self.assertEqual(f.deleted, [], mode)
            self.assertLess(c.t, 2.0, mode)

    def test_printed_flag_with_an_error_is_not_trusted(self):
        f = FakeSpooler("printed_offline")
        with self.assertRaises(escpos.PrinterError):
            send(f, wait=2.0)
        self.assertEqual(f.deleted, [7])

    def test_printer_off_job_is_cancelled_and_reported(self):
        for mode in ("keeps", "offline"):
            f = FakeSpooler(mode)
            with self.assertRaises(escpos.PrinterError, msg=mode) as cm:
                send(f, wait=2.0)
            self.assertEqual(f.deleted, [7], mode)
            self.assertEqual(f.jobs, {}, mode)                      # nothing left to fire when the printer comes back
            self.assertIn("لا تستجيب", str(cm.exception))
            self.assertEqual(f.closed, 1)

    def test_cancel_refused_says_so(self):
        f = FakeSpooler("cancel_fails")
        with self.assertRaises(escpos.PrinterError) as cm:
            send(f, wait=0.5)
        self.assertIn("امسحه من قائمة الطباعة", str(cm.exception))

    def test_queue_not_listable_keeps_the_old_behaviour(self):
        f = FakeSpooler("enum_fails")
        send(f)
        self.assertEqual((f.data, f.deleted), ([b"x"], []))

    def test_send_routes_win_targets(self):
        f = FakeSpooler("keeps")
        with mock.patch.dict(sys.modules, {"win32print": f}), mock.patch.object(escpos, "SPOOL_WAIT", 0.0):
            with self.assertRaises(escpos.PrinterError):
                escpos.send("win:POS-80C", b"x")
        self.assertEqual((f.name, f.deleted), ("POS-80C", [7]))


class TestCheckoutPrinterOff(CheckoutCase):
    """The #18 P7 steps: printer off, F4 receipt + cash."""

    def setUp(self):
        super().setUp()
        self.spool = FakeSpooler("keeps")
        for p in (mock.patch.object(escpos, "send", REAL_SEND), mock.patch.dict(sys.modules, {"win32print": self.spool}),
                  mock.patch.object(escpos, "SPOOL_WAIT", 0.0)):
            p.start()
            self.addCleanup(p.stop)
        rs.save(self.db, self.ctx, {"receipt_target": "win:POS-80C", "drawer_timing": "with"})

    def test_sale_saved_warning_shown_nothing_left_in_the_queue(self):
        sid = self.draft()
        out = self.complete(sid, [{"method": "cash", "amount": 3000}], receipt=True)
        dv = out["devices"]
        self.assertEqual((dv["receipt"], dv["drawer"]), ("failed", "failed"))     # the screen warns for both
        self.assertIn("لا تستجيب", dv["message"])
        self.assertEqual(self.spool.deleted, [7])
        self.assertEqual(self.spool.jobs, {})                                         # no receipt, no drawer later
        self.assertIn(KICK, self.spool.data[0])                                       # the pulse was in the cancelled job
        self.assertEqual(self.audits("print.receipt", "not_printed"), 1)
        self.assertEqual(self.audits("print.receipt", "sent"), 0)
        self.assertEqual(self.audits("cash.drawer_auto", "failed"), 1)
        self.assertEqual(self.audits("cash.drawer_auto", "sent"), 0)
        self.assertEqual(self.db.one("SELECT status FROM sales WHERE id=?", (sid,))["status"], "completed")

    def test_reprint_after_power_on_prints_once_without_drawer(self):
        sid = self.draft()
        self.complete(sid, [{"method": "cash", "amount": 3000}], receipt=True)
        self.spool.mode = "takes"                                                     # printer switched on
        r = self.c.post(f"/api/print/receipt/{sid}", json={}, headers={"X-Requested-With": "pharm"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(KICK, self.spool.data[-1])
        self.assertEqual(self.audits("print.receipt", "sent"), 1)

    def test_drawer_only_sale_and_manual_open_are_cancelled_too(self):
        sid = self.draft()
        out = self.complete(sid, [{"method": "cash", "amount": 3000}])               # no receipt: the pulse alone
        self.assertEqual(out["devices"]["drawer"], "failed")
        with self.assertRaises(printing.PrintFailed):
            printing.open_drawer(self.db, self.ctx, "change")
        self.assertEqual(self.spool.deleted, [7, 7])
        self.assertEqual(self.audits("cash.drawer_open", "failed"), 1)


    # #98 re-read before the pharmacy PC test (13/11): the cases the first tests did not cover

    def test_every_drawer_timing_and_two_copies_leave_nothing_queued(self):
        for i, timing in enumerate(("before", "with", "after")):
            rs.save(self.db, self.ctx, {"drawer_timing": timing, "copies": 2})
            self.spool.deleted.clear()
            sid = self.draft()
            out = self.complete(sid, [{"method": "cash", "amount": 3000}], receipt=True, rid=f"t{i}")
            self.assertEqual((out["devices"]["receipt"], out["devices"]["drawer"]), ("failed", "failed"), timing)
            self.assertEqual(self.spool.deleted, [7], timing)                 # one job, cancelled
            self.assertEqual(self.spool.jobs, {}, timing)                     # nothing fires when it comes back
            self.assertEqual(self.spool.data[-1].count(KICK), 1, timing)      # the one pulse was in that job
        self.assertEqual(self.audits("cash.drawer_auto", "sent"), 0)
        self.assertEqual(self.audits("print.receipt", "not_printed"), 3)

    def test_card_sale_warns_for_the_receipt_only(self):
        out = self.complete(self.draft(), [{"method": "card", "amount": 3000, "channel_id": self.card}], receipt=True)
        self.assertEqual((out["devices"]["receipt"], out["devices"]["drawer"]), ("failed", "none"))
        self.assertNotIn(KICK, self.spool.data[0])
        self.assertEqual((self.spool.deleted, self.spool.jobs), ([7], {}))
        self.assertEqual(self.audits("cash.drawer_auto"), 0)

    def test_delivery_sale_warns_and_never_opens_the_drawer(self):
        out = self.complete(self.draft(delivery=True), [{"method": "cash", "amount": 3000}])
        self.assertEqual((out["devices"]["receipt"], out["devices"]["drawer"]), ("failed", "none"))
        self.assertNotIn(KICK, self.spool.data[0])
        self.assertEqual((self.spool.deleted, self.spool.jobs), ([7], {}))

    def test_a_retry_of_the_failed_sale_sends_nothing_again(self):
        sid = self.draft()
        self.complete(sid, [{"method": "cash", "amount": 3000}], receipt=True)
        self.spool.mode = "takes"                                             # printer back on, cashier presses F9 again
        again = self.complete(sid, [{"method": "cash", "amount": 3000}], receipt=True)
        self.assertEqual(again["status"], "completed")
        self.assertEqual((len(self.spool.data), self.spool.deleted), (1, [7]))  # no second job, no late pulse
        self.assertEqual(self.audits("cash.drawer_auto", "sent"), 0)

    def test_next_sale_after_power_on_prints_once_with_one_pulse(self):
        self.complete(self.draft(), [{"method": "cash", "amount": 3000}], receipt=True)
        self.spool.mode = "takes"
        out = self.complete(self.draft(), [{"method": "cash", "amount": 3000}], receipt=True, rid="r2")
        self.assertEqual((out["devices"]["receipt"], out["devices"]["drawer"]), ("sent", "sent"))
        self.assertEqual(len(self.spool.data), 2)                             # the cancelled job is not resent
        self.assertEqual(self.spool.data[-1].count(KICK), 1)
        self.assertEqual(self.audits("cash.drawer_auto", "sent"), 1)
