"""#84: the owner's real label size (custom W x H), the E-Stock fields (pharmacy name, phone, item name,
barcode, internal code, expiry, price) as toggles, the barcode never shrunk below scannable, and fields
dropped (phone, then pharmacy name) when the size cannot hold them. Synthetic data only."""
import re
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import ValidationFailed
from app.inventory import service as inv
from app.printing import barcode as bc, labels as lb, settings as rs
from app.printing.escpos import PrinterError
from app.printing.service import label_data, print_labels
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

ALL = {k: True for k in lb.FIELDS}
DATA = {"name": "Panadol Extra 24 Tab", "value": "6221025012340", "code": "8130", "price": "40.00 ج",
        "expiry": "10/27", "pharmacy": "Test Pharmacy", "phone": "0100 000 0000"}


def decode_c128(bits: str) -> str:
    """Bars back to text (sets B and C), checking the checksum: proves what a scanner would read."""
    table = {"".join(("1" if i % 2 == 0 else "0") * int(w) for i, w in enumerate(p)): n for n, p in enumerate(bc._C128)}
    syms, i = [], 0
    while i < len(bits):
        n = 13 if bits[i:i + 13] in table and table[bits[i:i + 13]] == 106 else 11
        syms.append(table[bits[i:i + n]])
        i += n
    assert syms[-1] == 106 and syms[0] in (104, 105)
    body, check = syms[1:-2], syms[-2]
    assert (syms[0] + sum(k * v for k, v in enumerate(body, 1))) % 103 == check
    out, mode = "", "C" if syms[0] == 105 else "B"
    for v in body:
        if v == 100:
            mode = "B"
        elif mode == "C":
            out += f"{v:02d}"
        else:
            out += chr(v + 32)
    return out


def bars_in(img) -> tuple[str, int]:
    """Read one pixel row across the bars like a scanner (bars are the row repeated most, unlike text): the bar
    pattern in modules, and the module width in dots."""
    from collections import Counter
    rows = Counter(tuple(img.getpixel((x, y)) == 0 for x in range(img.size[0])) for y in range(img.size[1]))
    px = list(next(r for r, _ in rows.most_common() if any(r)))
    first, last = px.index(True), len(px) - 1 - px[::-1].index(True)
    runs, cur, n = [], px[first], 0
    for v in px[first:last + 1]:
        if v == cur:
            n += 1
        else:
            runs.append((cur, n)); cur, n = v, 1
    runs.append((cur, n))
    module = min(n for _, n in runs)
    assert all(n % module == 0 for _, n in runs)
    return "".join(("1" if v else "0") * (n // module) for v, n in runs), module


class TestSizes(Base):
    def test_custom_size_and_validation(self):
        self.assertEqual(lb.parse_size("38x12.5"), (38.0, 12.5))
        for bad in ("19x10", "38x9", "111x20", "38x81", "abc", "38x12.55", ""):
            with self.assertRaises(PrinterError, msg=bad):
                lb.parse_size(bad)
        self.assertEqual(rs.save(self.db, self.ctx, {"label_size": "38x13"})["label_size"], "38x13")
        self.assertEqual(rs.save(self.db, self.ctx, {"label_size": "38x12.5"})["label_size"], "38x12.5")
        with self.assertRaises(ValidationFailed):
            rs.save(self.db, self.ctx, {"label_size": "38x5"})
        js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
        for name in ("label_w", "label_h"):                                    # 38 x 12.3 can be typed and saved
            self.assertRegex(js, rf'name="{name}" type="number" min="\d+" max="\d+" step="0.1"')

    def test_fields_defaults_partial_save_and_a4(self):
        c = rs.get(self.db, self.ctx.org_id)
        self.assertEqual(c["label_fields"], lb.DEFAULT_FIELDS)        # the fields printed before #84
        c = rs.save(self.db, self.ctx, {"label_fields": {"expiry": True, "phone": True}})
        self.assertTrue(c["label_fields"]["expiry"] and c["label_fields"]["phone"] and c["label_fields"]["price"])
        for bad in ({"label_fields": {"logo": True}}, {"label_fields": ["name"]}, {"label_a4": "9x24"}, {"label_a4": "6x"}):
            with self.assertRaises(ValidationFailed, msg=bad):
                rs.save(self.db, self.ctx, bad)
        self.assertEqual(rs.save(self.db, self.ctx, {"label_a4": "6x24"})["label_a4"], "6x24")


class TestBarcode(Base):
    def test_code128_digits_use_set_c_and_decode(self):
        for v in ("6221025012345", "8130", "26824", "ABC-12"):
            self.assertEqual(decode_c128(bc.code128_modules(v)), v)
        self.assertLess(len(bc.code128_modules("6221025012345")), len(bc.code128_modules("622102501234X")))

    def test_small_label_keeps_a_scannable_barcode(self):
        for value, kind in (("6221025012340", "ean13"), ("6221025012345", "c128"), ("8130", "c128")):
            img, dropped = lb.render_label({**DATA, "value": value}, 38, 10, 203, ALL)
            self.assertEqual(img.size, (lb.dots(38, 203), lb.dots(10, 203)))
            bits, module = bars_in(img)
            self.assertGreaterEqual(module, 2)                                 # >= 0.25 mm at 203 dpi
            if kind == "ean13":
                self.assertEqual(bits, bc.ean13_modules(value))
            else:
                self.assertEqual(decode_c128(bits), value)
            self.assertEqual(dropped, ["phone", "pharmacy"])                    # 10 mm: the row with both goes
        for h in (13, 25):                                                     # E-Stock: all fields fit at 38 x 13
            self.assertEqual(lb.render_label(DATA, 38, h, 203, ALL)[1], [], h)

    def test_too_small_is_refused_not_shrunk(self):
        with self.assertRaises(PrinterError):
            lb.render_label(DATA, 20, 10, 203, ALL)                            # bars would be thinner than 0.25 mm
        with self.assertRaises(PrinterError):
            lb.render_label({**DATA, "value": "A" * 40}, 38, 25, 203, ALL)
        img, _ = lb.render_label(DATA, 38, 10, 203, {**ALL, "barcode": False})   # text only: allowed
        self.assertEqual(img.size[1], lb.dots(10, 203))


class TestLabelData(Base):
    def test_sources_expiry_identity(self):
        it = cat.create_item(self.db, self.ctx, {"name_en": "Brufen 400", "code": "26824", "public_price": 400})
        T = date.today()
        with self.db.transaction():
            old = inv.get_or_create_batch(self.db, self.ctx, it, "OLD", (T + timedelta(days=30)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=2, batch_id=old, movement_type="purchase", unit_cost=Decimal("1"))
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=-2, batch_id=old, movement_type="adjustment", unit_cost=None)
            near = (T + timedelta(days=200)).isoformat()
            b = inv.get_or_create_batch(self.db, self.ctx, it, "NEW", near)
            inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=b, movement_type="purchase", unit_cost=Decimal("1"))
        rs.save(self.db, self.ctx, {"title": "صيدلية تجربة", "lines": ["شارع تجربة", "0100 111 2222"]})
        row = self.db.one("SELECT * FROM items WHERE id=?", (it,))
        d = label_data(self.db, self.ctx, row, rs.get(self.db, self.ctx.org_id))
        self.assertEqual((d["code"], d["price"], d["pharmacy"], d["phone"]), ("26824", "4.00 EGP", "صيدلية تجربة", "0100 111 2222"))  # label words in English by default (owner, 12/11)
        self.assertEqual(d["expiry"], f"{near[5:7]}/{near[2:4]}")             # the empty older batch is skipped
        rs.save(self.db, self.ctx, {"label_pharmacy": "Pharmacy X", "label_phone": "02 000"})
        d = label_data(self.db, self.ctx, row, rs.get(self.db, self.ctx.org_id))
        self.assertEqual((d["pharmacy"], d["phone"]), ("Pharmacy X", "02 000"))

    def test_direct_job_one_label_per_item_at_custom_size(self):
        import os
        import tempfile
        rs.save(self.db, self.ctx, {"label_lang": "tspl", "label_size": "38x12.5", "label_gap": 2,
                                    "label_fields": {"expiry": True, "code": True}})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "job.bin")
            r = print_labels(self.db, self.ctx, [(self.pan, 3)], target=f"file:{out}")
            job = Path(out).read_bytes()
        self.assertTrue(job.startswith(b"SIZE 38 mm,12.5 mm\r\nGAP 2 mm,0 mm"))
        m = re.search(rb"BITMAP 0,0,(\d+),(\d+),0,", job)
        self.assertEqual(int(m.group(2)), lb.dots(12.5, 203))                 # the image is exactly one label high
        self.assertEqual(job.count(b"PRINT 1,3"), 1)
        self.assertEqual(r["labels"], 3)


class TestLabelPages(APICase):
    def setUp(self):
        super().setUp()
        cat.add_barcode(self.db, self.ctx, self.pan, "6221025012340")
        self.http_login()

    def test_browser_page_custom_size_fields_and_a4(self):
        rs.save(self.db, self.ctx, {"label_size": "38x13", "label_fields": {"price": False, "expiry": True}})
        page = self.c.get("/labels", params={"items": f"{self.pan}:2"}).text
        self.assertIn("/labels/page.css?w=38&amp;h=13", page)
        self.assertEqual(page.count('class="lb"'), 2)
        self.assertNotIn("40.00", page)
        css = self.c.get("/labels/page.css", params={"w": "38", "h": "13"})
        self.assertIn("@page { size: 38mm 13mm; margin: 0; }", css.text)
        self.assertIn(".lb { width: 38mm; height: 13mm; padding: 0.6mm; }", css.text)
        # Codex on #90: the page's margin is the one the bar-width check uses, and nothing else pads a label
        self.assertEqual(lb.PAD_MM, 0.6)
        static = (Path(__file__).resolve().parent.parent / "app" / "web" / "labels.css").read_text(encoding="utf-8")
        self.assertNotRegex(static.split(".lb .t")[0], r"\.lb \{[^}]*padding")
        self.assertNotIn("padding", css.text.split("padding: 0.6mm")[1])
        self.assertEqual(self.c.get("/labels/page.css", params={"w": "5", "h": "13"}).status_code, 400)
        rs.save(self.db, self.ctx, {"label_lang": "a4", "label_a4": "6x24"})
        page = self.c.get("/labels", params={"items": f"{self.pan}:1"}).text
        self.assertIn('class="a4"', page)
        css = self.c.get("/labels/page.css", params={"a4": "6x24"}).text
        self.assertIn("grid-template-columns: repeat(6, 35mm)", css)
        self.assertIn("grid-auto-rows: 12.37mm", css)                          # 24 rows fit in 297 mm (Codex on #90)
        for c in range(1, 9):
            for r in range(1, 41):
                w, h = lb.a4_cell(c, r)
                self.assertTrue(w * c <= 210 and h * r <= 297, (c, r))
        r = self.c.post("/api/print/labels", json={"items": [{"id": self.pan, "count": 1}]}, headers=H)
        self.assertEqual(r.json()["error"]["code"], "labels_in_browser")     # A4 sheets print from the browser

    def test_browser_page_refuses_a_barcode_too_narrow_to_scan(self):
        # Codex on #90: 20 mm wide would stretch an EAN-13 to ~0.16 mm bars in the browser
        rs.save(self.db, self.ctx, {"label_size": "20x25"})
        r = self.c.get("/labels", params={"items": f"{self.pan}:1"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("أعرض", r.json()["error"]["message"])
        rs.save(self.db, self.ctx, {"label_fields": {"barcode": False}})       # text-only labels still print
        page = self.c.get("/labels", params={"items": f"{self.pan}:1"})
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('class="b"', page.text)                               # no empty 4 mm barcode box (Codex on #90)

    def test_preview_png_and_dropped_fields(self):
        r = self.c.post("/api/print/label-preview", json={"label_size": "38x10", "label_fields": ALL, "label_phone": "0100",
                                                                   # Latin name: on a PC without Arabic shaping
                                                                   # (Windows CI) an Arabic one is left off first
                                                                   "label_pharmacy": "Test Pharmacy"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        self.assertEqual(r.headers["x-label-dropped"], "phone,pharmacy")
        r = self.c.post("/api/print/label-preview", json={"label_lang": "a4", "label_a4": "6x24"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)                            # the default A4 grid previews (valid EAN-13 sample)
        r = self.c.post("/api/print/label-preview", json={"label_size": "20x10", "label_fields": ALL}, headers=H)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "label_too_small"))
