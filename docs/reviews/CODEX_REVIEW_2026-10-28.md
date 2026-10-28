# Independent review — checkpoint 1

Date: 2026-10-28 (Africa/Cairo).
Baseline: `69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0`.
Reviewer: Codex. Primary developer: Claude. Owner approves material decisions.

## Scope and safety
- Review and test an isolated source snapshot with synthetic, temporary databases.
- No production/E-Stock access, migrations on real data, paid integrations, or changes to main.
- This branch preserves review work; do not run its contents on the pharmacy computer.
- Work in bounded batches to conserve usage. Save a checkpoint after each completed batch.
- No application changes in this checkpoint.

## Verified so far
- GitHub main still points to the baseline above; branch protection is disabled (read-only observation).
- Source tree: 241 files; 33 numbered SQL migrations; 52 test modules.
- Static source contains 367 test method definitions; this is NOT the executed test count.
- GitHub Windows installer workflow run 40203671697 succeeded, including Tests (Windows),
  embedded-build smoke test and installer build. Physical printers remain unverified.
- PROJECT.md and parts of FUTURE_REQUIREMENTS.md lag behind implemented code.
- Local isolated environment is being prepared; the baseline suite has NOT yet completed here.

## Priority review candidates (not reproduced defects yet)
1. API idempotency response persistence happens after the handler's committed business change.
   Reproduce a crash at that boundary, using a synthetic cash operation, before proposing a fix.
2. Updater copies files in place; staged extraction does not make replacement atomic.
   Reproduce partial file-copy failure and inspect rollback behavior.
3. Permission and isolation sweeps test status codes and selected table counts; inspect
   whether valid request bodies and unchanged existing values need stronger assertions.
4. Audit SHA-256 chaining provides limited tamper evidence; do not describe it as
   protection against an attacker who controls the entire database and can recompute hashes.

## Resume
1. Finish materializing and verifying every file against Git blob SHA-1.
2. Run baseline unittest suite in the isolated environment; record versions, pass/fail/skip counts.
3. Save the result here before broadening the review.
4. Request owner review before applying material fixes, changing business rules or deploying.
