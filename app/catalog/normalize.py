"""Text normalization for Egyptian pharmacy item names (Arabic + English).

Supplier invoices write the same item many ways:
  'AUGMENTIN 1GM 14 F.C.TAB', 'Augmentin 1 g tab 14', 'اوجمنتين ١ جم ١٤ قرص'
This module turns a raw name into comparable parts:
  core name tokens, normalized strength, dosage form, pack count, and a
  script-independent consonant skeleton used to compare Arabic with English.
Everything here is deterministic; no AI involved.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_AR_NORMAL = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي",
    "ـ": "", "،": ",", "٫": ".", "٬": ",",
})
_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")

# dosage forms -> canonical code. Order matters (longer phrases first).
_FORMS: list[tuple[str, str]] = [
    (r"f\.?\s?c\.?\s?tabs?|film\s?coated\s?tabs?", "tab"),
    (r"eff(?:ervescent)?\.?\s?tabs?|فوار", "eff"),
    (r"chewable\s?tabs?|مضغ", "chew"),
    (r"tablets?|tabs?|tab\b|قرص|اقراص|اقرص", "tab"),
    (r"capsules?|caps?|cap\b|كبسوله|كبسول|كبسولات", "cap"),
    (r"syrup|syr|شراب", "syrup"),
    (r"suspension|susp|معلق", "susp"),
    (r"drops?|نقط|نقاط", "drops"),
    (r"ampoules?|amps?|امبول|امبولات|حقن|حقنه", "amp"),
    (r"vials?|فيال", "vial"),
    (r"injection|inj", "amp"),
    (r"cream|crm|كريم", "cream"),
    (r"ointment|oint|مرهم", "oint"),
    (r"gel|جل", "gel"),
    (r"suppositor(?:y|ies)|supp|لبوس|اقماع", "supp"),
    (r"sachets?|sach|اكياس|كيس|ظرف", "sachet"),
    (r"spray|بخاخ|سبراي", "spray"),
    (r"inhaler|معلق استنشاق", "inhaler"),
    (r"lotion|لوشن", "lotion"),
    (r"shampoo|شامبو", "shampoo"),
    (r"solution|sol|محلول", "solution"),
    (r"powder|بودره", "powder"),
    (r"lozenges?|استحلاب", "lozenge"),
    (r"eye\s?drops?|قطره عين", "eyedrops"),
]
_FORM_RES = [(re.compile(rf"(?<![a-z؀-ۿ])(?:{p})(?![a-z؀-ۿ])"), c)
             for p, c in _FORMS]

# compatible groups: a 'tab' item may be written 'eff' only if truly eff, etc.
FORM_GROUP = {"tab": "solid", "chew": "solid", "cap": "solid", "eff": "eff", "lozenge": "solid",
              "syrup": "liquid", "susp": "liquid", "solution": "liquid", "drops": "drops",
              "eyedrops": "drops", "amp": "inj", "vial": "inj", "cream": "topical",
              "oint": "topical", "gel": "topical", "lotion": "topical", "supp": "supp",
              "sachet": "sachet", "powder": "sachet", "spray": "spray", "inhaler": "spray",
              "shampoo": "topical"}

_UNIT_MAP = {
    "mg": "mg", "milligram": "mg", "مجم": "mg", "ملجم": "mg", "مج": "mg", "مللجم": "mg",
    "mcg": "mcg", "µg": "mcg", "ug": "mcg", "ميكروجرام": "mcg",
    "g": "g", "gm": "g", "gr": "g", "gram": "g", "grams": "g", "جم": "g", "جرام": "g",
    "ml": "ml", "مل": "ml", "مللي": "ml", "l": "l",
    "iu": "iu", "i.u": "iu", "وحده": "iu", "%": "%",
}
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in _UNIT_MAP), key=len, reverse=True))
_NUM = r"\d+(?:\.\d+)?"
# 875/125 mg  |  500mg  |  1 gm  |  250mg/5ml  |  2%
_STRENGTH_RE = re.compile(
    rf"(?<![\w.])({_NUM}(?:\s*/\s*{_NUM})*)\s*({_UNIT_ALT})"
    rf"(?:\s*/\s*({_NUM})?\s*(ml|مل))?(?![a-z؀-ۿ])")
# pack count: '14 tab', '20 caps', 'x30', '30's', 'ع 20'
_COUNT_RE = re.compile(r"(?:(?<![\w.])x\s*(\d{1,4})\b|\b(\d{1,4})\s*'?s\b)")

_NOISE = {"new", "جديد", "the", "and", "for", "of", "film", "coated", "fc",
          "plain", "عاديه", "عادي", "pack", "box", "علبه", "عبوه", "strip", "شريط",
          "sr", "xr", "er", "mr", "cr", "retard"}
# release modifiers are meaningful and must not be dropped silently:
_MODIFIERS = {"sr", "xr", "er", "mr", "cr", "retard", "forte", "extra", "plus", "fort",
              "junior", "pediatric", "kids", "adult", "night", "cold", "duo", "max",
              "اكسترا", "فورت", "بلس", "نايت", "جونيور", "اطفال", "كولد"}
_MOD_CANON = {"اكسترا": "extra", "فورت": "forte", "fort": "forte", "بلس": "plus",
              "نايت": "night", "جونيور": "junior", "اطفال": "kids", "pediatric": "kids",
              "كولد": "cold"}


@dataclass
class ParsedName:
    raw: str
    norm: str
    core: str                 # name tokens only, sorted for order-independence
    core_tokens: list[str] = field(default_factory=list)
    strength: str | None = None
    form: str | None = None
    pack_count: int | None = None
    content: str | None = None    # pack content: '100ml', '15g'
    modifiers: frozenset[str] = frozenset()
    skeleton: str = ""


def normalize_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_AR_DIGITS).translate(_AR_NORMAL)
    s = _TASHKEEL.sub("", s).lower()
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)             # 2,5 -> 2.5
    s = re.sub(r"[^\w\s.%/']", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)           # keep decimal points only
    s = re.sub(r"(\d)([a-z\u0600-\u06ff])", r"\1 \2", s)  # 500mg -> 500 mg
    # letter->digit split ("x20", "tab14") except name codes such as vitamin
    # B12 / D3 / K2 / Q10 and Omega-3, which must stay distinct from B6 / Omega-6
    s = re.sub(r"(?<![a-z\u0600-\u06ff])([a-wyz])\s?(\d{1,2})(?![\d.])", r"\1\2", s)
    s = re.sub(r"([a-z\u0600-\u06ff])(?<![^a-z\u0600-\u06ff][a-wyz])(?<!^[a-wyz])(\d)", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


def _fmt_num(x: float) -> str:
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0").rstrip(".")


def _norm_strength(nums: str, unit: str, per: str | None, per_unit: str | None) -> str:
    unit = _UNIT_MAP.get(unit, unit)
    parts = [float(p) for p in re.split(r"\s*/\s*", nums)]
    if unit == "g":                      # grams -> mg so '1 gm' == '1000 mg'
        parts, unit = [p * 1000 for p in parts], "mg"
    out = "/".join(_fmt_num(p) for p in parts) + unit
    if per_unit:
        out += "/" + (_fmt_num(float(per)) if per else "1") + "ml"
    return out


_CONTENT_FORMS = {"cream", "oint", "gel", "lotion", "shampoo", "spray"}


def parse_name(raw: str) -> ParsedName:
    norm = normalize_text(raw)
    work = f" {norm} "
    work = re.sub(r"(?<=[a-z\u0600-\u06ff])/(?=[a-z\u0600-\u06ff])", " ", work)
    # "S.R." / "X.R." are normalized to "s r": rejoin so the release modifier is kept
    work = re.sub(r"(?<![a-z])([sxemc]) r(?![a-z])", r"\1r", work)
    work = re.sub(r"(?<![a-z])f c(?![a-z])", "fc", work)     # F.C. = film coated (noise)

    form = None
    for rx, code in _FORM_RES:
        if rx.search(work):
            form = form or code
            work = rx.sub(" ", work)

    strengths, contents = [], []
    for m in _STRENGTH_RE.finditer(work):
        unit = _UNIT_MAP.get(m.group(2), m.group(2))
        value = _norm_strength(m.group(1), m.group(2), m.group(3), m.group(4))
        is_content = (unit in ("ml", "l") and not m.group(4)) or (
            unit == "g" and form in _CONTENT_FORMS and "/" not in m.group(1))
        if is_content:
            # keep grams as g for pack content (15g tube, not 15000mg)
            if unit == "g":
                value = _fmt_num(float(m.group(1))) + "g"
            contents.append(value)
        else:
            strengths.append(value)
    work = _STRENGTH_RE.sub(" ", work)

    pack = None
    m = _COUNT_RE.search(work)
    if m:
        pack = int(m.group(1) or m.group(2))
        work = _COUNT_RE.sub(" ", work)
    tokens = [t for t in work.split() if t]
    nums = [i for i, t in enumerate(tokens) if t.isdigit()]
    if pack is None and nums and form:
        # with a dosage form, the last bare number is the pack count ("Omega 3 30 caps")
        pack = int(tokens[nums[-1]])
        tokens.pop(nums[-1])
    # remaining bare numbers are part of the product name ("Omega 3", "Centrum 50+"):
    # keep them, otherwise "Omega 3" and "Omega 6" would look identical
    tokens = [t for t in tokens if t not in ("'", "/")]
    modifiers = frozenset(_MOD_CANON.get(t, t) for t in tokens if t in _MODIFIERS)
    core_tokens = [t for t in tokens if t not in _NOISE or t in _MODIFIERS]
    # single letters are kept: they tell apart Anti-A / Anti-D sera, Alveolin / Alveolin-P
    core_tokens = [t for t in core_tokens if len(t) > 1 or t.isdigit() or t.isalpha()]
    return ParsedName(
        raw=raw, norm=norm, core=" ".join(sorted(core_tokens)), core_tokens=core_tokens,
        strength="+".join(strengths) if strengths else None, form=form, pack_count=pack,
        content="+".join(contents) if contents else None,
        modifiers=modifiers, skeleton=skeleton(" ".join(core_tokens)))


# ---------------------------------------------------------------------------
# Script-independent consonant skeleton: lets 'بانادول' and 'panadol' meet.
_AR_SKEL = {"ب": "b", "ت": "t", "ث": "s", "ج": "g", "ح": "h", "خ": "k", "د": "d",
            "ذ": "z", "ر": "r", "ز": "z", "س": "s", "ش": "s", "ص": "s", "ض": "d",
            "ط": "t", "ظ": "z", "ع": "", "غ": "g", "ف": "f", "ق": "k", "ك": "k",
            "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "", "ي": "", "ا": "", "ء": "",
            "پ": "b", "ڤ": "f", "چ": "g", "گ": "g"}
_EN_SUBS = [("ph", "f"), ("ch", "s"), ("sh", "s"), ("th", "t"), ("ck", "k"), ("qu", "k"),
            ("x", "ks"), ("c", "k"), ("q", "k"), ("p", "b"), ("v", "f"), ("j", "g"),
            ("w", ""), ("y", "")]


def skeleton(text: str) -> str:
    out = []
    for word in text.split():
        if re.search(r"[؀-ۿ]", word):
            w = "".join(_AR_SKEL.get(ch, "") for ch in word)
        else:
            w = re.sub(r"[^a-z]", "", word)
            for a, b in _EN_SUBS:
                w = w.replace(a, b)
            w = re.sub(r"[aeiou]", "", w)
        w = re.sub(r"(.)\1+", r"\1", w)   # collapse doubles
        w = w.replace("h", "") if len(w) > 2 else w
        if w:
            out.append(w)
    return " ".join(out)


_UNKNOWN_MAKER = {"غير معرفه", "غير معروف", "unknown", "-"}


def dedupe_key(name: str, strength: str | None, form: str | None, pack_size: int,
               manufacturer: str | None = None) -> str:
    """Identity of an item inside one pharmacy. The same generic name from two
    manufacturers (Ampicillin 500 12 cap) is two items, so a known maker is part
    of the key; an unknown maker is left out (never guessed)."""
    p = parse_name(name)
    s = strength_norm(strength) if strength else p.strength
    f = form or p.form
    # exact form, not FORM_GROUP: cream vs gel vs ointment of one brand are different items
    return "|".join([p.core, s or "", f or "", p.content or "",
                     str(p.pack_count or ""), str(pack_size)]) + _maker(manufacturer)


def _maker(m: str | None) -> str:
    t = normalize_text(m) if m else ""
    return f"|m:{t}" if t and t not in _UNKNOWN_MAKER else ""


def strength_norm(text: str | None) -> str | None:
    if not text:
        return None
    p = parse_name(text)
    return p.strength or normalize_text(text).replace(" ", "")


def normalize_form(text: str | None) -> str | None:
    if not text:
        return None
    t = f" {normalize_text(text)} "
    for rx, code in _FORM_RES:
        if rx.search(t):
            return code
    return None


def normalize_barcode(code: str | None) -> str | None:
    if not code:
        return None
    digits = re.sub(r"\D", "", str(code).translate(_AR_DIGITS))
    if not digits:
        return None
    # Excel often turns EAN-13 into a float like 6221000012345.0 -> handled by \D strip
    return digits


def valid_gtin(code: str) -> bool:
    """GS1 check digit for EAN-8/UPC-A/EAN-13/GTIN-14."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check
