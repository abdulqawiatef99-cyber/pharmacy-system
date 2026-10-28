# H3 — attachment verification proposal and regression tests
Status: tests/design only; awaiting owner approval before changing app/core/backup.py.
Baseline: 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0; branch codex/backup-attachment-verification.
No application code or restore replacement algorithm changed in this checkpoint.

Scope proposed: verify registered source_files against ZIP members, SHA-256 and size from the snapshot DB.
Use the existing schema and formats 1/2; no new archive format, encryption, migration or data deletion.
Resolve only archive members under the correct organization and known original/transfer layouts.
Normalize stored Windows paths as metadata; never open the old absolute path during archive verification.
Check quick_check too, because automatic backups use it. Reject duplicate ZIP entries.
Keep restore's existing order, so rejecting a broken archive happens before touching live data.

Compatibility requirement: do not make create() unconditionally reject missing live uploads in this limited fix.
The existing recovery test intentionally restores when the current uploads are missing; a pre-restore snapshot
must still be possible. Distinguish an incomplete pre-restore safety copy from a verified complete recovery backup.
Do not claim that a damaged current state can never be snapshotted or that file replacement is already atomic.

Tests cover: missing/changed invoice, cross-tenant substitution, duplicate entries, quick-check integrity,
legacy format 1, current format 2, Windows stored paths, transfer subfolder and rejection before live replacement.
Only synthetic temporary files/databases are used. H1 fix is a separate PR #2; this branch is based on main.
H2 remains open. No merge, deployment or production backup/restore is authorized by this checkpoint.
