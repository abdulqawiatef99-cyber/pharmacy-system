"""Barcode drawing for shelf / pack labels, as SVG (no external library).

EAN-13 when the item has a valid international code, otherwise Code 128 (B) of its
barcode or internal code — both read by every ordinary barcode scanner."""
from __future__ import annotations

from ..catalog.normalize import valid_gtin

_C128 = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
]
_EAN_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_EAN_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_EAN_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_EAN_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def code128_modules(text: str) -> str:
    """Bar/space pattern as a string of '1' (bar) and '0' (space). Digits use set C, anything else set B."""
    if not text or any(not 32 <= ord(c) <= 126 for c in text):
        raise ValueError("Code 128: printable ASCII only")
    if text.isdigit() and len(text) >= 4:
        # Code set C packs two digits per symbol: a 13-digit code fits a small label with bars wide enough to
        # scan (#84). An odd digit count ends with one digit in set B (code 100 switches C -> B).
        even = len(text) - len(text) % 2
        codes = [105] + [int(text[i:i + 2]) for i in range(0, even, 2)]
        if even < len(text):
            codes += [100, ord(text[-1]) - 32]
    else:
        codes = [104] + [ord(c) - 32 for c in text]
    check = (codes[0] + sum(i * c for i, c in enumerate(codes[1:], 1))) % 103
    out = ""
    for c in codes + [check, 106]:
        widths = _C128[c]
        for i, w in enumerate(widths):
            out += ("1" if i % 2 == 0 else "0") * int(w)
    return out


def ean13_modules(code: str) -> str:
    if len(code) != 13 or not valid_gtin(code):
        raise ValueError("not a valid EAN-13")
    first, left, right = int(code[0]), code[1:7], code[7:]
    out = "101"
    for d, p in zip(left, _EAN_PARITY[first]):
        out += (_EAN_L if p == "L" else _EAN_G)[int(d)]
    out += "01010"
    for d in right:
        out += _EAN_R[int(d)]
    return out + "101"


def svg(value: str, height: float = 40, quiet: int = 10) -> tuple[str, str]:
    """(svg markup, kind). Scales to its container width."""
    try:
        bits, kind = ean13_modules(value), "ean13"
    except ValueError:
        bits, kind = code128_modules(value), "code128"
    n = len(bits) + 2 * quiet
    rects = []
    i = 0
    while i < len(bits):
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1":
                j += 1
            rects.append(f'<rect x="{quiet + i}" y="0" width="{j - i}" height="{height}"/>')
            i = j
        else:
            i += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {height}" preserveAspectRatio="none"'
            f' role="img" aria-label="{value}">{"".join(rects)}</svg>', kind)
