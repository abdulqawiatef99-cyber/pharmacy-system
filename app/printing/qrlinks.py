"""What the receipt QR codes point to (F27): turn what the owner types into a working link and give each
code a short caption, so the customer knows what scanning it does.

The owner may type an Egyptian mobile number (WhatsApp) or paste any link (Facebook page, Google Maps
location, the InstaPay payment link copied from the InstaPay app, ...)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_MOBILE = re.compile(r"^(?:\+?20|0020|0)?(1[0125]\d{8})$")


def normalize(text: str) -> str:
    """An Egyptian mobile number -> its wa.me link; anything else is kept as typed (trimmed)."""
    t = str(text or "").strip()
    m = _MOBILE.match(re.sub(r"[\s\-()]", "", t))
    return f"https://wa.me/20{m.group(1)}" if m else t


_CAPTIONS = (
    (("wa.me", "whatsapp.com"), "اطلب على واتساب"),
    (("maps.app.goo.gl", "goo.gl", "maps.google.com", "google.com"), "موقعنا على الخريطة"),
    (("facebook.com", "fb.com", "fb.me", "m.facebook.com"), "تابعنا على فيسبوك"),
    (("instagram.com",), "تابعنا على إنستجرام"),
    (("ipn.eg", "instapay.eg"), "ادفع بإنستاباي"),
)


def auto_caption(link: str) -> str:
    """Default caption from the link's site; the owner's own caption always wins."""
    try:
        host = (urlsplit(link).hostname or "").lower()
    except ValueError:
        return ""
    host = host[4:] if host.startswith("www.") else host
    for hosts, caption in _CAPTIONS:
        if host in hosts and (caption != "موقعنا على الخريطة" or host != "google.com" or "/maps" in link):
            return caption
    return ""


def caption(link: str, own: str = "") -> str:
    return (own or "").strip() or auto_caption(link)
