# H2 — immutable code releases (checkpoint; NOT approved for deployment)

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

Current checks: 23 targeted tests passed. A real subprocess CLI check also staged a local archive,
ran candidate preflight on a temporary data directory, redirected CLI to candidate code, manually rolled
back, and rejected a failing preflight without changing the pointer. No pharmacy DB was opened/created.
Full regression and combined validation are pending; this checkpoint is not task completion.

Process-exit tests cover before/after pointer publication; this is not a real power cut or Windows test.
Windows locking/rename/embedded Python paths require Windows acceptance. TLS/signing and coordinated
schema/runtime upgrades remain separate work. No code is merged or deployed; preserve this branch for Claude.
