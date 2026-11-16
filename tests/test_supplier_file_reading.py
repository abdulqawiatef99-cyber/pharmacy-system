"""Reading supplier invoice files (purchasing/importers/files.py): paths no test reached before.

Synthetic invoices only. The main gap: a CSV saved by Arabic Windows Excel is cp1256, not UTF-8;
its Arabic headers and item names must come through, or the header is never found.
"""
import io
import unittest
import zipfile
from decimal import Decimal

from app.core.errors import ValidationFailed
from app.purchasing.importers import files

ROWS = ["رقم الفاتورة: ف-501",
        "الصنف;الكمية;سعر الجمهور;الاجمالي",
        "بنادول 500 مجم 24 قرص;10;40.00;400.00",
        "كتافلام 50 مجم 20 قرص;5;60.00;300.00"]


def blank_pdf() -> bytes:
    """One empty page and no text: what a scanner produces, as far as text extraction goes."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"]
    out, offs = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


class TestCsvEncodings(unittest.TestCase):
    def check(self, data: bytes):
        ex = files.extract_structured("csv", data)
        self.assertNotIn("header_not_found", ex.warnings)
        self.assertEqual([(l.raw_name, l.qty) for l in ex.lines],
                         [("بنادول 500 مجم 24 قرص", 10), ("كتافلام 50 مجم 20 قرص", 5)])
        self.assertEqual([l.public_price for l in ex.lines], [Decimal("40.00"), Decimal("60.00")])

    def test_arabic_windows_csv_cp1256(self):
        data = ("\r\n".join(ROWS) + "\r\n").encode("cp1256")
        with self.assertRaises(UnicodeDecodeError):
            data.decode("utf-8")                      # really not UTF-8
        self.check(data)

    def test_utf8_with_bom(self):
        self.check(("\n".join(ROWS) + "\n").encode("utf-8-sig"))

    def test_rows_read_as_cells(self):
        rows = files.read_csv(("\r\n".join(ROWS) + "\r\n").encode("cp1256"))
        self.assertEqual(rows[1], ["الصنف", "الكمية", "سعر الجمهور", "الاجمالي"])


class TestSniff(unittest.TestCase):
    def test_webp_is_an_image(self):
        self.assertEqual(files.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20, "x.bin"), ("image", "image/webp"))

    def test_a_zip_that_is_not_a_workbook_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hello")
        with self.assertRaises(ValidationFailed) as e:
            files.sniff(buf.getvalue(), "invoice.xlsx")
        self.assertIn("مضغوط", e.exception.message)

    def test_a_broken_zip_is_refused(self):
        with self.assertRaises(ValidationFailed):
            files.sniff(b"PK\x03\x04" + b"\x00" * 40, "invoice.xlsx")

    def test_text_without_csv_name_is_read_as_csv(self):
        self.assertEqual(files.sniff("الصنف,الكمية\n".encode("utf-8"), "invoice")[0], "csv")
        self.assertEqual(files.sniff("الصنف,الكمية\n".encode("cp1256"), "invoice")[0], "csv")

    def test_binary_without_a_known_signature_is_refused(self):
        with self.assertRaises(ValidationFailed) as e:
            files.sniff(b"\x00\x01\x02garbage", "invoice.dat")
        self.assertEqual(e.exception.code, "unsupported_file")


class TestBrokenFiles(unittest.TestCase):
    def test_a_corrupt_workbook_is_a_clear_refusal(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", "<not really>")
        with self.assertRaises(ValidationFailed) as e:
            files.read_excel_sheets(buf.getvalue())
        self.assertIn("Excel", e.exception.message)

    def test_a_corrupt_pdf_is_a_clear_refusal(self):
        with self.assertRaises(ValidationFailed) as e:
            files.read_pdf(b"%PDF-1.4\n garbage")
        self.assertIn("PDF", e.exception.message)

    def test_a_pdf_without_text_goes_to_the_image_reader(self):
        grid, texts, has_text = files.read_pdf(blank_pdf())
        self.assertFalse(has_text)
        self.assertIsNone(files.extract_structured("pdf", blank_pdf()))


class TestDelimiters(unittest.TestCase):
    """Before: a title line above a ';' or tab table made the sniffer give up, and every line came out
    as a single cell, so the header was never found."""

    def test_semicolon_table_under_a_title_line(self):
        rows = files.read_csv(("\r\n".join(ROWS) + "\r\n").encode("utf-8"))
        self.assertEqual(rows[2], ["بنادول 500 مجم 24 قرص", "10", "40.00", "400.00"])

    def test_tab_table_under_a_title_line(self):
        rows = files.read_csv(("\r\n".join(ROWS).replace(";", "\t") + "\r\n").encode("cp1256"))
        self.assertEqual(rows[1], ["الصنف", "الكمية", "سعر الجمهور", "الاجمالي"])

    def test_commas_inside_a_semicolon_file_stay_in_the_cell(self):
        text = "رقم الفاتورة: 9\nالصنف;الكمية\nPanadol, 24 tab;3\nCataflam;2\n"
        self.assertEqual(files.read_csv(text.encode("utf-8"))[2], ["Panadol, 24 tab", "3"])

    def test_comma_files_unchanged(self):
        text = "Invoice No: C-77\nItem,Qty\nPanadol 500mg,10\n"
        self.assertEqual(files.read_csv(text.encode("utf-8"))[2], ["Panadol 500mg", "10"])

    def test_a_single_column_stays_one_column(self):
        self.assertEqual(files.read_csv("الصنف\nبنادول\n".encode("utf-8")), [["الصنف"], ["بنادول"]])
