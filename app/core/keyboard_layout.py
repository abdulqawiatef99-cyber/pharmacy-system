"""Switch the Windows input language of the window in front (the cashier's browser) to Arabic or English.

The sale screen asks for it when a field that wants one language gets the focus, so the cashier does not
type a customer's name in Latin letters or a code in Arabic ones. It is a convenience: it never raises,
never waits for anything, touches no data, and off Windows (or without that language installed) it simply
answers "unsupported".

How: WM_INPUTLANGCHANGEREQUEST posted (not sent: no waiting for the other program) to GetForegroundWindow,
with the installed keyboard layout of the asked language. The layout is found by its language, not by one
fixed number: an Egyptian PC lists Arabic as 0x0C01 (Arabic - Egypt) with the Arabic (101) keyboard, not as
0x0401, and English may be 0x0409 or 0x0809.
"""
from __future__ import annotations

import sys

WM_INPUTLANGCHANGEREQUEST = 0x0050
PRIMARY = {"ar": 0x01, "en": 0x09}          # primary language id = the low 10 bits of the language id
# preferred full ids first when a PC has two layouts of one language (Arabic 101, US English)
PREFERRED = {"ar": (0x04010401, 0x04010C01), "en": (0x04090409,)}


def _user32():
    """user32 with the argument types set (an HKL and an HWND are pointer-sized: the defaults cut them)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    u.GetKeyboardLayoutList.restype = ctypes.c_int
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p]
    u.PostMessageW.restype = wintypes.BOOL
    return u


def _installed(u) -> list[int]:
    import ctypes
    n = u.GetKeyboardLayoutList(0, None)
    if n <= 0:
        return []
    buf = (ctypes.c_void_p * n)()
    got = u.GetKeyboardLayoutList(n, buf)
    return [int(h or 0) for h in buf[:got]]


def pick(lang: str, installed: list[int]) -> int | None:
    """The installed layout to ask for: one of this language (its low word is the language id), the usual
    one first. None when the language is not installed. The handle is returned exactly as Windows gave it:
    on 64-bit Windows some are sign-extended (0xFFFFFFFFF0C10C01), and a cut one may not be recognised."""
    mine = [h for h in installed if h & 0x3FF == PRIMARY[lang] and h & 0xFFFF]
    for usual in PREFERRED[lang]:
        for h in mine:
            if h & 0xFFFFFFFF == usual:
                return h
    return mine[0] if mine else None


def _no(detail: str) -> dict:
    return {"ok": False, "reason": "unsupported", "detail": detail}


def switch(lang, *, user32=None, platform: str | None = None) -> dict:
    """{"ok": True} when the request was posted to the window in front (posted, not confirmed applied), else
    {"ok": False, "reason": "unsupported", "detail": why}. Never raises."""
    try:
        if not isinstance(lang, str) or lang not in PRIMARY:
            return _no("bad_lang")
        if (platform or sys.platform) != "win32":
            return _no("not_windows")
        u = user32 or _user32()
        hkl = pick(lang, _installed(u))
        if hkl is None:
            return _no("not_installed")
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return _no("no_window")
        # always asked, never "it is Arabic already": an earlier request may still be on its way to the
        # window, and asking for the language it already has changes nothing
        if not u.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl):
            return _no("refused")
        return {"ok": True}
    except Exception:                    # a convenience must never break the screen that asked for it
        return _no("error")
