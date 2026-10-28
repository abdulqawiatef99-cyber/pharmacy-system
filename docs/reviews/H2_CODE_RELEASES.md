# H2 — immutable code releases (code implementation; NOT approved for deployment)

Baseline: 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0.
Branch: codex/atomic-code-releases. Owner preauthorizes project-local development; no main merge or production use.

Design: stage every source archive in its own directory, verify file hashes, and replace one activation JSON
pointer atomically. Keep the installed launcher and previous code intact. A pending first startup must
answer health with that child's startup nonce; failure selects the previous complete code, without
restoring the database. No await, financial-rule change, schema migration or new external service.

Scope is code-only protocol-1 updates with identical requirements and SQL migration contents.
A dependency/schema change is refused, requiring a coordinated upgrade. This is deliberate:
automatic DB restore after accepting newer transactions could lose data.
The initial transition from the old in-place updater requires a verified installer with the app stopped.
Do not use the old unsafe updater to bootstrap this fix.

Changed original files: app/core/updater.py, app/api/app.py (health only), launcher.py, manage.py,
tests/test_updater.py. New: release_bootstrap.py, release_protocol.json, tests/test_atomic_releases.py.
app/api/app.py also has an independent cash-handler change in PR #2; reconcile both disjoint changes
and run combined tests before recommending a release. No original migration or requirements file changed.

Current checks: 393 standalone tests passed (37.076 seconds), including 24 targeted updater/release tests. A real subprocess CLI check also staged a local archive,
ran candidate preflight on a temporary data directory, redirected CLI to candidate code, manually rolled
back, and rejected a failing preflight without changing the pointer. No pharmacy DB was opened/created.
Combined isolated validation with PR #2, PR #3 and the four independent access tests: 422 tests passed, zero failures, 38.629 seconds. This was a local composition, not a Git merge. Direct CLI serve refuses a pending release without launcher supervision.

Process-exit tests cover before/after pointer publication; this is not a real power cut or Windows test.
Windows locking/rename/embedded Python paths require Windows acceptance. TLS/signing and coordinated
schema/runtime upgrades remain separate work. No code is merged or deployed; preserve this branch for Claude.

Continuation: review this protocol before installer adoption; preserve the stable bootstrap contract. No requirements or migrations changed. Reconcile PR #2 health/cash changes in the same API file. Existing source ZIP backups are not complete Git history or production database backups.

Validation log SHA256:
h2-full.log 4dff8356bfd6f9bc0d31366877d6ba83517f8817350614f29fc961978ac2a708
h2-integration-full.log 4fe9942ad10f803842a2204fd65edcb399fd3c6d8e2be403b69dcddbd2c9da19
