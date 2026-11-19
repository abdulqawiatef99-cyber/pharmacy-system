"""Publisher public keys built into the program: the only keys whose licences this build accepts.

Each entry is an Ed25519 PUBLIC key, 64 lowercase hex characters. Public keys are not secret.
The matching private keys never enter this repository, CI logs or a customer PC
(docs/PROTECTION_PLAN.md, "المفاتيح"). They are made with:

    python scripts/license/issue_license.py keygen --out <a path outside the repository>

Keep two entries from the first day: the key in use and a spare kept offline and never used. If the
key in use is lost or leaks, licences signed with the spare still open on every installed program, and
the next update drops the old key.

EMPTY ON PURPOSE until the owner's key ceremony: with no key here every licence is refused
(`no_trusted_key`). That is safe today because nothing in the running program asks for a licence yet.
"""
from __future__ import annotations

PUBLISHER_KEYS: tuple[str, ...] = ()
