# Smart update — design (protocol 1 state, bootstrap 2)

Goal: after **one** more installer, every change (code, database migrations, Python packages, the
updater itself) reaches the pharmacy PC through the in-app «تحديث» button, safely.
Rules kept: the live database is never migrated in place; any failure leaves the old code and the old
database untouched; data and settings in the data folder survive; every step is audited.

## 1. Two stages
| | Stage 0 (frozen) | Stage 1 (updatable) |
|---|---|---|
| Files | installed `launcher.py` + `release_bootstrap.py` (stdlib only) | everything in a release: `manage.py update …`, `app/core/smart_update.py`, `app/core/update_sign.py`, `app/core/updater.py` |
| Changes by | installer only | the «تحديث» button |
| Does | select the verified release pointer; run the prepared *apply* step once; swap files from a hash-checked journal; supervise the first start; on failure undo the swap and the pointer | download, verify signature and hashes, stage code and packages, rehearse, apply on a copy, decide when |

Stage 0 is deliberately small and generic (file swaps from a journal, health check, rollback), so a
future change to the update logic lives in stage 1 and ships by the button. A new updater is checked
before it is selected: the candidate's own `update --self-check` (keys load, signature verify, deps
path) must pass. Recovery never depends on stage 1: stage 0 can always undo, and the installer
remains the last resort. Stage 0 writes `code-releases/bootstrap.json` (`{"bootstrap": 2}`); stage 1
refuses a database update when that capability is missing (old launcher: installer first).
The state file `active-release.json` keeps format 1 (installed launchers keep reading it);
`release_protocol.json` stays `{"protocol": 1}` (today's installed updater requires it).

## 2. Release bundle (CI, `.github/workflows/update-bundle.yml`)
On a push to `main` or `develop`, CI publishes release `update-<channel>` with:
- `code.zip` — `git archive --prefix=program/` of the commit (LF line ends).
- `deps-<id>.zip` — the complete `site-packages` built with
  `pip install -r requirements.txt --target … --platform win_amd64 --python-version 3.13 --only-binary=:all:`
  (the same command the installer uses). `id = sha256("3.13 win_amd64\n" + requirements.txt)`.
- `manifest.json` — `{format, channel, sha, commit_time, version, python, platform, code:{sha256,size},
  deps:{id,sha256,size}, migrations:[…]}` and `manifest.sig` (Ed25519, base64).
Signing key: repository secret `UPDATE_SIGNING_KEY`; public keys in `release_keys/*.pub`. No secret →
CI publishes nothing (fail closed). Trust root = the keys in the **active** release, never the
downloaded one; a new key ships inside a release signed by the old key.

## 3. Flow
1. **Check** (badge, every 10 min): manifest + signature → «فيه تحديث» with the plain Arabic list:
   the lines of `docs/CHANGES_AR.md` that the active release does not have (one batch, whatever the
   number of merges).
2. **Prepare** (program keeps working): download with resume into `code-releases/downloads/`, verify
   sha256 + size; stage the code (existing immutable release dirs); extract packages into
   `code-releases/deps/<id>/` only when `id` differs from the installed set; candidate `selftest
   --quick` and `update --self-check` on temporary data; **rehearsal**: online snapshot of the live DB
   (SQLite backup API, read only) → candidate code migrates the snapshot → integrity, foreign keys,
   stock balances, audit chain, row counts, app start + `/api/health`. Result: `prepared.json`.
   No network needed after this point.
3. **Choose**: «حدّث دلوقتي» or «حدّث بعد قفل الوردية». Recorded in `apply-request.json` and audited.
4. **Trigger** (server, every 30 s): never during a sale — no request in flight, no write in the last
   60 s, no draft sale with lines from the last 30 min; «بعد القفل» also waits until no shift is open.
   Code-only batch: verified backup, select the pointer (pending), restart (exit 75).
   Database batch: write `apply-go.json`, restart (exit 75).
5. **Apply** (stage 0 runs stage 1 once, program stopped, max 30 min): verified backup of the live DB →
   copy (backup API) to `data/update-candidate/pharmacy.db` → candidate code migrates **the copy** and
   runs the same checks as the rehearsal → journal `update-journal.json` (`ready`: live, candidate,
   previous path, sha256 of both) → select the pointer (pending).
6. **Swap** (stage 0): only when the journal is `ready`, its release is the pending pointer, both sha256
   match and the live DB has no `-wal`. `live → data/update-previous/…`, `candidate → live`
   (`os.replace`, same folder). Journal `swapped`.
7. **First start** (stage 0): health check with boot id (existing). OK → confirm, journal `done`.
   Fail → stop the process, move the new DB (+ `-wal`/`-shm`) aside, put the previous DB back, roll the
   pointer back, journal `rolled_back`, start the old version. A power cut at any step is resumed from
   the journal state on the next start.
8. **Report**: the next start (old or new code) audits every step from `update-log.jsonl` and shows
   the result in Arabic on the program page.

Any failure in 2–5: nothing selected, the live DB untouched, candidate files deleted, Arabic message.

## 4. Limits (known, accepted)
- After a **confirmed** start, going back means restoring the pre-update backup (later sales lost) or
  a fix forward. Automatic rollback covers "the new version does not start".
- A new Python version or a changed launcher still needs an installer (manifest `python`, bootstrap).
- `pywin32` loads DLLs from the installed set at interpreter start; a pywin32 upgrade is caught by the
  candidate selftest (import check) and then needs an installer.
- Until the owner adds the signing secret, the button keeps today's code-only path (zipball,
  migrations and requirements refused).
