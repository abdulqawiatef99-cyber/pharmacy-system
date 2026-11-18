"""QR Code encoder for the receipt (no external library, like barcode.py).

Byte mode, error correction level M (~15% damage), versions 1-10: up to 213 bytes, enough for a
link or a WhatsApp number. Kept dependency-free so it reaches the pharmacy through the normal
in-app update (a requirements.txt change ships only with the installer). Follows ISO/IEC 18004;
the structure mirrors the well-known reference implementation (Project Nayuki, MIT).
Verified by decoding every version x mask with independent decoders (tests/test_receipt_qr.py)."""
from __future__ import annotations

MAX_VERSION = 10
# version -> (ec codewords per block, [(blocks, data codewords per block), ...]) for level M
_BLOCKS = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_EC_M_BITS = 0  # format-info bits for level M


class QRTooLong(ValueError):
    pass


def capacity(version: int) -> int:
    """Bytes of payload a version holds in byte mode at level M."""
    data_bits = 8 * sum(n * k for n, k in _BLOCKS[version][1])
    return (data_bits - 4 - (8 if version < 10 else 16)) // 8


# ---------------------------------------------------------------- Reed-Solomon over GF(256), poly 0x11D
def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ---------------------------------------------------------------- codewords
def _codewords(payload: bytes, version: int) -> list[int]:
    ec_len, groups = _BLOCKS[version]
    data_len = sum(n * k for n, k in groups)
    bits: list[int] = []

    def put(val: int, n: int) -> None:
        bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))
    put(0b0100, 4)
    put(len(payload), 8 if version < 10 else 16)
    for b in payload:
        put(b, 8)
    put(0, min(4, data_len * 8 - len(bits)))
    put(0, -len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < data_len:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    blocks, pos = [], 0
    divisor = _rs_divisor(ec_len)
    for n, k in groups:
        for _ in range(n):
            blk = data[pos:pos + k]
            pos += k
            blocks.append((blk, _rs_remainder(blk, divisor)))
    out = []
    for i in range(max(len(b) for b, _ in blocks)):
        out += [b[i] for b, _ in blocks if i < len(b)]
    for i in range(ec_len):
        out += [e[i] for _, e in blocks]
    return out


# ---------------------------------------------------------------- matrix
class _Grid:
    def __init__(self, version: int):
        self.v, self.n = version, 17 + 4 * version
        self.m = [[False] * self.n for _ in range(self.n)]
        self.fn = [[False] * self.n for _ in range(self.n)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = dark
        self.fn[y][x] = True

    def functions(self) -> None:
        n = self.n
        for i in range(n):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.v]
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, len(pos) - 1), (len(pos) - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)        # reserve the areas; real bits drawn after masking
        if self.v >= 7:
            rem = self.v
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.v << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self.set(a, b, bit)
                self.set(b, a, bit)

    def format_bits(self, mask: int) -> None:
        n = self.n
        data = _EC_M_BITS << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1  # noqa: E731
        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, n - 15 + i, bit(i))
        self.set(8, n - 8, True)    # the dark module

    def place(self, codewords: list[int]) -> None:
        n, i = self.n, 0
        total = len(codewords) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < total:
                        self.m[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        f = _MASKS[mask]
        for y in range(self.n):
            for x in range(self.n):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]


_MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]
_FINDER_LIKE = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])


def _penalty(m: list[list[bool]]) -> int:
    """ISO 18004 mask evaluation (rules 1-4); only picks the most readable mask."""
    n, score = len(m), 0
    lines = [row for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]
    for line in lines:
        run, prev = 0, None
        for c in line:
            if c == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run, prev = 1, c
        if run >= 5:
            score += 3 + run - 5
        bits = [int(c) for c in line]
        for i in range(n - 10):
            if bits[i:i + 11] in _FINDER_LIKE:
                score += 40
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    score += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return score


def encode(text: str | bytes, mask: int | None = None) -> list[list[bool]]:
    """QR matrix (True = dark), without the quiet zone."""
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if not payload:
        raise QRTooLong("نص الكود فارغ")
    version = next((v for v in range(1, MAX_VERSION + 1) if len(payload) <= capacity(v)), None)
    if version is None:
        raise QRTooLong(f"نص الكود أطول من {capacity(MAX_VERSION)} حرفًا")
    cw = _codewords(payload, version)
    best = None
    for k in ([mask] if mask is not None else range(8)):
        g = _Grid(version)
        g.functions()
        g.place(cw)
        g.apply_mask(k)
        g.format_bits(k)
        s = _penalty(g.m) if mask is None else 0
        if best is None or s < best[0]:
            best = (s, g.m)
    return best[1]


def image(matrix: list[list[bool]], module: int = 6, quiet: int = 4):
    """Pillow 'L' image, black modules on white, with the quiet zone."""
    from PIL import Image
    n = len(matrix)
    side = (n + 2 * quiet) * module
    img = Image.new("L", (side, side), 255)
    px = img.load()
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                for yy in range((y + quiet) * module, (y + quiet + 1) * module):
                    for xx in range((x + quiet) * module, (x + quiet + 1) * module):
                        px[xx, yy] = 0
    return img


def svg(matrix: list[list[bool]], size_mm: float = 22, quiet: int = 4) -> str:
    """Inline SVG for the browser receipt (crisp at any printer resolution)."""
    n = len(matrix) + 2 * quiet
    path = "".join(f"M{x + quiet},{y + quiet}h1v1h-1z" for y, row in enumerate(matrix)
                   for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{size_mm}mm" '
            f'height="{size_mm}mm" shape-rendering="crispEdges"><rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/></svg>')
