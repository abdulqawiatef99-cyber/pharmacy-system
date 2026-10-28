# H1 — scoped cash movement fix (in progress)
Owner authorization: 2026-10-28, "اعمل الصح", following the explicit approval question about the limited H1 fix.
Baseline: 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0.
Branch: codex/fix-cash-idempotency. No merge or production deployment authorized.

Reserved in this isolated branch: app/api/app.py; new app/core/idempotency.py;
new tests/test_cash_idempotency.py; this document.
No evidence of another published code branch was found; this does not establish Claude's unpublished local state.
Claude must reconcile this small PR before editing the same API handler. No original migration will be changed.

Implementation plan: special-case only POST /api/cash/movements out of the generic post-commit response cache.
Read the request before starting a transaction. A synchronous helper, using the existing idempotency_keys
table, rechecks the key and executes the drawer change, audit, response serialization and key insertion in
one BEGIN IMMEDIATE transaction. Do not await while a transaction is open.
Authorize cash.manage and sales.read before serving cached responses.
Keep key namespace, fingerprint, 7-day cleanup and invalid/absent-key behavior compatible.

Financial amounts, limits, return policy and cost rules stay unchanged. No new tables or migrations.
Test failure before cache persistence, response-generation failure, concurrent requests, mismatched keys,
permission revocation, cached replay and separate actions without a key.
This fixes only this cash movement route; other generic financial POSTs are not claimed safe by extension.
H2/H3 remain open. Before completion, record red/green results, run the applicable suite and publish a review PR.
