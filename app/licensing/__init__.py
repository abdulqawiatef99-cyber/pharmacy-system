"""Per-device signed licences (docs/PROTECTION_PLAN.md, layer 4).

A library with tests. Nothing in the running program imports it yet: wiring it into the launcher and
the screens is a later change that ships with an installer, after the owner's key ceremony.

  fingerprint.py   which PC this is (hashed identifiers) and the activation request text
  license.py       the licence format, signature check and the decision for a PC at a time
  clock.py         the latest time seen, against turning the PC clock back
  service.py       the licence file in the data folder: check, install
  keys.py          the publisher's public keys built into the program (empty until the key ceremony)

The publisher's side (making keys, issuing licences) is scripts/license/issue_license.py.
"""
