"""Licence file: format, verification and the decision "may the program run on this PC today".

A licence is a signed envelope (app/core/signed_envelope.py, purpose "license") around this JSON:

    {"format": 1, "product": "pharmacy-platform",
     "license_id": "PH-20261119-7F3A9C",          unique; the publisher's ledger and revocation use it
     "customer": "صيدلية ...",                     shown on the licence screen
     "kind": "perpetual" | "subscription" | "trial",
     "issued_at": "2026-11-19T12:30:00Z",
     "starts_at": "2026-11-19T00:00:00Z",
     "expires_at": "2027-11-19T23:59:59Z" | null,  null only for perpetual
     "grace_days": 14,                             full use after the end date, with a warning
     "updates_until": "2027-11-19T23:59:59Z" | null,   perpetual + paid updates: last release date covered
     "devices": [{"board": "…", "system": "…", "disk": "…"}],   hashed (fingerprint.py); 1 to 10 PCs
     "features": []}                               optional extras, by name

Rules a licence must satisfy to be read at all (the publisher's tool enforces the same ones):
- perpetual and subscription licences are always tied to at least one PC;
- only a trial may be untied, and a trial lasts at most 45 days;
- a subscription or trial always has an end date; a perpetual licence never has one.

All times are UTC. Nothing here talks to the network, the database or the screen.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core import signed_envelope as se

from .fingerprint import PRODUCT, Device, FingerprintError, matches

PURPOSE = "license"
FORMAT = 1
KINDS = ("perpetual", "subscription", "trial")
MAX_DEVICES = 10
MAX_GRACE_DAYS = 90
MAX_TRIAL_DAYS = 45
MAX_LICENSE_BYTES = 64 * 1024
WARN_DAYS = 14
CLOCK_TOLERANCE = timedelta(hours=26)      # a time-zone fix or a summer-time change is not a rollback
_ID = re.compile(r"[A-Z0-9][A-Z0-9-]{5,39}")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_FEATURE = re.compile(r"[a-z][a-z0-9_.-]{0,39}")
_FIELDS = {"format", "product", "license_id", "customer", "kind", "issued_at", "starts_at", "expires_at",
           "grace_days", "updates_until", "devices", "features"}


class LicenseError(ValueError):
    """The file is not a licence this program accepts. `code`: malformed | wrong_purpose | no_trusted_key |
    untrusted_key | bad_signature | unsupported_format | wrong_product | invalid_terms."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def parse_time(text) -> datetime:
    if not isinstance(text, str) or not _TIME.fullmatch(text):
        raise LicenseError("malformed", "a time is written YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise LicenseError("malformed", f"not a real time: {text}") from None


def format_time(t: datetime) -> str:
    return _utc(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError("a time without a time zone is ambiguous: pass an aware datetime")
    return t.astimezone(timezone.utc)


@dataclass(frozen=True)
class License:
    license_id: str
    customer: str
    kind: str
    issued_at: datetime
    starts_at: datetime
    expires_at: datetime | None
    grace_days: int
    updates_until: datetime | None
    devices: tuple[Device, ...]
    features: tuple[str, ...]
    key_ids: tuple[str, ...] = ()

    def payload(self) -> dict:
        return {"format": FORMAT, "product": PRODUCT, "license_id": self.license_id, "customer": self.customer,
                "kind": self.kind, "issued_at": format_time(self.issued_at), "starts_at": format_time(self.starts_at),
                "expires_at": format_time(self.expires_at) if self.expires_at else None,
                "grace_days": self.grace_days,
                "updates_until": format_time(self.updates_until) if self.updates_until else None,
                "devices": [d.as_json() for d in self.devices], "features": list(self.features)}

    def covers_release(self, released_at: datetime) -> bool:
        """Perpetual licence with paid updates: is a release of this date still covered?"""
        return self.updates_until is None or _utc(released_at) <= self.updates_until


def from_payload(value) -> License:
    """Strict reading of the signed JSON; any surprise is a refusal, never a guess."""
    if not isinstance(value, dict):
        raise LicenseError("malformed", "not a licence")
    if type(value.get("format")) is not int or value["format"] != FORMAT:
        raise LicenseError("unsupported_format", "this licence needs a newer program")
    if value.get("product") != PRODUCT:
        raise LicenseError("wrong_product", "this licence is for another program")
    if set(value) != _FIELDS:
        raise LicenseError("malformed", "unexpected or missing fields")
    lid, customer, kind = value["license_id"], value["customer"], value["kind"]
    if not isinstance(lid, str) or not _ID.fullmatch(lid):
        raise LicenseError("malformed", "bad licence id")
    if not isinstance(customer, str) or not customer.strip() or len(customer) > 120 or customer != customer.strip() \
            or any(ord(c) < 32 for c in customer):
        raise LicenseError("malformed", "bad customer name")
    if kind not in KINDS:
        raise LicenseError("malformed", "unknown licence kind")
    issued, starts = parse_time(value["issued_at"]), parse_time(value["starts_at"])
    expires = None if value["expires_at"] is None else parse_time(value["expires_at"])
    updates = None if value["updates_until"] is None else parse_time(value["updates_until"])
    grace = value["grace_days"]
    if type(grace) is not int or not 0 <= grace <= MAX_GRACE_DAYS:
        raise LicenseError("malformed", "bad grace period")
    devices_raw, features = value["devices"], value["features"]
    if not isinstance(devices_raw, list) or len(devices_raw) > MAX_DEVICES:
        raise LicenseError("malformed", "bad device list")
    try:
        devices = tuple(Device(dict(d)) if isinstance(d, dict) else _bad_device() for d in devices_raw)
    except FingerprintError as e:
        raise LicenseError("malformed", str(e)) from None
    if not isinstance(features, list) or len(features) > 50 or len(set(map(str, features))) != len(features) \
            or not all(isinstance(f, str) and _FEATURE.fullmatch(f) for f in features):
        raise LicenseError("malformed", "bad feature list")
    lic = License(lid, customer, kind, issued, starts, expires, grace, updates, devices, tuple(features))
    check_terms(lic)
    return lic


def _bad_device():
    raise FingerprintError("a device is an object of hashed parts")


def check_terms(lic: License) -> None:
    """The business rules of a licence (same check when issuing and when reading)."""
    def bad(why: str):
        raise LicenseError("invalid_terms", why)
    for d in lic.devices:
        if not d.usable:
            bad("a device needs a board identifier, or both the Windows and the disk identifiers")
    if len({tuple(sorted(d.parts.items())) for d in lic.devices}) != len(lic.devices):
        bad("the same device is listed twice")
    if lic.kind == "perpetual":
        if lic.expires_at is not None:
            bad("a perpetual licence has no end date")
        if lic.grace_days:
            bad("a perpetual licence has no grace period")
    else:
        if lic.expires_at is None:
            bad(f"a {lic.kind} licence needs an end date")
        if lic.expires_at <= lic.starts_at:
            bad("the end date is not after the start date")
        if lic.updates_until is not None:
            bad("updates_until is only for perpetual licences")
    if lic.kind == "trial":
        if lic.expires_at - lic.starts_at > timedelta(days=MAX_TRIAL_DAYS):
            bad(f"a trial lasts at most {MAX_TRIAL_DAYS} days")
    elif not lic.devices:
        bad("only a trial may be untied from a PC")


def verify(data: bytes, trusted_keys) -> License:
    """Licence out of a licence file's bytes, only if a built-in publisher key signed it."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_LICENSE_BYTES:
        raise LicenseError("malformed", "not a licence file")
    try:
        payload = se.open_envelope(bytes(data), PURPOSE, trusted_keys)
        value = se.strict_json(payload)
    except se.EnvelopeError as e:
        raise LicenseError(e.code, str(e).partition(": ")[2]) from None
    return from_payload(value)


def issue(lic: License, *private_keys) -> bytes:
    """Publisher side: the licence file's bytes. Refuses terms the program itself would refuse."""
    check_terms(lic)
    checked = from_payload(lic.payload())            # exactly what a customer PC will read
    return se.sign(se.canonical_json(checked.payload()), PURPOSE, *private_keys)


# --- the decision -------------------------------------------------------------------------------------

STATES = ("valid", "grace", "expired", "not_yet_valid", "wrong_device", "revoked", "invalid", "missing")
_MESSAGES = {
    "valid": "الترخيص ساري.",
    "grace": "مدة الترخيص انتهت. البرنامج شغال في فترة السماح؛ جدّد الترخيص قبل ما تنتهي.",
    "expired": "مدة الترخيص وفترة السماح انتهوا. بياناتك محفوظة؛ جدّد الترخيص علشان تكمل.",
    "not_yet_valid": "الترخيص لسه ما بدأش. راجع تاريخ ووقت الجهاز.",
    "wrong_device": "الترخيص ده صادر لجهاز تاني. اطلب ترخيص للجهاز ده.",
    "revoked": "الترخيص ده اتلغى. تواصل مع الدعم.",
    "invalid": "ملف الترخيص مش سليم أو مش صادر من ناشر البرنامج.",
    "missing": "مفيش ترخيص على الجهاز ده.",
}


@dataclass(frozen=True)
class Status:
    state: str                      # one of STATES
    usable: bool                    # the program may work fully (valid or grace)
    message: str                    # short Arabic line, ready to show
    license: License | None = None
    reason: str = ""                # stable code for logs (an error code, or the state)
    days_left: int | None = None    # until the end date; None for perpetual or when there is no decision
    grace_days_left: int | None = None
    warn: bool = False              # show a reminder: ending soon, in grace, or the clock went back
    clock_back: bool = False        # the PC clock is behind the latest time the program has seen


def status(state: str, *, license: License | None = None, reason: str = "", **more) -> Status:
    return Status(state=state, usable=state in ("valid", "grace"), message=_MESSAGES[state], license=license,
                  reason=reason or state, **more)


def evaluate(lic: License, *, now: datetime, device: Device, high_water: datetime | None = None,
             revoked=frozenset()) -> Status:
    """May the program run on `device` at `now`?

    `high_water` is the latest time the program has already seen on this PC (clock.py, and any dated
    record in the pharmacy's own data). Time never runs backwards for a licence: the decision uses the
    later of `now` and `high_water`, so turning the PC clock back buys nothing, and a PC whose clock
    reset itself (dead battery) keeps working exactly as long as the licence was really valid."""
    now = _utc(now)
    seen = _utc(high_water) if high_water is not None else None
    clock_back = seen is not None and seen - now > CLOCK_TOLERANCE
    effective = max(now, seen) if seen is not None else now
    if lic.license_id in revoked:
        return status("revoked", license=lic, clock_back=clock_back)
    if lic.devices and not any(matches(d, device) for d in lic.devices):
        return status("wrong_device", license=lic, clock_back=clock_back)
    if effective < lic.starts_at - CLOCK_TOLERANCE:
        return status("not_yet_valid", license=lic, clock_back=clock_back, warn=True)
    if lic.expires_at is None:
        return status("valid", license=lic, clock_back=clock_back, warn=clock_back)
    left = lic.expires_at - effective
    days_left = math.ceil(left.total_seconds() / 86400)
    if left >= timedelta(0):
        return status("valid", license=lic, days_left=days_left, clock_back=clock_back,
                      warn=clock_back or days_left <= WARN_DAYS)
    grace_left = lic.expires_at + timedelta(days=lic.grace_days) - effective
    if grace_left >= timedelta(0):
        return status("grace", license=lic, days_left=days_left, clock_back=clock_back, warn=True,
                      grace_days_left=math.ceil(grace_left.total_seconds() / 86400))
    return status("expired", license=lic, days_left=days_left, grace_days_left=0, clock_back=clock_back, warn=True)
