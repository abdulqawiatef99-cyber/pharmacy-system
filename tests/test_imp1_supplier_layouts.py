"""Synthetic copies of two supplier layouts, generated in memory."""
import io
import unittest
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook

from app.purchasing.importers.files import extract_structured


def supplier_workbook(*, extra_footers=(), include_unit_price=True, public_total="اجمالى جمهور"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "11"
    sheet.append(["الكمية", "الصنف", "الوقت", "الاجمالى", public_total,
                  "السعر" if include_unit_price else None, "الصلاحية", "خصم 1"])
    for i in range(1, 7):
        qty, price = i + 1, 20 + i
        sheet.append([qty, f"نباتيل تجريبي {i}", "08:00", qty * price * 0.75, qty * price,
                      price if include_unit_price else None, "01/09/2029", 0.25])
    sheet.append([None, 6])
    for row in extra_footers:
        sheet.append(row)
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


SUPPLIER = "مخزن الهتون التجريبي"
ITEMS = [(1 + i % 3, Decimal(48 + i), 21 + i % 19) for i in range(1, 17)]
ITEMS[0] = (2, Decimal(49), 21)
ITEMS.append((1, Decimal("568.11"), 39))


def line_total(qty, price, discount):
    return (qty * price * (100 - discount) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _visual(arabic):
    # Encode fixture phrases in visual order, choosing presentation glyphs.
    forms = {unicodedata.normalize("NFKC", char): char for n in range(0xFE70, 0xFEFD)
             if len(unicodedata.normalize("NFKC", char := chr(n))) == 1}
    # Some Egyptian invoice fonts encode heh and yeh with Persian presentation forms.
    forms.update({"ه": "ﮭ", "ي": "ﯾ"})
    return "".join(forms.get(c, c) for c in reversed(arabic))


def _invoice_page(start, stop, *, header, last_page, rich_on_right):
    cells = []
    for right in (False, True):
        copy = 421 if right else 0
        rich = right == rich_on_right
        xs = [399, 365, 257, 169, 132, 104, 73, 27]
        offset = 0 if right else 5

        def put(x, top, value):
            cells.append((copy + x, top + offset, str(value)))

        if header:
            put(220, 28, _visual(SUPPLIER))
            if rich_on_right:
                put(288, 48, f"990701 : {_visual('رقم الفاتورة')}")
                put(125, 48, f"23/11/2026 : {_visual('التاريخ')}")
            else:
                put(288, 48, f"{_visual('رقم الفاتورة')} : 990701")
                put(125, 48, f"{_visual('التاريخ')} : 23/11/2026")
            if not last_page and rich_on_right:
                put(220, 66, _visual("شهرى"))
            for x, heading in zip(xs, ("م", "الموقع", "الصنف", "الكمية", "السعر", "الخصم", "الاجمالى")):
                put(x, 96, _visual(heading))
            if rich:
                if rich_on_right:
                    put(xs[-1], 96, _visual("صلاحية") + "/" + _visual("تشغيلة"))
                else:
                    put(xs[-1], 96, _visual("صلاحية"))
                    put(xs[-1], 106, _visual("تشغيلة"))
        for row, i in enumerate(range(start, stop)):
            top = 121 + row * 37
            qty, price, discount = ITEMS[i]
            name = f"{_visual('نـباتيل تجريبي')} Neo-{i + 1} 250 {_visual('مجم')}"
            values = (i + 1, "A-3", name, qty, price, f"{discount}%", line_total(qty, price, discount))
            for x, value in zip(xs, values):
                put(x, top, value)
            if rich:
                put(xs[-1], top, f"Z{i + 1:03d}")
                put(xs[-1], top + 11, "09/2029" if i == 16 else "01/09/2029")
            if i in (1, 12):
                put(xs[2], top + 11, _visual("عبوة تجريبية طويلة"))
        if last_page:
            if not rich_on_right:
                put(220, 500, _visual("شهرى"))
            put(225, 515, f"1,614.67 : {_visual('صافى الفاتورة')}")
    return cells


def supplier_pdf(*, repeat_header=True, rich_on_right=True):
    pages = [_invoice_page(0, 10, header=True, last_page=False, rich_on_right=rich_on_right),
             _invoice_page(10, 17, header=repeat_header, last_page=True, rich_on_right=rich_on_right)]
    # ReportLab needs a font that contains Arabic Presentation Forms.
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if font.is_file():
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.pdfgen.canvas import Canvas
        except ImportError:
            pass
        else:
            out = io.BytesIO()
            pdfmetrics.registerFont(TTFont("IMP1Arabic", str(font)))
            canvas = Canvas(out, pagesize=(842, 595))
            for cells in pages:
                canvas.setFont("IMP1Arabic", 7)
                for x, top, value in cells:
                    canvas.drawCentredString(x, 595 - top, value)
                canvas.showPage()
            canvas.save()
            return out.getvalue()
    return _minimal_text_pdf(pages)


def _minimal_text_pdf(pages, *, exact_words=False):
    """A Unicode CID text layer for extraction on hosts without an Arabic test font."""
    def stream(data):
        return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"

    cmap = b""" /CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /TestUnicode def /CMapType 2 def
1 begincodespacerange <0000> <FFFF> endcodespacerange
1 beginbfrange <0000> <FFFF> <0000> endbfrange
endcmap CMapName currentdict /CMap defineresource pop end end """
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [6 0 R 8 0 R] /Count 2 >>",
               b"<< /Type /Font /Subtype /Type0 /BaseFont /TestUnicode /Encoding /Identity-H "
               b"/DescendantFonts [4 0 R] /ToUnicode 5 0 R >>",
               b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /TestUnicode /DW 540 "
               b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
               b"/FontDescriptor 10 0 R >>",
               stream(cmap)]
    for cells in pages:
        content_obj = len(objects) + 2
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
                       b"/Resources << /Font << /F 3 0 R >> >> /Contents %d 0 R >>" % content_obj)
        commands = []
        for x, top, text in cells:
            if exact_words:
                # Leave a word boundary even if a synthetic replacement has wider glyphs.
                next_x = min((xx for xx, tt, _ in cells if xx > x and abs(tt - top) <= 2), default=842)
                scale = min(1, (next_x - x - 2) / (len(text) * 3.78))
                baseline = 595 - top - 5.6
            else:
                scale = 1
                x -= len(text) * 1.89
                baseline = 595 - top
            commands.append(f"BT /F 7 Tf {scale:.5f} 0 0 1 {x:.3f} {baseline:.3f} Tm "
                            f"<{text.encode('utf-16-be').hex()}> Tj ET")
        content = "\n".join(commands)
        objects.append(stream(content.encode("ascii")))

    objects.append(b"<< /Type /FontDescriptor /FontName /TestUnicode /Flags 4 "
                   b"/FontBBox [0 -200 540 800] /Ascent 800 /Descent -200 "
                   b"/CapHeight 700 /ItalicAngle 0 /StemV 80 >>")
    out = io.BytesIO(b"%PDF-1.4\n")
    out.seek(0, io.SEEK_END)
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objects) + 1, xref))
    return out.getvalue()


class TestSupplierWorkbook(unittest.TestCase):
    def test_count_footer_is_not_an_item(self):
        invoice = extract_structured("excel", supplier_workbook())
        self.assertEqual([line.raw_name for line in invoice.lines],
                         [f"نباتيل تجريبي {i}" for i in range(1, 7)])

    def test_public_total_is_neither_unit_price_nor_net_total(self):
        invoice = extract_structured("excel", supplier_workbook())
        self.assertEqual([line.public_price for line in invoice.lines[:6]], list(range(21, 27)))
        self.assertEqual(invoice.lines[0].line_total, Decimal("31.50"))
        self.assertEqual(invoice.lines[0].discount_pct, Decimal("25"))
        invoice = extract_structured("excel", supplier_workbook(include_unit_price=False, public_total="اجمالى سعر الجمهور"))
        self.assertTrue(all(line.public_price is None for line in invoice.lines))

    def test_summary_labels_and_non_item_rows_are_skipped(self):
        invoice = extract_structured("excel", supplier_workbook(extra_footers=[
            [None, "نهاية الكشف"], [None, "ملخص تجريبي", None, "-", None, "—"],
            [9, "عدد الأصناف", None, 9], [None, "الإجمالى", None, "440.00", "550.00"],
        ]))
        self.assertEqual(len(invoice.lines), 6)
        self.assertEqual(invoice.declared_total, Decimal("440.00"))


class TestSupplierPdf(unittest.TestCase):
    def test_explicit_arabic_percent_is_not_an_excel_fraction(self):
        lines = ["الصنف;الكمية;السعر;الخصم", "نباتيل تجريبي;1;100;٠٫٥٪"]
        invoice = extract_structured("csv", "\n".join(lines).encode())
        self.assertEqual(invoice.lines[0].discount_pct, Decimal("0.5"))


    def test_standalone_payment_next_to_an_item_is_not_a_name_fragment(self):
        first_page = _invoice_page(0, 10, header=True, last_page=False, rich_on_right=True)
        second_page = _invoice_page(10, 17, header=True, last_page=True, rich_on_right=True)
        second_page.append((678, 360, _visual("شهرى")))
        invoice = extract_structured("pdf", _minimal_text_pdf([first_page, second_page]))
        self.assertEqual(len(invoice.lines), 17, invoice.warnings)
        self.assertEqual(invoice.lines[-1].raw_name, "نباتيل تجريبي Neo-17 250 مجم")
        self.assertEqual(invoice.to_json()["payment_method"], "credit")

    def test_net_summary_above_header_does_not_end_the_table(self):
        first_page = _invoice_page(0, 10, header=True, last_page=False, rich_on_right=True)
        first_page.append((646, 80, f"1,614.67 : {_visual('صافى الفاتورة')}"))
        second_page = _invoice_page(10, 17, header=True, last_page=True, rich_on_right=True)
        invoice = extract_structured("pdf", _minimal_text_pdf([first_page, second_page]))
        self.assertEqual(len(invoice.lines), 17, invoice.warnings)
        self.assertEqual(invoice.declared_total, Decimal("1614.67"))

    def test_item_amounts_can_continue_below_its_serial(self):
        first_page = _invoice_page(0, 10, header=True, last_page=False, rich_on_right=True)
        first_page = [(x, 132 if top == 121 and x in (494, 525, 553, 590) else top, value)
                      for x, top, value in first_page]
        second_page = _invoice_page(10, 17, header=True, last_page=True, rich_on_right=True)
        invoice = extract_structured("pdf", _minimal_text_pdf([first_page, second_page]))
        self.assertEqual(len(invoice.lines), 17, invoice.warnings)
        self.assertEqual(invoice.warnings, [])
        line = invoice.lines[0]
        self.assertEqual((line.qty, line.public_price, line.discount_pct, line.line_total),
                         (2, Decimal("49"), Decimal("21"), Decimal("77.42")))
        self.assertEqual(line.sources["line_total"], "read")

    def test_two_copies_two_pages_read_only_the_customer_items(self):
        for repeat_header, rich_on_right in ((True, True), (False, True), (True, False)):
            with self.subTest(repeat_header=repeat_header, rich_on_right=rich_on_right):
                invoice = extract_structured(
                    "pdf", supplier_pdf(repeat_header=repeat_header, rich_on_right=rich_on_right))
                self.assertEqual(len(invoice.lines), 17, invoice.warnings)
                self.assertEqual(invoice.warnings, [])
                self.assertEqual(invoice.supplier_name, SUPPLIER)
                self.assertEqual(invoice.invoice_no, "990701")
                self.assertEqual(invoice.invoice_date, "2026-11-23")
                self.assertEqual(invoice.declared_total, Decimal("1614.67"))
                self.assertEqual(invoice.to_json()["payment_method"], "credit")
                self.assertEqual(sum(line.line_total for line in invoice.lines), Decimal("1614.67"))

                for i, (line, (qty, price, discount)) in enumerate(zip(invoice.lines, ITEMS)):
                    name = f"نباتيل تجريبي Neo-{i + 1} 250 مجم"
                    if i in (1, 12):
                        name += " عبوة تجريبية طويلة"
                    self.assertEqual(line.raw_name, name)
                    self.assertEqual((line.qty, line.public_price, line.discount_pct), (qty, price, discount))
                    self.assertEqual(line.sources["discount_pct"], "read")
                    self.assertEqual(line.batch_no, f"Z{i + 1:03d}")
                    expiry = ("2029-09-30", "month") if i == 16 else ("2029-09-01", "day")
                    self.assertEqual((line.expiry_date, line.expiry_precision), expiry)
                    self.assertLessEqual(
                        abs(line.line_total - qty * price * (100 - discount) / 100), Decimal("0.01"))
