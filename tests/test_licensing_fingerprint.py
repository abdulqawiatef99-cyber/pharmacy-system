"""Device fingerprint (app/licensing/fingerprint.py): which PC a licence belongs to, the rule that a
copied disk is another PC, and the activation request text. Identifiers here are made up."""
import os
import unittest
from unittest import mock

from app.licensing import fingerprint as fp

BOARD_A = "4c4c4544-0042-3510-8051-c7c04f4a3432"
BOARD_B = "9f2d1a70-11c3-4e57-a2b8-0d6f5e3c7a91"
PC_A = {"board": BOARD_A, "system": "5d3f1c9e-7a44-4b8e-9a1e-2f6c0d7b8e11", "disk": "a4f1c2d3"}


def smbios(uuid_bytes: bytes | None, *, before: int = 1, type1_len: int = 0x1B) -> bytes:
    """A raw SMBIOS table as Windows returns it: 8-byte header, then structures."""
    def struct(kind: int, formatted: bytes, strings: tuple[bytes, ...] = (b"Vendor",)) -> bytes:
        body = bytes([kind, 4 + len(formatted), 0, 0]) + formatted
        return body + b"".join(s + b"\0" for s in strings) + b"\0" if strings else body + b"\0\0"
    data = b"".join(struct(0, bytes(20), (b"BIOS", b"1.0")) for _ in range(before))
    if uuid_bytes is not None:
        formatted = bytes(4) + uuid_bytes + bytes(max(0, type1_len - 4 - 4 - 16))
        data += struct(1, formatted[:type1_len - 4], (b"Maker", b"Model"))
    data += struct(2, bytes(8), ()) + struct(127, b"", ())
    return bytes([0, 3, 2, 0]) + len(data).to_bytes(4, "little") + data


class TestSmbios(unittest.TestCase):
    RAW = bytes.fromhex("44454c4c" "4200" "1035" "8051c7c04f4a3432")     # first three fields little-endian

    def test_uuid_is_found_after_other_structures_and_their_strings(self):
        for before in (0, 1, 3):
            self.assertEqual(fp.smbios_uuid(smbios(self.RAW, before=before)), BOARD_A)

    def test_factory_filler_uuids_are_not_identifiers(self):
        for raw in (bytes(16), b"\xff" * 16, bytes.fromhex("00020003000400050006000700080009"),
                    bytes.fromhex("03000200040005000006000700080009"), b"\x11" * 16):
            self.assertIsNone(fp.smbios_uuid(smbios(raw)), raw.hex())

    def test_no_type_1_or_too_old_or_cut_table_gives_none_not_a_crash(self):
        self.assertIsNone(fp.smbios_uuid(smbios(None)))
        self.assertIsNone(fp.smbios_uuid(smbios(self.RAW, type1_len=0x08)))      # SMBIOS 2.0: no UUID field
        full = smbios(self.RAW)
        for cut in range(0, len(full), 3):
            fp.smbios_uuid(full[:cut])                                           # must not raise
        self.assertIsNone(fp.smbios_uuid(b""))
        self.assertIsNone(fp.smbios_uuid(bytes(8) + b"\x01\x00\x00\x00"))        # structure length 0

    def test_usable_board_uuid(self):
        self.assertTrue(fp.usable_board_uuid(BOARD_A))
        self.assertTrue(fp.usable_board_uuid(BOARD_A.upper()))
        for bad in ("", "not-a-uuid", "00000000-0000-0000-0000-000000000000",
                    "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF", "03000200-0400-0500-0006-000700080009",
                    "01010101-0101-0101-0101-010101010101"):
            self.assertFalse(fp.usable_board_uuid(bad), bad)


class TestDevice(unittest.TestCase):
    def test_hashes_are_one_way_stable_and_ignore_case_and_spaces(self):
        a = fp.from_raw(PC_A)
        self.assertEqual(a, fp.from_raw({k: f"  {v.upper()} " for k, v in PC_A.items()}))
        self.assertEqual(set(a.parts), {"board", "system", "disk"})
        for name, value in a.parts.items():
            self.assertRegex(value, r"^[0-9a-f]{32}$")
            self.assertNotIn(PC_A[name].replace("-", "")[:8], value)
        # the same raw value in two parts gives two different hashes (no cross-matching)
        self.assertNotEqual(fp.part_hash("system", "abc"), fp.part_hash("disk", "abc"))

    def test_junk_board_and_empty_values_are_left_out(self):
        d = fp.from_raw({"board": "00000000-0000-0000-0000-000000000000", "system": "", "disk": "a4f1c2d3"})
        self.assertEqual(set(d.parts), {"disk"})
        self.assertTrue(d.weak)
        self.assertFalse(d.usable)

    def test_usable_means_a_board_or_both_other_parts(self):
        self.assertTrue(fp.from_raw({"board": BOARD_A}).usable)
        self.assertTrue(fp.from_raw({"system": "x1", "disk": "y1"}).usable)
        self.assertFalse(fp.from_raw({"system": "x1"}).usable)
        self.assertFalse(fp.Device({}).usable)

    def test_bad_parts_are_refused(self):
        for parts in ({"cpu": "0" * 32}, {"board": "xyz"}, {"board": "A" * 32}, {"board": 5}):
            with self.assertRaises(fp.FingerprintError):
                fp.Device(parts)
        with self.assertRaises(fp.FingerprintError):
            fp.part_hash("cpu", "x")
        with self.assertRaises(fp.FingerprintError):
            fp.part_hash("disk", "  ")


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.bound = fp.from_raw(PC_A)

    def test_same_pc(self):
        self.assertTrue(fp.matches(self.bound, fp.from_raw(PC_A)))

    def test_windows_reinstalled_and_disk_replaced_is_still_the_same_pc(self):
        self.assertTrue(fp.matches(self.bound, fp.from_raw({"board": BOARD_A, "system": "new-guid", "disk": "ffff0000"})))

    def test_a_disk_image_copied_to_another_pc_is_another_pc(self):
        clone = {**PC_A, "board": BOARD_B}                     # Windows id and disk serial came with the image
        self.assertFalse(fp.matches(self.bound, fp.from_raw(clone)))

    def test_board_unreadable_today_falls_back_to_windows_and_disk_together(self):
        self.assertTrue(fp.matches(self.bound, fp.from_raw({"system": PC_A["system"], "disk": PC_A["disk"]})))
        self.assertFalse(fp.matches(self.bound, fp.from_raw({"system": PC_A["system"], "disk": "ffff0000"})))
        self.assertFalse(fp.matches(self.bound, fp.from_raw({"system": PC_A["system"]})))

    def test_a_licence_without_a_board_needs_both_other_parts(self):
        weak = fp.from_raw({"system": PC_A["system"], "disk": PC_A["disk"]})
        self.assertTrue(fp.matches(weak, fp.from_raw(PC_A)))
        self.assertFalse(fp.matches(weak, fp.from_raw({**PC_A, "disk": "ffff0000"})))
        self.assertFalse(fp.matches(weak, fp.from_raw({"board": BOARD_A})))

    def test_nothing_in_common_never_matches(self):
        self.assertFalse(fp.matches(fp.Device({}), fp.Device({})))
        self.assertFalse(fp.matches(fp.from_raw({"board": BOARD_A}), fp.Device({})))
        self.assertFalse(fp.matches(fp.from_raw({"board": BOARD_A}), fp.from_raw({"system": "x", "disk": "y"})))


class TestReading(unittest.TestCase):
    def test_a_part_that_cannot_be_read_is_absent_never_an_error(self):
        def boom():
            raise OSError("access denied")
        with mock.patch.object(fp.os, "name", "nt"), \
                mock.patch.object(fp, "_windows_firmware_table", boom), \
                mock.patch.object(fp, "_windows_machine_guid", lambda: PC_A["system"]), \
                mock.patch.object(fp, "_windows_volume_serial", lambda: PC_A["disk"]):
            self.assertEqual(fp.raw_identifiers(), {"system": PC_A["system"], "disk": PC_A["disk"]})
            self.assertTrue(fp.current().weak)
            self.assertTrue(fp.current().usable)

    def test_windows_reads_the_board_from_the_firmware_table(self):
        table = smbios(TestSmbios.RAW)
        with mock.patch.object(fp.os, "name", "nt"), \
                mock.patch.object(fp, "_windows_firmware_table", lambda: table), \
                mock.patch.object(fp, "_windows_machine_guid", lambda: PC_A["system"]), \
                mock.patch.object(fp, "_windows_volume_serial", lambda: PC_A["disk"]):
            self.assertEqual(fp.current(), fp.from_raw(PC_A))

    def test_current_never_raises_on_this_machine(self):
        self.assertIsInstance(fp.current(), fp.Device)

    @unittest.skipUnless(os.name == "nt", "Windows only: the real registry and firmware calls")
    def test_real_windows_identifiers_are_read_and_stable(self):
        first, second = fp.raw_identifiers(), fp.raw_identifiers()
        self.assertEqual(first, second)
        self.assertIn("system", first)                         # MachineGuid exists on every Windows
        self.assertRegex(first["disk"], r"^[0-9a-f]{8}$")
        self.assertTrue(fp.current().usable)


class TestActivationRequest(unittest.TestCase):
    def setUp(self):
        self.device = fp.from_raw(PC_A)
        self.text = fp.encode_request(self.device, "CASHIER-PC")

    def test_round_trip(self):
        self.assertRegex(self.text, r"^PHREQ1-[A-Z2-7]+-[0-9A-F]{8}$")
        self.assertEqual(fp.decode_request(self.text), (self.device, "CASHIER-PC"))

    def test_it_survives_a_chat_app_lower_case_spaces_and_line_breaks(self):
        mangled = "  " + "\n".join(self.text[i:i + 30] for i in range(0, len(self.text), 30)).lower() + " \n"
        self.assertEqual(fp.decode_request(mangled)[0], self.device)

    def test_arabic_pc_name_and_default_label(self):
        text = fp.encode_request(self.device, "جهاز الكاشير")
        self.assertEqual(fp.decode_request(text)[1], "جهاز الكاشير")
        self.assertLessEqual(len(fp.decode_request(fp.encode_request(self.device, "x" * 100))[1]), 40)
        self.assertIsInstance(fp.decode_request(fp.encode_request(self.device))[1], str)

    def test_a_mistyped_or_cut_request_is_refused(self):
        body = self.text.split("-")[1]
        swapped = self.text.replace(body, ("B" if body[0] != "B" else "C") + body[1:])
        for bad in (self.text[:-1], self.text[:-9], swapped, "PHREQ2" + self.text[6:], "hello", "",
                    self.text.replace("PHREQ1-", "PHREQ1-1")):
            with self.assertRaises(fp.FingerprintError, msg=bad):
                fp.decode_request(bad)

    def test_a_pc_that_cannot_be_identified_gets_no_request(self):
        with self.assertRaises(fp.FingerprintError):
            fp.encode_request(fp.from_raw({"system": "only-this"}))

    def test_the_request_holds_hashes_only_never_the_raw_identifiers(self):
        import base64
        body = self.text.split("-")[1]
        decoded = base64.b32decode(body + "=" * (-len(body) % 8)).decode("utf-8")
        for raw in PC_A.values():
            self.assertNotIn(raw, decoded)


if __name__ == "__main__":
    unittest.main()
