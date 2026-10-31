"""Name normalization and attribute parsing for supplier product names.

Produces a search key (name_norm), the numeric attributes (strengths, sizes, pack
counts), the dosage form code, and any price tag printed inside the name
(e.g. "سعر 80 جنيه", "سعر جديد"). Nothing here decides money; it only builds keys.
"""
import re
import unicodedata

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
LETTER_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ی": "ي", "ې": "ي",
    "ئ": "ي", "ؤ": "و", "ک": "ك", "ھ": "ه", "ہ": "ه", "ۀ": "ه", "ـ": "",
})
DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭ]")

UNITS = {
    "mg": "mg", "مجم": "mg", "مج": "mg", "ملجم": "mg", "ملغ": "mg", "مليجرام": "mg",
    "mcg": "mcg", "مكجم": "mcg", "ميكروجرام": "mcg", "ug": "mcg",
    "ml": "ml", "مل": "ml", "ملل": "ml", "ملي": "ml", "مللي": "ml", "مم": "mm", "mm": "mm",
    "g": "g", "gm": "g", "gr": "g", "جم": "g", "جرام": "g", "جرم": "g",
    "kg": "kg", "كجم": "kg", "كيلو": "kg", "l": "l", "لتر": "l", "cm": "cm", "سم": "cm",
    "iu": "iu", "وحده": "iu", "%": "%",
}
FORMS = {
    "tab": ["اقراص", "قرص", "اقرص", "tab", "tabs", "tablet", "tablets", "اقراصا", "اقراض"],
    "cap": ["كبسول", "كبسوله", "كبسولات", "كبسوله", "cap", "caps", "capsule", "capsules", "كبسولة"],
    "syrup": ["شراب", "syrup", "syr"],
    "susp": ["معلق", "susp", "suspension"],
    "amp": ["امبول", "امبولات", "امبوله", "حقن", "حقنه", "فيال", "amp", "ampoule", "vial", "inj"],
    "supp": ["لبوس", "تحاميل", "supp"],
    "drops": ["نقط", "نقطه", "قطره", "قطرات", "drops", "drop"],
    "eye_drops": ["قطرة_عين"],
    "cream": ["كريم", "cream"],
    "oint": ["مرهم", "oint", "ointment"],
    "gel": ["جل", "gel", "جيل"],
    "lotion": ["لوشن", "لوسيون", "lotion"],
    "spray": ["بخاخ", "سبراي", "اسبراي", "سبراى", "spray"],
    "sachet": ["اكياس", "كيس", "فوار", "sachet", "sachets"],
    "shampoo": ["شامبو", "shampoo"],
    "solution": ["محلول", "solution", "غسول"],
    "oil": ["زيت", "oil"],
    "soap": ["صابون", "soap"],
    "powder": ["بودره", "بودر", "powder"],
}
FORM_OF = {w: code for code, words in FORMS.items() for w in words}
DRUG_FORMS = {"tab", "cap", "syrup", "susp", "amp", "supp", "drops", "eye_drops", "sachet"}
PACK_WORDS = {"قرص", "ق", "اقراص", "كبسول", "كبسوله", "شريط", "شرايط", "امبول", "كيس", "اكياس", "لبوس",
              "حقن", "قلم", "فيال", "tab", "cap", "x"}

_B = r"(?<![\w.])"
_E = r"(?![\w])"
PRICE_TAG = re.compile(
    rf"{_B}سعر\s*(?:جديد{_E}|قديم{_E})?\s*(?:ج\s+)?(\d+(?:\.\d+)?)?\s*(?:جنيه{_E}|جنية{_E}|ج{_E})?"
    rf"|{_B}(\d+(?:\.\d+)?)\s*(?:جنيه|جنية){_E}"
    rf"|{_B}(\d+(?:\.\d+)?)\s*ج{_E}"
    rf"|{_B}ج\s*(\d+(?:\.\d+)?){_E}"
    rf"|{_B}س\s*\.?\s*ج{_E}"
    rf"|{_B}(?:جديد|قديم|new|old){_E}"
)
STRAY_LATIN_UNIT = re.compile(r"[-_]*(ml|ML|Ml|kg|KG|Kg|gm|GM|cm|CM|mm|MM)[-_]*")


def base_clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = DIACRITICS.sub("", s)
    s = s.translate(LETTER_MAP).translate(AR_DIGITS)
    return s


def tidy_display(name: str) -> str:
    """Readable cleanup for display: Latin digits, no price tags, glued Latin units moved
    next to a number. The original name is always kept separately."""
    s = unicodedata.normalize("NFKC", name or "")
    s = s.translate(AR_DIGITS).translate(str.maketrans({"ی": "ي", "ک": "ك", "ھ": "ه", "ـ": ""}))
    s = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_%])", " ", s)
    s = re.sub(r"\s*[*]+\s*\d*\s*$", "", s)   # trailing "*40" carton counts
    s = re.sub(r"(?<=[A-Za-z])(?=[\u0600-\u06ff])|(?<=[\u0600-\u06ff])(?=[A-Za-z])", " ", s)
    s = PRICE_TAG.sub(" ", s)
    units = STRAY_LATIN_UNIT.findall(s)
    if len(units) == 1 and re.search(r"\d", s):
        unit = units[0].lower()
        rest = STRAY_LATIN_UNIT.sub(" ", s)
        if not re.search(rf"\d\s*{unit}\b", rest, re.I):
            nums = list(re.finditer(r"\d+(?:[.,]\d+)?", rest))
            if nums:
                m = nums[-1]
                rest = rest[:m.end()] + f" {unit}" + rest[m.end():]
        s = rest
    s = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", s)
    s = re.sub(r"\s*-\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip(" -_")


def parse_name(name: str) -> dict:
    s = base_clean(name).lower()
    s = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_%])", " ", s)
    s = re.sub(r"(?<=[a-z])(?=[\u0600-\u06ff])|(?<=[\u0600-\u06ff])(?=[a-z])", " ", s)
    price_tags = []
    has_price_tag = False

    def _tag(m):
        nonlocal has_price_tag
        has_price_tag = True
        for g in m.groups():
            if g:
                price_tags.append(float(g))
        return " "

    s = PRICE_TAG.sub(_tag, s)
    s = re.sub(r"[_\-+*/()\[\]{},:;|\\\"'`.]", lambda m: " " if m.group(0) not in "./" else m.group(0), s)
    # split digits from letters, keep decimals and ratios like 12.5/5 together
    s = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_%])", " ", s)
    s = re.sub(r"(?<=\d)\s*%", " %", s)
    toks = [t for t in re.split(r"\s+", s) if t and t not in {".", "/"}]
    numbers, strengths, pack = [], [], None
    form = None
    core = []
    i = 0
    while i < len(toks):
        t = toks[i].strip(".")
        if not t:
            i += 1
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)*", t):
            vals = [v.rstrip("0").rstrip(".") if "." in v else v for v in t.split("/")]
            numbers.extend(vals)
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt in UNITS:
                strengths.append("/".join(vals) + UNITS[nxt])
                i += 2
                continue
            if nxt in PACK_WORDS and pack is None:
                pack = "/".join(vals)
                if nxt in FORM_OF and form is None:
                    form = FORM_OF[nxt]
                i += 2
                continue
            i += 1
            continue
        if t in FORM_OF:
            if form is None:
                form = FORM_OF[t]
            i += 1
            continue
        if t in UNITS:   # unit without number (e.g. "ml" alone)
            i += 1
            continue
        core.append(t)
        i += 1
    core_str = " ".join(core)
    return {
        "core": core_str,
        "brand": core[0] if core else "",
        "numbers": sorted(set(numbers), key=lambda x: (len(x), x)),
        "strengths": strengths,
        "pack_count": pack,
        "form": form,
        "price_tags": price_tags,
        "has_price_tag": has_price_tag,
        "name_norm": " ".join(filter(None, [core_str, " ".join(strengths), form or "",
                                            f"x{pack}" if pack else ""])).strip(),
    }


def match_key(p: dict) -> str:
    """Exact-match key: core words (order-insensitive) + all numbers + form."""
    return "|".join([" ".join(sorted(p["core"].split())), ",".join(p["numbers"]), p["form"] or ""])
