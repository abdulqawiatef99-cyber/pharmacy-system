# Reject fractional integer inputs — 2026-10-28

Branch: codex/reject-fractional-integers. Baseline main 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0. Independent safe validation repair; no schema or pricing formula change.

## Confirmed failure

Catalog integer fields used int(value), and the API _int helper used int(value or 0). JSON numeric fractions were truncated silently: pack_size 2.5 became 2, public_price 4000.9 became 4000 piastres, cash opening 100.9 became 100. Booleans were also accepted as quantities. Four initial regression cases failed before repair; the whole-number compatibility case passed. This is malformed-input validation, not a change in currency units or rounding of legitimate prices.

## Repair and ownership

New app/core/numbers.py whole_number converts integer-compatible strings and whole numeric values without discarding a fraction; booleans and nonfinite values fail validation. app/catalog/service.py uses it for its existing integer fields. app/api/app.py uses it only through the existing _int helper, preserving omitted/None/empty-string zero defaults. No blanket rewrite of every int call. Other independently parsed inputs remain outside this bounded patch.

Files: those three plus tests/test_integer_input_guards.py and this report. The API file also changes in PR #2/#4/#5; retain their cash/payment/health changes when integrating. This branch is based on main and does not contain those unrelated fixes. No unpublished Claude work can be inferred from remote refs.

## Verification and limits

Eight synthetic regression tests: fractional pack creation, fractional price update/history, fractional opening cash, boolean pack size, whole numeric/string compatibility, fractional sale units without a new line, nonfinite/Decimal/negative-fraction parser checks, and legacy zero cash default. No live pharmacy/patient data or external API used.

Initial five-case reproducer: 4 failures. Initial targeted group: 15 passed. Full standalone branch suite: 382 passed, zero failures/skips, 36.674 seconds. On the isolated candidate containing PR #2–#7, 34 relevant input/cycle/cash/payment/batch tests passed (4.730 seconds). This is targeted combined validation, not a new full-candidate run.

Log SHA256:
integer-before.log f9005ad8b844ec61ebc3e15a398aabb1fe180b657c69992a5b58fbeb4e7e60ce
integer-target.log ff7852ba7c3187bd888e535bd466344f82f39621b316075b1db898e63aaa2dbb
integer-full.log b78c53cbb489868e2ce1e1b7d0e5cfe2309a57a3d68dcf82d09eee7502059154
integer-combined.log ed7d3aa92de72e5a3c899fdd94ce26f25937c194f5c550ae161f31c5e904c2b3 Existing split precision and cashier tests are reused rather than replaced. No actual Windows, printer or real invoice validation. No automatic merge or production approval.

Continue validating other parsing boundaries in separate tasks; do not claim this repairs all numeric input or all financial retry routes. Main/owner review and Windows/hardware acceptance remain separate release gates.
