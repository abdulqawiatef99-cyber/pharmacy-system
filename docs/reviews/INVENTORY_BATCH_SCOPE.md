# Inventory batch ownership guard — 2026-10-28

Branch: codex/inventory-batch-scope. Baseline main: 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0.
Files owned: app/inventory/service.py, tests/test_inventory_batch_scope.py, this report. No original migration, financial/clinical rule, quantity conversion or expiry policy changed.

## Confirmed service-level defect

post_movement checked the item and branch tenant but never checked that batch_id belongs to that same item and tenant. A direct service call with a valid local item and another item's/pharmacy's existing batch ID created a movement and balance referencing the wrong batch. Foreign keys only establish that the batch exists. Balance-ledger reconciliation alone does not establish batch ownership. Separately, get_or_create_batch accepted an item owned by another tenant and could create a batch scoped to the caller with that foreign item; a missing item with no metadata returned None without validation.

Reproduce in a synthetic migrated database: create tenants A/B, one item/batch per tenant, call post_movement as A with A's item and B's batch inside a transaction. Before the fix no NotFound was raised. Four of five new tests failed before repair; valid tracked/untracked stock passed.

Impact: inventory integrity and tenant boundary defense. This is confirmed at internal service entry points. No user-facing HTTP exploit or actual disclosure has been demonstrated; many callers already validate batches. Do not describe this as a proven UI exploit or evidence that historical data is corrupt.

## Minimal repair

Validate item ownership before batch creation/lookup (including the no-metadata path). Validate batch id + org_id + item_id before stock writes. Reject with the existing NotFound domain error. Keep valid tracked/untracked stock behavior. No cleanup, deletion or repair of existing records is attempted, and no live database was inspected.

## Verification

Five new synthetic tests: wrong-item movement, cross-tenant batch movement, foreign-item batch creation, missing-item/no-metadata, and valid tracked/untracked balances. Existing sales/catalog tests are included in targeted regression. Targeted regression: 45 passed. Full standalone branch suite: 379 passed, zero failures/skips, 37.087 seconds. After applying this guard to the previously tested isolated candidate, 38 relevant inventory/purchasing/operating-cycle tests passed (2.823 seconds). The earlier complete candidate run of 431 tests predates this guard; do not report 436 as tested.

Log SHA256:
inventory-batch-before.log 4ab6f7eeddd677acfd2788dbcc1c00f0898af1151a93ffe54eceecaa885128e1
inventory-batch-target.log 146843682f885fbb8d4307cae0102bc72132404b9ae288c665ae184a569db42f
inventory-batch-full.log a34f02389c72d962668ba7abac225121a8dda705e6f3d706a40d86d1264ad6a6
inventory-batch-combined.log 92f81c50128c335b1ac8bc2b882425e494d28e8d07ba27027b93b81046aa4d4f

## Continuation

Preserve existing inventory service and modular monolith. Review this small boundary guard independently; do not merge automatically. If an audit of existing data is later needed, design a read-only report and obtain explicit authorization before accessing real pharmacy data. Other isolation paths and all device/Windows/clinical acceptance remain open. Overall project percentage remains unmeasured against an approved requirement denominator.
