"""Keyboard 2D scanners often drop the GS separator after a batch or a serial. Synthetic codes only.

Before: a serial first (01 21 17 10, common on Egyptian packs) swallowed the expiry and the batch into
the serial; a batch containing "21" was cut at that "21" and the expiry was lost.
"""
import unittest

from app.catalog import gs1

GTIN = "0106221234567890"
G = "\x1d"


class TestWithoutSeparator(unittest.TestCase):
    def check(self, code, expiry, batch, serial=None):
        out = gs1.parse(code)
        self.assertEqual((out.get("expiry"), out.get("batch"), out.get("serial")), (expiry, batch, serial), code)

    def test_serial_first_then_expiry_and_batch(self):
        self.check(GTIN + "21SERIAL" + "17270630" + "10BATCH", "2027-06-30", "BATCH", "SERIAL")

    def test_serial_with_digits_first(self):
        self.check(GTIN + "21A12345678" + "17270630" + "10L5", "2027-06-30", "L5", "A12345678")

    def test_batch_containing_21_before_the_expiry(self):
        self.check(GTIN + "10B2101" + "17270630", "2027-06-30", "B2101")
        self.check(GTIN + "10B2101" + "17270630" + "21SER", "2027-06-30", "B2101", "SER")

    def test_batch_then_serial_unchanged(self):
        self.check(GTIN + "17270630" + "10AB123" + "21SERIAL", "2027-06-30", "AB123", "SERIAL")

    def test_batch_then_expiry_then_end(self):
        self.check(GTIN + "10XY9" + "17280600", "2028-06-30", "XY9")

    def test_not_a_date_is_not_a_cut(self):
        # 17 followed by six digits that are not a date (month 13) stays inside the batch
        self.check(GTIN + "10B171399" + "21SER001", None, "B171399", "SER001")

    def test_with_separators_unchanged(self):
        self.check(GTIN + "21SER17" + "\x1d" + "17270630" + "10B21", "2027-06-30", "B21", "SER17")
        self.check(GTIN + "10B2101" + "\x1d" + "21SER", None, "B2101", "SER")

    def test_serial_alone_runs_to_the_end(self):
        self.check(GTIN + "21SER1727063099", None, None, "SER1727063099")

    def test_an_expiry_already_read_is_never_replaced_by_digits_in_a_later_field(self):
        # Codex on #226: a terminal serial ending in 17<YYMMDD>, with every separator present
        self.check(GTIN + "17280630" + "10BATCH" + G + "21SER17270630", "2028-06-30", "BATCH", "SER17270630")
