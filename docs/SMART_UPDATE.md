# Smart update — design (protocol 1 state, bootstrap 2)

The deployed bootstrap-2 launcher applies code, schema, package and updater changes through the
in-app «تحديث» button. Candidate migrations run on a copy of the database. The launcher keeps its
existing health-supervised rollback.

## 1. Two stages

| | Stage 0 (installed) | Stage 1 (updated through the button) |
| --- | --- | --- |
| Files | `launcher.py` and `release_bootstrap.py` from the last installer | `manage.py`, `app/core/smart_update.py`, and the rest of the release |
| Tasks | Run the prepared apply command, swap files using the journal, supervise actual first start and recover on failure | Authenticate bundles, stage and rehearse, recheck code and packages at apply and startup |

The currently deployed updater on `b433c31` accepts the existing signed outer manifest and checks
the code ZIP before staging. It does not persist that manifest. Each new bundle therefore also
contains a signed startup proof. It authenticates the staged file tree, release identity, channel,
and package archive digest. The first hardened release checks this proof at startup using the public
keys in the installed program root exported by bootstrap 2. It needs neither another download nor an
installer change. Subsequent preparations retain the signed outer manifest for verification before apply
and at startup.

Bootstrap 2 still checks its original local checksums before handing off to the selected entry point.
The signed check runs in that entry point before it loads application packages or opens the database.
Protection against direct writes to the installed files or staged verifier remains an operating-system
boundary (SEC-08). Previously installed local emergency tools keep their existing recovery behavior.

The deployed launcher supplies `PHARM_BOOTSTRAP=2`. A candidate runs its self-tests with isolated
data first. Stage 0 continues to supervise startup and restore the old database and code on failure.
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
CI fails the bundle build and publishes nothing. The trust root is the key set in the installed
program directory. The trust root uses the keys already deployed in that directory. The secret reaches
only the steps that sign or verify the build. The build log names the committed key that verified the signature (`update-2.pub`
since 21/11; the next spare, `update-3.pub`, stays offline for rotation and must never be the one signing).
**Dry run:** a PR labelled `ci` that touches the signing path (this workflow, `scripts/release/`,
`release_keys/`, `update_sign.py`, `smart_update.py`, `requirements.txt`), or a manual run on a work
branch, goes through the same steps (key present, package set, build, sign, verify the way the PC
reads it) and publishes nothing, so a missing or wrong key shows before the merge.

**Settled 21/11 (owner 13:16): outcome (b).** update-1's private file was not found (the secret set at 10:28
matched no committed key: run 44896903157 attempt 1). The owner put the spare back; attempt 2 logged `signed
with release_keys/update-2-spare.pub`. So `update-2-spare.pub` is now `release_keys/update-2.pub` (same key,
blob `d61835b`) and `update-1.pub` is removed. **Spare done 21/11 14:41:** the owner ran `new-signing-key.bat`
on the cashier PC (private half only on the flash drive `F:\pharmacy-update-keys-3`); its public half is
`release_keys/update-3.pub`. So **update-2 signs, update-3 is the spare**, and the PCs trust both from the next
installer on.

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
   No network is needed after this point. Keep the cached signed assets and the proof in
   `code-releases/proofs/` for application, restart and compatible rollback.
3. **Choose**: «حدّث دلوقتي» or «حدّث بعد قفل الوردية». Recorded in `apply-request.json` and audited.
4. **Trigger** (server, every 30 s): never during a sale — no write in the last 60 s, no draft sale with
   lines from the last 30 min; «بعد القفل» also waits until no shift is open. The last check runs while
   holding the database write lock and with no HTTP request in flight; then `apply-go.json`, exit 75.
5. **Apply** (stage 0 runs stage 1 once, program stopped, max 30 min). Reverify the publisher proof
   and exact staged files. Code-only batch: verified backup,
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
   During a database update, the candidate serves only the health probe until its release is confirmed
   and its journal reaches `done`. The cashier API returns a retryable 503 during this interval.
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
- The signed update path keeps the deployed Python and launcher protocol.
- `pywin32` loads DLLs from the installed set at interpreter start; a pywin32 upgrade is caught by the
  candidate selftest (import check). No new installer is built (OWNER_RULES §3), so do not upgrade pywin32
  until the owner decides how to ship it.
- Production releases use signed bundles. The transition retires the branch and raw ZIP paths.
  The Windows installer workflow is manual and publishes review builds only.
- The installer records its commit time. A healthy signed update raises the per-channel version floor,
  which also applies to offline updates and survives a compatible rollback.

## Space check before the program stops (#461)
`smart_update.check_space` runs when the update is scheduled (the refusal is shown on screen), again just
before the program leaves for the launcher, and again in the apply step, for code-only updates too (they
take a verified backup as well). It counts, per disk and at worst case (nothing assumed to compress):
- backups folder: database + E-Stock archive (its ready `.xz` when there is one) + all attachments;
- system TEMP: 2 x database + archive (building the backup, then unpacking it for verification while
  SQLite's checks write their sort files there);
- data folder: the archive's `.xz` cache when none is ready; with a database change also 3 x the database.
200 MB stay free on each disk; folders on one disk add up. A refusal names the folder, the need and the free
space. If the disk filled after scheduling, the request is dropped (no retry loop), the program keeps
running and the update screen shows why.
