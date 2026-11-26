"""POST /api/system/keyboard-layout: the sale screen asks the cashier PC to type Arabic or English in the
window in front. A convenience: it never raises, never touches the database (it must answer while a sale is
being written), and only this PC's own screen may ask. Windows calls are replaced by a fake here."""
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

from app.core import keyboard_layout as kl
from tests.test_api import APICase, H

AR_EG, AR_SA, EN_US, EN_GB, FR = 0x04010C01, 0x04010401, 0x04090409, 0x08090809, 0x040C040C


class FakeUser32:
    """The five user32 calls, with a list of installed layouts and the layout of the window in front."""

    def __init__(self, installed, current, hwnd=77, post_ok=1):
        self.installed, self.current, self.hwnd, self.post_ok = installed, current, hwnd, post_ok
        self.posted = []

    def GetKeyboardLayoutList(self, n, buf):
        if buf is None:
            return len(self.installed)
        for i, h in enumerate(self.installed[:n]):
            buf[i] = h
        return min(n, len(self.installed))

    def GetForegroundWindow(self):
        return self.hwnd


    def PostMessageW(self, hwnd, msg, wparam, lparam):
        self.posted.append((hwnd, msg, wparam, lparam))
        return self.post_ok


class TestSwitch(unittest.TestCase):
    def go(self, lang, u):
        return kl.switch(lang, user32=u, platform="win32")

    def test_arabic_is_asked_with_the_installed_egyptian_layout(self):
        u = FakeUser32([EN_US, AR_EG], EN_US)
        self.assertEqual(self.go("ar", u), {"ok": True})
        self.assertEqual(u.posted, [(77, 0x0050, 0, AR_EG)])

    def test_english_from_arabic_and_the_usual_layout_first(self):
        u = FakeUser32([AR_EG, EN_GB, EN_US], AR_EG)
        self.assertEqual(self.go("en", u), {"ok": True})
        self.assertEqual(u.posted, [(77, 0x0050, 0, EN_US)])
        u = FakeUser32([AR_EG, AR_SA, EN_US], EN_US)
        self.go("ar", u)
        self.assertEqual(u.posted[0][3], AR_SA)

    def test_it_asks_even_when_the_window_seems_to_type_that_language_already(self):
        # the request is posted, not waited for: an earlier request for the other language may still be on
        # its way, so "already Arabic" read now can be stale (review, 26/11)
        u = FakeUser32([EN_US, AR_EG], AR_EG)
        self.assertEqual(self.go("ar", u), {"ok": True})
        self.assertEqual(u.posted, [(77, 0x0050, 0, AR_EG)])

    def test_a_language_that_is_not_installed_is_unsupported(self):
        u = FakeUser32([EN_US, FR], EN_US)
        self.assertEqual(self.go("ar", u), {"ok": False, "reason": "unsupported", "detail": "not_installed"})
        self.assertEqual(u.posted, [])
        self.assertEqual(self.go("ar", FakeUser32([], 0))["detail"], "not_installed")

    def test_a_layout_handle_with_the_high_bit_set_is_read_by_its_language(self):
        # 64-bit Windows hands some layouts back sign-extended (0xFFFFFFFFF0C10C01)
        odd = 0xFFFFFFFFF0C10C01
        u = FakeUser32([EN_US, odd], EN_US)
        self.assertEqual(self.go("ar", u), {"ok": True})
        self.assertEqual(u.posted[0][3], odd)            # handed back exactly as Windows gave it (review, 26/11)
        self.assertEqual(kl.pick("ar", [EN_US, odd, AR_EG]), AR_EG)             # the usual keyboard first
        self.assertEqual(kl.pick("en", [0xFFFFFFFFF0020409, AR_EG]), 0xFFFFFFFFF0020409)

    def test_no_window_refused_post_and_bad_language(self):
        self.assertEqual(self.go("ar", FakeUser32([AR_EG], EN_US, hwnd=0))["detail"], "no_window")
        self.assertEqual(self.go("ar", FakeUser32([AR_EG], EN_US, post_ok=0))["detail"], "refused")
        for bad in (None, "fr", 5, ["ar"], {"ar": 1}, ""):
            self.assertEqual(self.go(bad, FakeUser32([AR_EG], EN_US)), {"ok": False, "reason": "unsupported", "detail": "bad_lang"})

    def test_it_never_raises(self):
        class Broken:
            def __getattr__(self, name):
                raise OSError("user32 failed")
        self.assertEqual(self.go("ar", Broken()), {"ok": False, "reason": "unsupported", "detail": "error"})

    def test_off_windows_it_is_unsupported_without_loading_anything(self):
        with mock.patch.object(kl, "_user32", side_effect=AssertionError("loaded user32")):
            self.assertEqual(kl.switch("ar", platform="linux"), {"ok": False, "reason": "unsupported", "detail": "not_windows"})


class TestEndpoint(APICase):
    URL = "/api/system/keyboard-layout"

    def test_this_pc_without_a_session_gets_the_answer_of_the_switch(self):
        with mock.patch.object(kl, "switch", return_value={"ok": True}) as sw:
            r = self.c.post(self.URL, json={"lang": "ar"}, headers=H)
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))
        sw.assert_called_once_with("ar")
        r = self.c.post(self.URL, json={"lang": "en"}, headers=H)               # the real call, on Linux
        # 501: the sale screen (keyboardSwitcher in app.js) stops asking a PC that can never switch
        self.assertEqual((r.status_code, r.json()), (501, {"ok": False, "reason": "unsupported", "detail": "not_windows"}))
        with mock.patch.object(kl, "switch", return_value={"ok": False, "reason": "unsupported", "detail": "not_installed"}):
            self.assertEqual(self.c.post(self.URL, json={"lang": "ar"}, headers=H).status_code, 501)
        # a passing condition (no window in front, the window refused) is a 503: keyboardSwitcher reads only the
        # status, forgets what it asked and asks again on the next field (Codex on #485); a wrong language is a 400
        for detail, status in (("no_window", 503), ("refused", 503), ("error", 503), ("bad_lang", 400)):
            with mock.patch.object(kl, "switch", return_value={"ok": False, "reason": "unsupported", "detail": detail}):
                r = self.c.post(self.URL, json={"lang": "ar"}, headers=H)
            self.assertEqual((r.status_code, r.json()["detail"]), (status, detail))

    def test_another_device_is_refused_and_nothing_is_switched(self):
        phone = TestClient(self.app, client=("192.168.1.50", 50000))
        with mock.patch.object(kl, "switch", side_effect=AssertionError("switched for another device")):
            r = phone.post(self.URL, json={"lang": "ar"}, headers=H)
        self.assertEqual((r.status_code, r.json()), (501, {"ok": False, "reason": "unsupported", "detail": "not_local"}))

    def test_another_site_or_a_plain_form_is_refused(self):
        with mock.patch.object(kl, "switch", side_effect=AssertionError("switched for another site")):
            for headers in ({"Content-Type": "application/json"},                              # no marker header
                            {**H, "Origin": "http://evil.example"},
                            {**H, "Origin": "null"},
                            {**H, "Sec-Fetch-Site": "cross-site"},
                            {**H, "Sec-Fetch-Site": "same-site"},
                            {**H, "Sec-Fetch-Site": "same-origin"},                           # ... without an Origin
                            {**H, "Content-Type": "text/plain"}):
                r = self.c.post(self.URL, content=b'{"lang":"ar"}', headers=headers)
                self.assertEqual((r.status_code, r.json()),
                                 (403, {"ok": False, "reason": "unsupported", "detail": "not_this_screen"}), headers)

    def test_a_broken_body_or_a_broken_switch_still_answers(self):
        for body in (b"not json", b"[1,2]", b'"ar"', b'{"lang": 7}'):
            with mock.patch.object(kl.sys, "platform", "win32"), mock.patch.object(kl, "_user32", side_effect=OSError):
                r = self.c.post(self.URL, content=body, headers={**H, "Content-Type": "application/json"})
            self.assertEqual((r.status_code, r.json()["ok"], r.json()["reason"]),
                             (400, False, "unsupported"), body)
        with mock.patch.object(kl, "switch", side_effect=RuntimeError("boom")):
            r = self.c.post(self.URL, json={"lang": "ar"}, headers=H)
        self.assertEqual((r.status_code, r.json()), (503, {"ok": False, "reason": "unsupported", "detail": "error"}))
        self.assertEqual(self.c.get(self.URL).status_code, 405)

    def test_another_host_name_never_reaches_it(self):
        with mock.patch.object(kl, "switch", side_effect=AssertionError("switched for another host")):
            r = self.c.post(self.URL, json={"lang": "ar"}, headers={**H, "Host": "evil.example:8000"})
        self.assertEqual(r.status_code, 403)

    def test_it_never_touches_the_database_and_answers_while_a_write_is_held(self):
        with self.db.transaction():                                    # a sale being written, on this thread
            self.db.execute("UPDATE items SET min_stock=1 WHERE id=?", (self.pan,))
            with mock.patch.object(kl, "switch", return_value={"ok": True}), \
                    mock.patch.object(type(self.db), "conn", new_callable=mock.PropertyMock,
                                      side_effect=AssertionError("database used")):
                t = time.perf_counter()
                r = self.c.post(self.URL, json={"lang": "ar"}, headers={**H, "Idempotency-Key": "k-1"})
                took = (time.perf_counter() - t) * 1000
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))
        self.assertLess(took, 500)                 # measured 2-15 ms; the bound only catches a wait on a lock


if __name__ == "__main__":
    import unittest
    unittest.main()
