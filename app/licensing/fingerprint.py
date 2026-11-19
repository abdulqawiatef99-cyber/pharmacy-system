"""Device fingerprint: which PC a licence belongs to.

Three facts about the PC, each turned into a one-way hash (the raw identifiers never leave the PC):

  board   the motherboard's own identifier (SMBIOS system UUID). Survives a Windows reinstall and a new
          disk; a disk image copied to another PC does NOT carry it. The strongest part.
  system  the Windows installation's identifier (MachineGuid). Changes when Windows is reinstalled;
          a disk image copied to another PC DOES carry it.
  disk    the serial number of the Windows volume. Changes on a format; a disk image carries it.

Matching rule (`matches`):
- the licence and this PC both know a board identifier: that alone decides. Same board = same PC
  (Windows may be reinstalled freely); another board = another PC, even if the whole disk was copied.
- otherwise (a cheap board with no usable identifier, or it could not be read today): the Windows
  installation AND the disk must both match. This is weaker (a disk image passes), so a request from
  such a PC is marked `weak` and the publisher sees that before issuing.

Limits, stated plainly: a virtual machine can be given any board identifier, and whoever edits the
program can remove the check. The fingerprint stops casual copying, not a determined engineer.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
from dataclasses import dataclass, field

from app.core.signed_envelope import canonical_json, strict_json

PARTS = ("board", "system", "disk")
PRODUCT = "pharmacy-platform"
_HASH = re.compile(r"[0-9a-f]{32}")
# identifiers that many boards share (factory defaults): useless for telling two PCs apart
_JUNK_UUIDS = {
    "00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "03000200-0400-0500-0006-000700080009", "00020003-0004-0005-0006-000700080009",
    "12345678-1234-5678-90ab-cddeefaabbcc", "fefefefe-fefe-fefe-fefe-fefefefefefe",
    "11111111-1111-1111-1111-111111111111",
    "01234567-89ab-cdef-0123-456789abcdef", "88888888-8888-8888-8888-888888888888",
}
REQUEST_PREFIX = "PHREQ1"


class FingerprintError(ValueError):
    pass


def part_hash(part: str, raw: str) -> str:
    """One-way, product-specific hash of one identifier (32 hex characters)."""
    if part not in PARTS:
        raise FingerprintError(f"unknown part: {part}")
    norm = re.sub(r"\s+", "", str(raw)).lower()
    if not norm:
        raise FingerprintError(f"empty {part} identifier")
    return hashlib.sha256(f"{PRODUCT}/device/v1\n{part}\n{norm}".encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Device:
    """Hashed identifiers of one PC. `parts` maps a name in PARTS to a 32-hex hash."""
    parts: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.parts, dict) or not set(self.parts) <= set(PARTS):
            raise FingerprintError("unknown device part")
        for v in self.parts.values():
            if not isinstance(v, str) or not _HASH.fullmatch(v):
                raise FingerprintError("a device part is 32 lowercase hex characters")

    @property
    def weak(self) -> bool:
        """No board identifier: a copied disk image would pass as this PC."""
        return "board" not in self.parts

    @property
    def usable(self) -> bool:
        """Enough to bind a licence to: a board, or both the Windows installation and the disk."""
        return "board" in self.parts or {"system", "disk"} <= set(self.parts)

    def as_json(self) -> dict:
        return {k: self.parts[k] for k in PARTS if k in self.parts}


def from_raw(raw: dict) -> Device:
    """Device from raw identifiers ({"board": "...", ...}); missing or junk values are left out."""
    parts = {}
    for name in PARTS:
        value = raw.get(name)
        if value is None or not str(value).strip():
            continue
        if name == "board" and not usable_board_uuid(str(value)):
            continue
        parts[name] = part_hash(name, str(value))
    return Device(parts)


def matches(bound: Device, current: Device) -> bool:
    """Is `current` (this PC) the PC the licence was issued for (`bound`)? See the module text."""
    b, c = bound.parts, current.parts
    if "board" in b and "board" in c:
        return _same(b["board"], c["board"])
    return all(p in b and p in c and _same(b[p], c[p]) for p in ("system", "disk"))


def _same(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


# --- reading the identifiers -------------------------------------------------------------------------

def usable_board_uuid(text: str) -> bool:
    t = text.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", t) or t in _JUNK_UUIDS:
        return False
    digits = t.replace("-", "")
    return len(set(digits)) > 2                      # 0000…, ffff…, 0101… are factory fillers


def smbios_uuid(table: bytes) -> str | None:
    """The system UUID out of a raw SMBIOS table (what Windows returns for the 'RSMB' firmware table,
    including its 8-byte header). None when the table has no usable type-1 structure.

    Layout: header = 4 single bytes then a 4-byte little-endian length of the table data; each structure
    = type (1), length of its formatted part (1), handle (2), formatted part, then its strings, ended by
    two zero bytes. The UUID is 16 bytes at offset 8 of the type-1 structure (SMBIOS 2.1+)."""
    if len(table) < 8:
        return None
    size = int.from_bytes(table[4:8], "little")
    data = table[8:8 + size]
    i = 0
    while i + 4 <= len(data):
        kind, length = data[i], data[i + 1]
        if length < 4 or i + length > len(data):
            return None
        if kind == 1 and length >= 0x18:
            raw = data[i + 8:i + 24]
            # the first three fields are stored little-endian (SMBIOS 2.6+): show it the way Windows does
            text = "%s-%s-%s-%s-%s" % (raw[3::-1].hex(), raw[5:3:-1].hex(), raw[7:5:-1].hex(),
                                       raw[8:10].hex(), raw[10:16].hex())
            big_endian = "%s-%s-%s-%s-%s" % (raw[0:4].hex(), raw[4:6].hex(), raw[6:8].hex(),
                                             raw[8:10].hex(), raw[10:16].hex())
            return text if usable_board_uuid(text) and usable_board_uuid(big_endian) else None
        if kind == 127:                              # end-of-table marker
            return None
        j = i + length                               # skip the strings: up to the double zero
        while j + 1 < len(data) and (data[j] or data[j + 1]):
            j += 1
        i = j + 2
    return None


def _windows_firmware_table() -> bytes:
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.kernel32.GetSystemFirmwareTable
    fn.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    fn.restype = wintypes.UINT
    provider = int.from_bytes(b"RSMB", "big")
    size = fn(provider, 0, None, 0)
    if not size:
        raise OSError("no SMBIOS table")
    buf = ctypes.create_string_buffer(size)
    got = fn(provider, 0, buf, size)
    if not got or got > size:
        raise OSError("SMBIOS table could not be read")
    return buf.raw[:got]


def _windows_machine_guid() -> str:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        return str(winreg.QueryValueEx(key, "MachineGuid")[0])


def _windows_volume_serial() -> str:
    import ctypes
    from ctypes import wintypes
    root = os.environ.get("SystemDrive", "C:") + "\\"
    serial = wintypes.DWORD(0)
    fn = ctypes.windll.kernel32.GetVolumeInformationW
    fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                   ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    fn.restype = wintypes.BOOL
    if not fn(root, None, 0, ctypes.byref(serial), None, None, None, 0) or not serial.value:
        raise OSError("volume serial could not be read")
    return f"{serial.value:08x}"


def _read_file(path: str) -> str:
    with open(path, encoding="ascii", errors="ignore") as f:
        return f.read().strip()


def raw_identifiers() -> dict:
    """Raw identifiers of this PC; a part that cannot be read is simply absent (never an exception:
    a failed read must not stop a pharmacy from opening)."""
    if os.name == "nt":
        readers = {"board": lambda: smbios_uuid(_windows_firmware_table()),
                   "system": _windows_machine_guid, "disk": _windows_volume_serial}
    else:                                            # developer machines and CI only
        readers = {"board": lambda: _read_file("/sys/class/dmi/id/product_uuid"),
                   "system": lambda: _read_file("/etc/machine-id")}
    raw = {}
    for name, read in readers.items():
        try:
            value = read()
        except Exception:  # noqa: BLE001 - registry, firmware and file errors differ per Windows version
            continue
        if value:
            raw[name] = value
    return raw


def current() -> Device:
    return from_raw(raw_identifiers())


# --- activation request: what the customer sends to get a licence --------------------------------------

def encode_request(device: Device, label: str | None = None) -> str:
    """One line of text the customer copies to the publisher: PHREQ1-<data>-<check>.
    Holds only the hashed parts and a label (the PC's name) so the publisher knows which PC it is."""
    if not device.usable:
        raise FingerprintError("this PC could not be identified well enough for a licence")
    label = (label if label is not None else socket.gethostname() or "")[:40]
    body = canonical_json({"v": 1, "product": PRODUCT, "parts": device.as_json(), "label": label})
    data = base64.b32encode(body).decode("ascii").rstrip("=")
    return f"{REQUEST_PREFIX}-{data}-{hashlib.sha256(body).hexdigest()[:8].upper()}"


def decode_request(text: str) -> tuple[Device, str]:
    """(device, label) out of a request line; refuses a mistyped or cut line (the check at the end)."""
    compact = re.sub(r"\s+", "", str(text)).upper()
    m = re.fullmatch(rf"{REQUEST_PREFIX}-([A-Z2-7]+)-([0-9A-F]{{8}})", compact)
    if not m:
        raise FingerprintError("not an activation request")
    try:
        body = base64.b32decode(m.group(1) + "=" * (-len(m.group(1)) % 8))
    except ValueError:
        raise FingerprintError("damaged activation request") from None
    if hashlib.sha256(body).hexdigest()[:8].upper() != m.group(2):
        raise FingerprintError("damaged activation request (check the copied text)")
    try:
        value = strict_json(body)
    except ValueError:
        raise FingerprintError("damaged activation request") from None
    if not isinstance(value, dict) or set(value) != {"v", "product", "parts", "label"} or value["v"] != 1 \
            or value["product"] != PRODUCT or not isinstance(value["label"], str) or len(value["label"]) > 40:
        raise FingerprintError("activation request of another program or version")
    device = Device(value["parts"]) if isinstance(value["parts"], dict) else None
    if device is None or not device.usable:
        raise FingerprintError("activation request without a usable device")
    return device, value["label"]
