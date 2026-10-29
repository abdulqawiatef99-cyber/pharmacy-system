# Customer collection retry protection — 2026-10-28

Owner: Codex. Branch: codex/customer-payment-idempotency, based on PR #2 head ea5a3034b2da57e46d547e40f0f4f7faf63e9d96. Preserve both PR #2 and this extension; no main merge or production deployment.

Confirmed before fix: 2 of 4 initial synthetic tests failed. A failed idempotency cache insert returned HTTP 500 after committing the customer ledger/cash/audit; retry could collect again. A cached success also bypassed a subsequently revoked customers.manage permission.

Fix: reuse the existing synchronous atomic replay helper, explicitly bypass the generic post-commit cache for the customer payment route, and recheck customers.manage on every request. All awaits precede the transaction. Ledger, cash, audit, response serialization and replay record commit or roll back together. No financial calculation, schema, migration, credit policy or payment-method rule changed.

Files owned in this branch: app/api/app.py (customer payment handler and generic dispatch exception), app/core/idempotency.py (docstring only), tests/test_customer_payment_idempotency.py, this report. API file also changes in PR #2 and PR #4: preserve their cash handler and boot health changes when integrating. No published Claude branch other than main was present at this review; unpublished work cannot be inferred from GitHub.

Targeted checks: 20 tests passed (8 new collection tests plus 12 existing cash retry tests). New cases cover cache persistence failure, response serialization failure, revoked permission, changed payload, changed customer path, concurrent independent connections, card collection without cash movement, invalid request without consuming key, and repeated success. Audit chain verified after rollback/retry and concurrent collection.

Limits: guarantees apply to the same valid Idempotency-Key and existing retention policy. New keys, missing/oversized keys and other financial endpoints are not deduplicated by this fix. This does not close the whole financial gate or authorize production. No patient data, live database, E-Stock or external payment system used.

Full branch suite: 394 tests passed, zero failures/skips, 38.829 seconds.

Logs (SHA256):
customer-payment-before.log 82b88d0ad61d205510c6ca7da5f00260b42de2cd2645bdc73a600ceb4a6a7641
customer-payment-target.log e0b9d0ad2f30a0e820f8ffa6ee9f4d0542172793ccaa9d406c7a5a9634fd010e
customer-payment-full.log f70dc011d32e1969ce9274072c7ee26fdfb7aa411670b131dbae840a2aa97149
