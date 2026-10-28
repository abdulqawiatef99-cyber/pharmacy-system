# Unified local review candidate — 2026-10-28

Branch: codex/local-release-candidate. This is a review composition of existing changes, not a main merge, production release or a new pharmacy system. Base main: 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0.

## Sources preserved

| Source | Commit | Included change |
|---|---|---|
| PR #1 review | 9a307266ae7928465b6e7fe496ee517be1f5bde8 | Four access guard tests and collaboration instructions; other historical reviews remain on review branch |
| PR #2 | ea5a3034b2da57e46d547e40f0f4f7faf63e9d96 | Cash replay transaction helper and visibility guard |
| PR #3 | 100600dca808fd1ca48b2f12f9612a754312c385 | Backup attachment verification |
| PR #4 | fc720b3cf282bf7613c7f267dc4781ccf6a6de88 | Code release staging, pointer selection and supervised first-start rollback |
| PR #5 | 33bd8d8d98d1dc04384452ca54e85819ddf2f360 | Atomic customer collection and fresh authorization |
| PR #6 | df53b45c9d56f3d0f627b418d7eab55ca816cac1 | Purchase-to-shift HTTP cycle and reopen checks |
| PR #7 | 547c7208846fda3574dbed877a98b7bafb971c07 | Inventory batch/item/tenant boundary guard |
| PR #8 | 279f6567e98eeb2f64bf8825ef39f9c1558fb1af | Whole-number input validation |
| PR #9 | 54cea823999ca617f6ff857585c7d368444b61d8 | Portable isolated check command and first-release scope |

app/api/app.py was reconciled explicitly: retain cash movement transaction/redaction, customer collection transaction/permission, startup health nonce, and whole-number parsing. No new schema, requirements, pricing formula, dispensing rule or automatic cleanup of historical data.

## Owner review and Claude continuation

This cumulative candidate overlaps the individual PRs. Choose either this cumulative change or individually reconcile their diffs; do not reapply identical changes as new work. Do not merge any branch automatically. The history of the source PRs remains available; the candidate records provenance in this table.

Use docs/FIRST_RELEASE_SCOPE.md as the finite launch checklist. Next gate is Windows verification on a permitted isolated environment, then installer/bootstrap adoption, hardware, restore acceptance and an owner-approved operating day. If a candidate defect blocks a gate, repair only that bounded defect and rerun affected checks. No open-ended enhancements or new modules during this gate.

The update protocol currently supports code-only changes with unchanged requirements/SQL migrations. Initial adoption needs reviewed installer/application stopped. Never bootstrap through the unsafe old in-place updater. Automatic DB rollback is intentionally absent to avoid discarding newer transactions.

No live E-Stock or patient data, actual printer, scanner, cash drawer, Windows host or production deployment was accessed. All tests/DBs are synthetic. Overall completion percentage is not derived from test count.

## Validation

Linux/Python 3.12.14: 447 tests passed, zero failures/skips, 39.905 seconds. The validator then completed selftest --quick, synthetic init and verify; all four phases exited zero. windows_hardware_accepted=false.

Log SHA256:
report.json 17351f402fbfd4b115b634814da25c3c54dbdd3db3d9bab762d01648c607410d
tests.log 2c8297fd342e7657e26fa8553652f5c1ffd96022fbebcd376ec1c253e820291d
selftest.log d81a101caa3b684d38f55b9b504b5914b47882d01dbfcc26a3717a75dbe84bc6
init.log 780d2395d1a6a50829568fa29bd479b92a0bb586bbc681fea908672d78816bb0
verify.log a12b7cb43c9d9134b5bb1b35e9096b66775d9e92e7611d1cc92b02edd6782a87
