# #18 retest visit checklist (2026-11 fixes: #100, #98, #107, #114, #116)

This is the current checklist for the next #18 visit; it is also posted on #18. It follows the owner's
location rule (each visit runs its whole list in one go) and the `pharmacy-system-device-test` workflow.
Who: **Owner** = the owner by hand; **Claude** = Claude runs it on the PC through the device bridge.
Rules: hostname first and stop on a mismatch; synthetic data only; never touch E-Stock, Radmin VPN,
printer drivers or Windows security settings; start the app from its own shortcut; post each group's
results on #18 right away (hostname, installer run, table of step / result / evidence, settings left).
Any failure = its own `launch-critical` Issue.

## Visit runner (coordinator, browser tab only)
`docs/visit/visit_runner.js` replaces the hand-written fetch calls. In the tab logged in to
http://127.0.0.1:8000, evaluate the whole file once (again after any reload), then one call per tool
call; each finishes in under 25 s and returns `{md, rows}` (print `r.md`: id / PASS · FAIL · SKIP ·
NEEDS-OWNER · INFO / evidence). Every request is same-origin with `X-Requested-With: pharm`; it saves no
settings, deletes nothing and writes only `TEST-VISIT` records. Order on an install day:
1. `r = await visit.pre()` → post `r.md` on #18 and keep `r.json` (the snapshot is also in localStorage).
2. Owner installs (B4). 3. `await visit.post(r.snapshot, {channel: "develop", noUpdate: true})`
   (add `installed: "<sha>"` when known). 4. `await visit.flows()` (only after step 3: it adds TEST sales).
5. Printer rows: `await visit.print("test" | "labels" | "sale", {confirm: true} | "reprint" | "drawer")`, then
   ask the owner the Arabic question in the row; B8(b): `await visit.printAudit("<time before the sale>")`.
6. B13: `s = await visit.pre({backup: false})` right before the press, then `await visit.post(s.snapshot, {audit: 1})`.
`visit.day19()` checks the #19 end-of-day figures. Contract and browser tests: `tests/test_visit_runner.py`.

| Row | Runner call | Result it gives |
|---|---|---|
| B1 | `visit.pre()` | Backup name, size, `last_ok` (sha256 and the USB copy stay manual) |
| B2 | `visit.pre()` | Health, counts, stock totals, cash, settings, channel |
| B5 | `visit.post(snapshot)` | New `boot_id`, `/` no-cache and `app.js?v=`, every snapshot value line by line |
| B6 | `visit.post(…, {channel, noUpdate})` | Channel unchanged, no update offered |
| B7 | `visit.print("labels", {label_shift: -2})` | NEEDS-OWNER: how many labels, whole, no blank |
| B8 | `visit.print("sale" / "reprint" / "drawer")`, `visit.printAudit(since)` | NEEDS-OWNER questions; (b) audit `not_printed` + `failed` |
| B9 | `visit.flows()` | Credit sale refund defaults to «آجل» on the real screen; double click and a retried POST each give one return |
| B13 | `visit.post(snapshot, {audit: 1})` | New `boot_id`, data unchanged, audit +1 |
| B14 | `visit.post(snapshot)` | Receipt, label and drawer settings unchanged |
| L2 | `visit.flows()` / `visit.estock()` | Cut-off E-Stock stock file refused with `estock_totals_mismatch` (upload through `/api/imports`) |
| L3 | `visit.flows()` / `visit.estock()` | Complete single-store file accepted (draft discarded, nothing imported) |
| L6 | `visit.day19()` | DAY19_SCRIPT.md end-of-day figures |
Not covered (owner's eyes or hands): B0, B3, B4, B10–B12, L0, L1, L4, L5.

## Before the visit (cloud)
| # | Step | Who | Expected |
|---|---|---|---|
| V0.1 | ✅ Done 11/11: PR #123 merged; 50 old artifacts deleted with the owner's yes | Claude | Usage recalculates in 6-12 h; no longer blocks delivery |
| V0.2 | ✅ Done 11/11: artifact upload still blocked by the quota, so PR #129 delivers manual builds as the pre-release `installer-review`; run [43018407392](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/43018407392) green | Claude | exe sha256 `ec7de5ff…0934` in the release notes |
| V0.3 | `develop` must not move between that build and the visit, except the one docs change kept for step B9 | Claude | The installed build is older than `develop` only at B9 |

## Pharmacy PC `desktop-5jvh3qd` (real devices; any time that suits the owner — open or closed, no fixed rule — with the owner watching the screen; E-Stock stays the live cashier and is never closed or covered full-screen)
| # | Step | Who | Expected |
|---|---|---|---|
| B0 | `hostname` | Claude | `DESKTOP-5JVH3QD`; anything else: stop |
| B1 | Backup first: `POST /api/backup/run` with header `X-Requested-With: pharm`; copy the file to the USB stick | Claude, then Owner (USB) | Backup written and verified; file name + size + sha256 on #18; copy on USB (open reminder since 11/01) |
| B2 | Record counts before install (users, items, sales, stock rows, audit, migrations), `/api/health` (`version`, `boot_id`) and `GET /api/system/update` (`installed`) | Claude | Numbers on #18 |
| B3 | Download `PharmacySetup.exe` from the pre-release [`installer-review`](https://github.com/abdulqawiatef99-cyber/pharmacy-system/releases/tag/installer-review) (#122 fix, PR #129); check its sha256 against the release notes | Owner or Claude downloads, Claude checks | sha256 of the exe = `ec7de5ff04e5ae9dda512af53cf8fb1bdd90c3b5d0117e8bdcddbc972e2e0934` (run 43018407392, `develop` @ `f56de12`) |
| B4 | R10: run `PharmacySetup.exe` **while the old app is running** | Owner | "The app is running… OK to close", then install completes with no "unable to automatically close"; app reopens from its shortcut |
| B5 | `/api/health`, `GET /api/system/update` and the same counts as B2 | Claude | A new `boot_id`; `installed` = the review build's SHA (`version` may stay the same: it changes only with `app/version.py`); counts identical; `/` returns `no-cache` and the new `app.js?v=` |
| B6 | Settings → البرنامج: update channel | Owner looks, Claude reads `GET /api/system/update` | «قناة التحديث: develop (تجربة)» (#107); "no update" right after install |
| B7 | #100 labels XP-235B 38×13, gap 2: shift −2 mm → «اطبع 3 ملصقات تجربة»; change by 0.5 mm until 3 come out whole, back to back, no blank; if −2 moves the print further down, try +2; save; then 5 labels from an item card | Claude sets, Owner looks | 3 test labels whole; after save, 5 labels whole with no blank between them; the saved value is on #18. Extra: set 0, save, print 3; they must print as before the fix (checks the printer does not keep the old SHIFT) |
| B8 | #98 receipt printer POS-80C. (a) printer **on**: cash sale with receipt, long receipt (10+ lines) | Owner | Prints normally, no warning |
| | (b) printer **off**: cash sale with receipt (F4 then F9) | Owner, Claude reads `Get-PrintJob -PrinterName POS-80C` and the audit | Warning «الإيصال لم يُطبع» and «الدرج لم يُفتح (F8)»; queue empty; audit `print.receipt` `not_printed`, `cash.drawer_auto` `failed`. After power on: **nothing prints, drawer stays shut** |
| | (c) printer on again: «اطبع إيصال آخر فاتورة» | Owner | One receipt, no drawer |
| | (d) printer off: manual drawer open, and F8 | Owner | A warning each time; nothing fires at power on |
| B9 | #114 returns: credit sale to the test customer → invoices → return one unit; then a fast double click on «تسجيل المرتجع» on another return | Owner, Claude checks the DB | Refund defaults to «آجل» (the sale's own payment); the double click records **one** return |
| B10 | P7 (old failure): covered by B8(b) | — | Pass only if B8(b) passes |
| B11 | P10 (old failure): covered by B7 | — | Pass only if B7 passes |
| B12 | Scanner HH360: scan an item and a printed label, focus in the search box and in the customer field | Owner | Added once, no DevTools, customer field stays empty |
| B13 | Update button (#107): the coordinator merges the small docs PR with this visit's results (after `CI / linux` is green on its head), so `develop` moves; Settings → «تحديث» | Claude prepares the PR and records the counts again right before the press (B8/B9 add rows), Owner merges and presses | Offers the update; the app restarts by itself; a new `boot_id` and `installed` = the merged `develop` SHA; business-data counts identical to the snapshot taken just before the press, audit = the same rows plus the one `system.update` row; channel still develop |
| B14 | Leave the settings as found: label shift = the saved good value, drawer timing «بعد الإيصال», receipt printer selected | Claude | Settings listed on #18 |

## Home laptop `DESKTOP-0DCSIC2` (no devices)
| # | Step | Who | Expected |
|---|---|---|---|
| L0 | `hostname`; check the install paths and port 8000 per `HANDOFF.md` | Claude | `DESKTOP-0DCSIC2`; anything else: stop |
| L1 | Install the same review installer (R10 if the app is running); counts before and after | Owner, Claude counts | Install completes, counts identical |
| L2 | #116 E-Stock: `manage.py import-estock --stock <file cut off at the end> …` without `--apply` (dry run) | Claude | Refused with `estock_totals_mismatch` |
| L3 | #116: dry run on a complete E-Stock stock export, including a single-store export | Claude | Accepted as before (checks the grand-total line is there in a single-store file) |
| L4 | #114 return form on a credit sale and a card sale | Owner | Defaults: credit → «آجل», card only → «بطاقة», cash → «نقدًا» |
| L5 | #107: Settings shows channel develop; update button after the same docs merge as B13 | Owner | Same as B13 |
| L6 | #19 dry run of [DAY19_SCRIPT.md](DAY19_SCRIPT.md) (optional, before the pharmacy day) | Owner | The end-of-day figures match the script |

## After the visit
Every line marked pass / fail with evidence on #18; failures = new Issues; `HANDOFF.md` next steps
updated; then #19 ([DAY19_SCRIPT.md](DAY19_SCRIPT.md)) and #20.
