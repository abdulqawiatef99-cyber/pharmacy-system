# DEVICE RUNBOOK: #18 and #19 on real Windows machines

Prepared 2026-10-30 so that the device sessions do not spend their usage re-reading docs. Sources:
`WINDOWS_ACCEPTANCE.md` §0 (R1–R10), the 12-step table in
`archive/HANDOFF_2026-10-30_before_compaction.md`, and `OWNER_TEST_PLAN.md` P1–P2.
Installer: run 72, artifact `PharmacySetup`, sha256 `913ad6027c81d7a32274f4026f82165f28b1c57757adaafbedc7509d722d5dd5`.
R8 candidate: `b632981`. It is identical to `c9906a9` except that `app/version.py` says rc2 (verified).
CLI commands: `backup`, `restore`, `verify`, `preflight`, `update`, `selftest`, `init`, `serve`.

## Rules for both machines
- Run `hostname` first and stop on a mismatch. Use synthetic data only. Never touch E-Stock, Radmin VPN,
  printer drivers or Windows security settings. Never delete or overwrite: copy into new folders.
- Post each result on #18 right away, prefixed with the hostname. `docs/STATUS.md` levels change only with
  the owner's confirmation.
- Hardware that is not attached = "not tested". It never counts as passed.

## A. Home laptop `DESKTOP-0DCSIC2` (any time; a real Windows test device, no hardware)
Stop if `%LOCALAPPDATA%\PharmacyPlatform` or `%LOCALAPPDATA%\Programs\PharmacyPlatform` exists or if
port 8000 is in use.
1. **G6:** R1 install (check the sha256 first) → R2 test pharmacy + user → R3 "backup now" → R4 reboot, same data.
2. **G2 money** (P1/P2 rows that need no hardware): shift with 100 opening cash; 3 strips of a 3-strip box =
   box price exactly; 10% line discount + change; refund one strip = exactly what was paid; legacy 99.99
   split-batch case (#72); close the shift and reconcile; credit sale 50, collect 20 → balance 30; contract 10%
   not below cost; delivery cash only on receipt; transfer + image (duplicate warning); expense from the cash drawer.
3. **G3 inventory:** purchase invoice with bonus → stock and public price; loss adjustment; stocktake shortage
   with reason; supplier cheque payment + statement; supplier return by batch.
4. **G4 permissions:** a cashier user cannot run owner actions (cost, discount above its limit, settings).
5. **G5:** R6 off-device backup to a USB flash (owner plugs one in); `verify` the backup zip; R7 = restore
   tonight's pharmacy backup here (a different machine) and compare the counts.
6. **G6:** R8 update to `b632981` (0.4.0rc2) → open the app → `--rollback` (0.4.0rc1), same data. Doing it here
   keeps the risk off the cashier PC. Then R10: run the installer again while the app is running.

## B. Pharmacy PC `DESKTOP-5JVH3QD` (during working hours with the owner watching; the chat session bound to it)
1. Read-only check: hostname, free disk on C: and D:, installed version, port 8000 owner, `Get-Printer`.
2. **Backup first:** `manage.py backup` → `verify` → copy it into a new `D:\PharmacyPlatform-backup-2026-10-30\`
   and onto a USB flash. Leave the old `D:\PharmacyPlatform-backup-2026-10-29\` untouched.
3. The owner downloads the run 72 artifact in the browser (the cloud cannot fetch it). The agent checks the
   SHA256 before the installer runs.
4. The 12-step table in order. Owner-only physical checks: the installer dialog (step 1 = R10), reading the
   receipt (3, 5, 6), the drawer opening (4, 5), scanning twice (8), reading the label (10). Step 11 (R8) runs
   here only if the owner wants it after the laptop result. Step 12 = R5 preflight (read-only).
5. If every line passes and nothing `launch-critical` fails: go straight to #19 (`OWNER_TEST_PLAN.md` P1–P2
   with the real scanner, printer and drawer) in the same session. Otherwise stop, file the Issues, and fix
   them in the cloud.
