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
remains the last resort. Stage 0 tells the program it starts what it can apply: `PHARM_BOOTSTRAP=2` in
the server's environment; stage 1 refuses every smart batch when that capability is missing (old
launcher: installer first; the button keeps today's code-only path there). Under a launcher only the
environment counts: `code-releases/bootstrap.json` (also written by stage 0) outlives the launcher when
an older installer is put back, and that launcher never applies a batch, so exit 75 would only restart
the program again and again (second review). The file is read only by commands run by hand.
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
downloaded one; a new key ships inside a release signed by the old key. The secret reaches only the two
steps that read it. The build log names the committed key that verified the signature (`update-2.pub`
since 21/11; the next spare, `update-3.pub`, stays offline for rotation and must never be the one signing).
**Dry run:** a PR labelled `ci` that touches the signing path (this workflow, `scripts/release/`,
`release_keys/`, `update_sign.py`, `smart_update.py`, `requirements.txt`), or a manual run on a work
branch, goes through the same steps (key present, package set, build, sign, verify the way the PC
reads it) and publishes nothing, so a missing or wrong key shows before the merge.

**Settled 21/11 (owner 13:16): outcome (b).** update-1's private file was not found (the secret set at 10:28
matched no committed key: run 44896903157 attempt 1). The owner put the spare back; attempt 2 logged `signed
with release_keys/update-2-spare.pub`. So `update-2-spare.pub` is now `release_keys/update-2.pub` (same key,
blob `d61835b`) and `update-1.pub` is removed. **Before any merge to `main` (and before the next installer):**
the owner runs `new-signing-key.bat` (new spare to `F:\pharmacy-update-keys`) and an agent commits its
`update-3.pub`, so the PCs always trust a second key.

**Which key signs (part 3 item 0, owner 20/11 23:18): settle before anything is published.** The dry run on
`77a392a` signed with `update-2-spare.pub`: the secret holds the **spare**. Tools (folder «أدوات الصيدلية», or
`scripts\windows\` of a downloaded copy of the repository, which finds `release_keys\` itself):
`signing-key-check.bat` names, for every small `.txt` on the flash drive, the committed key it matches
(update-1 / update-2-spare / none); it never prints or copies a key. Then:
- **(a) update-1's private file is found:** the owner puts that one line in the secret `UPDATE_SIGNING_KEY`
  (Settings → Secrets and variables → Actions → Repository secrets → Update); the spare goes back offline.
  Re-run the dry run: the log must say `signed with release_keys/update-1.pub`. No code change.
- **(b) not found:** update-2 becomes the signing key (the secret stays as it is). `new-signing-key.bat`
  writes a NEW spare to `F:\pharmacy-update-keys` (`update-signing-PRIVATE.txt` stays there, offline;
  `update-3.pub` is sent to an agent). The agent's PR adds `release_keys/update-3.pub`, removes
  `release_keys/update-1.pub` (a lost private key must not stay trusted), and updates this paragraph.
  Done before the first installer with these keys, so no PC ever trusts the lost key.

**Channels (owner 20/11):** the pharmacy PC follows the official release, `main`; `develop` is for test
PCs. An install from a work branch (a review installer, like the launch candidate on the pharmacy PC)
follows `main` as soon as `main` contains its commit (GitHub compare), never an older `main`; until
then it is offered nothing. The first update from `main` records `ref = main`, so the PC stays there.

## 3. Flow
1. **Check** (badge, every 10 min): manifest + signature → «فيه تحديث» with the plain Arabic list:
   the lines of `docs/CHANGES_AR.md` that the active release does not have (one batch, whatever the
   number of merges). Only forward: with the installed commit time known (a smart update records it)
   the times decide; an installer or a code-only update records none, so GitHub must show the
   bundle's commit containing the installed one (a bundle that lags behind a code-only update is
   no update). An offline bundle with nothing to compare is the owner's own choice; the copy check
   still refuses a database the code is too old for.
2. **Prepare** (program keeps working): download with resume into `code-releases/downloads/`, verify
   sha256 + size; stage the code (existing immutable release dirs); extract packages into
   `code-releases/deps/<id>/` only when `id` differs from the installed set; candidate `selftest
   --quick` and `update --self-check` on temporary data; **rehearsal**: online snapshot of the live DB
   (SQLite backup API, read only) → candidate code migrates the snapshot → integrity, foreign keys,
   stock balances, audit chain, row counts, app start + `/api/health`. Result: `prepared.json`.
   No network needed after this point.
3. **Choose**: «حدّث دلوقتي» or «حدّث بعد قفل الوردية». Recorded in `apply-request.json` and audited.
4. **Trigger** (server, every 30 s): never during a sale — no write in the last 60 s, no draft sale with
   lines from the last 30 min; «بعد القفل» also waits until no shift is open. The last check runs while
   holding the database write lock and with no HTTP request in flight; then `apply-go.json`, exit 75.
5. **Apply** (stage 0 runs stage 1 once, program stopped, max 30 min). Code-only batch: verified backup,
   select the pointer (pending), done. Database batch: verified backup of the live DB →
   copy (backup API) to `data/update-candidate/pharmacy.db` → candidate code migrates **the copy** and
   runs the same checks as the rehearsal → journal `update-journal.json` (`ready`: live, candidate,
   previous path, sha256 of both) → select the pointer (pending).
6. **Swap** (stage 0): only when the journal is `ready`, its release is the pending pointer, both sha256
   match and the live DB has no `-wal`. `live → data/update-previous/…`, `candidate → live`
   (`os.replace`, same folder). Journal `swapped`.
   If a move fails and the live file cannot be put back at once, the journal stays `ready`, the copy is
   kept and **no program starts** (retried at the next start; the old program could otherwise create an
   empty database).
7. **First start** (stage 0): health check with boot id (existing). OK → confirm, journal `done`.
   Fail → stop the process, move the new DB (+ `-wal`/`-shm`) aside, put the previous DB back, roll the
   pointer back, journal `rolled_back`, start the old version. A power cut at any step is resumed from
   the journal state on the next start.
8. **Report**: the next start (old or new code) audits every step from `update-log.jsonl` and shows
   the result in Arabic on the program page. The position read so far is kept in the database itself,
   so a database put back by an undo re-imports the events it lost.

Only one launcher runs steps 5–7 at a time (`stage0.lock`, held for the launcher's life); opening the
icon twice only opens the browser.

Any failure in 2–5: nothing selected, the live DB untouched, candidate files deleted, Arabic message.

## 4. Limits (known, accepted)
- After a **confirmed** start, going back means restoring the pre-update backup (later sales lost) or
  a fix forward. Automatic rollback covers "the new version does not start".
- A new Python version or a changed launcher still needs an installer (manifest `python`, bootstrap).
- `pywin32` loads DLLs from the installed set at interpreter start; a pywin32 upgrade is caught by the
  candidate selftest (import check) and then needs an installer.
- Until the owner adds the signing secret, the button keeps today's code-only path (zipball,
  migrations and requirements refused).
