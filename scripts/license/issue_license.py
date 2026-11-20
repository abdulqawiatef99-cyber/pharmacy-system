#!/usr/bin/env python3
"""Publisher's tool: make signing keys and issue licences. Runs on the owner's PC only, never on a
customer PC and never in CI. The private key is read from a path OUTSIDE the repository.

  keygen  --out E:\\keys\\license-1.key           make a key; prints the public key to build into the program
  public  --key E:\\keys\\license-1.key           print the public key and its id again
  request "PHREQ1-...."                           show what an activation request says (no key needed)
  issue   --key ... --request "PHREQ1-...." --customer "صيدلية ..." --kind subscription --days 365
          [--request ...more PCs] [--grace-days 14] [--starts 2026-12-01] [--expires 2027-12-01]
          [--updates-until 2027-12-01] [--feature NAME] [--out license.lic] [--ledger FILE]
  show    license.lic [--public-key HEX]          read a licence file; checks the signature when a key is given

Kinds: perpetual (no end date; the owner's own pharmacy), subscription (end date + grace), trial
(at most 45 days; the only kind that may be issued without --request, i.e. not tied to a PC).

Every issued licence is added to a ledger file next to the key (issued-licenses.jsonl): who, which PCs,
until when. The ledger is the publisher's record for renewals and for cancelling a licence later.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyfile  # noqa: E402  (puts the repository root on sys.path)

from app.core import signed_envelope as se  # noqa: E402
from app.licensing import fingerprint, license as lic_mod  # noqa: E402

LEDGER_NAME = "issued-licenses.jsonl"


def _day(text: str, end: bool = False) -> datetime:
    """A date (YYYY-MM-DD) as UTC: the start of the day, or its last second for an end date."""
    try:
        d = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit(f"bad date (write YYYY-MM-DD): {text}") from None
    return d + timedelta(days=1, seconds=-1) if end else d


def new_license_id(now: datetime) -> str:
    return f"PH-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


def read_ledger(path: Path) -> list[dict]:
    """The publisher's record of issued licences. A line that cannot be read stops the tool: issuing on
    top of a damaged record would hide a PC that is already licensed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if not isinstance(row, dict):
            raise keyfile.KeyFileError(f"the ledger {path} is damaged at line {n}; repair it before issuing")
        rows.append(row)
    return rows


def other_customers(rows: list[dict], licence: lic_mod.License) -> set[tuple[str, str]]:
    """(customer, licence id) of every recorded licence of ANOTHER customer on one of this licence's PCs.
    "The same PC" is judged by the licence's own rule (fingerprint.matches), in both directions: a PC whose
    Windows was reinstalled, or whose board could not be read this time, is still that PC."""
    found = set()
    for row in rows:
        if row.get("customer") == licence.customer:
            continue
        for parts in row.get("devices") or []:
            try:
                recorded = fingerprint.Device(dict(parts))
            except (fingerprint.FingerprintError, TypeError, ValueError):
                continue
            if any(fingerprint.matches(recorded, d) or fingerprint.matches(d, recorded) for d in licence.devices):
                found.add((str(row.get("customer")), str(row.get("license_id"))))
    return found


def build(a, now: datetime) -> tuple[lic_mod.License, list[str]]:
    devices, labels = [], []
    for text in a.request or []:
        device, label = fingerprint.decode_request(text)
        devices.append(device)
        labels.append(label + (" (weak: no board id)" if device.weak else ""))
    starts = _day(a.starts) if a.starts else now.replace(hour=0, minute=0, second=0, microsecond=0)
    if a.kind == "perpetual":
        if a.expires or a.days:
            raise SystemExit("a perpetual licence has no end date (no --days / --expires)")
        expires, grace = None, 0
    else:
        if bool(a.expires) == bool(a.days):
            raise SystemExit(f"a {a.kind} licence needs exactly one of --days or --expires")
        expires = _day(a.expires, end=True) if a.expires else starts + timedelta(days=a.days, seconds=-1)
        grace = a.grace_days if a.grace_days is not None else (14 if a.kind == "subscription" else 0)
    updates = _day(a.updates_until, end=True) if a.updates_until else None
    licence = lic_mod.License(license_id=new_license_id(now), customer=a.customer.strip(), kind=a.kind,
                              issued_at=now.replace(microsecond=0), starts_at=starts, expires_at=expires,
                              grace_days=grace, updates_until=updates, devices=tuple(devices),
                              features=tuple(a.feature or []))
    lic_mod.check_terms(licence)
    return licence, labels


def cmd_keygen(a) -> int:
    public_hex = keyfile.create(Path(a.out))
    print(f"private key written: {Path(a.out).expanduser().resolve()}")
    print("KEEP IT OFFLINE (encrypted USB + a second copy in another place). Never e-mail, upload or commit it.")
    print(f"public key (build into the program, app/licensing/keys.py): {public_hex}")
    print(f"key id: {se.key_id(public_hex)}")
    return 0


def cmd_public(a) -> int:
    key = keyfile.load(Path(a.key))
    print(f"public key: {se.public_key_bytes(key).hex()}")
    print(f"key id: {se.key_id(key)}")
    return 0


def cmd_request(a) -> int:
    device, label = fingerprint.decode_request(a.text)
    print(f"label: {label}")
    print(f"parts: {', '.join(device.as_json())}")
    print("binding: " + ("WEAK (no board identifier: a copied disk image would pass)" if device.weak else "board"))
    return 0


def cmd_issue(a) -> int:
    now = datetime.now(timezone.utc)
    key_path = keyfile.outside_any_repository(Path(a.key))
    out = keyfile.outside_any_repository(Path(a.out))       # a customer's licence is not project source
    if out.exists():
        raise SystemExit(f"refused: {out} already exists")
    licence, labels = build(a, now)
    ledger = Path(a.ledger).expanduser().resolve() if a.ledger else key_path.parent / LEDGER_NAME
    keyfile.outside_any_repository(ledger)
    for customer, lid in sorted(other_customers(read_ledger(ledger), licence)):
        print(f"WARNING: this PC already has a licence issued to another customer: {customer} ({lid})",
              file=sys.stderr)
    key = keyfile.load(key_path)
    data = lic_mod.issue(licence, key)
    row = {**licence.payload(), "labels": labels, "key": se.key_id(key), "file": out.name}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as f:          # the record first: a licence never exists unrecorded
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "xb") as f:
        f.write(data)
    print(f"licence {licence.license_id} for {licence.customer}: {licence.kind}, "
          + (f"until {lic_mod.format_time(licence.expires_at)} (+{licence.grace_days} days grace)"
             if licence.expires_at else "no end date")
          + f", {len(licence.devices)} PC(s)")
    print(f"file: {out}")
    print(f"ledger: {ledger}")
    return 0


def cmd_show(a) -> int:
    data = Path(a.file).read_bytes()
    if a.public_key:
        licence = lic_mod.verify(data, [a.public_key])
        print("signature: OK")
    else:
        import base64
        import binascii
        try:
            licence = lic_mod.from_payload(se.strict_json(base64.b64decode(se.strict_json(data)["payload"])))
        except (KeyError, TypeError, binascii.Error):
            raise lic_mod.LicenseError("malformed", "not a licence file") from None
        print("signature: NOT CHECKED (give --public-key)")
    print(json.dumps(licence.payload(), ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True)
    pb = sub.add_parser("public")
    pb.add_argument("--key", required=True)
    rq = sub.add_parser("request")
    rq.add_argument("text")
    i = sub.add_parser("issue")
    i.add_argument("--key", required=True, help="private key file, outside the repository")
    i.add_argument("--request", action="append", help="activation request text of one PC (repeat for more PCs)")
    i.add_argument("--customer", required=True)
    i.add_argument("--kind", choices=lic_mod.KINDS, required=True)
    i.add_argument("--days", type=int)
    i.add_argument("--starts", help="YYYY-MM-DD (default: today)")
    i.add_argument("--expires", help="YYYY-MM-DD (last day included)")
    i.add_argument("--grace-days", type=int)
    i.add_argument("--updates-until", help="YYYY-MM-DD, perpetual licences with paid updates")
    i.add_argument("--feature", action="append")
    i.add_argument("--out", default="license.lic")
    i.add_argument("--ledger", help=f"default: {LEDGER_NAME} next to the key")
    s = sub.add_parser("show")
    s.add_argument("file")
    s.add_argument("--public-key", help="64 hex characters")
    a = p.parse_args(argv)
    try:
        return {"keygen": cmd_keygen, "public": cmd_public, "request": cmd_request, "issue": cmd_issue,
                "show": cmd_show}[a.cmd](a)
    except (keyfile.KeyFileError, fingerprint.FingerprintError, lic_mod.LicenseError, se.EnvelopeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
