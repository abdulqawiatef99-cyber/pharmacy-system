# #18 retest visit checklist (2026-11 fixes: #100, #98, #107, #114, #116)

This is the current checklist for the next #18 visit; it is also posted on #18. It follows the owner's
location rule (each visit runs its whole list in one go) and the `pharmacy-system-device-test` workflow.
Who: **Owner** = the owner by hand; **Claude** = Claude runs it on the PC through the device bridge.
Rules: hostname first and stop on a mismatch; synthetic data only; never touch E-Stock, Radmin VPN,
printer drivers or Windows security settings; start the app from its own shortcut; post each group's
results on #18 right away (hostname, installer run, table of step / result / evidence, settings left).
Any failure = its own `launch-critical` Issue.

## Before the visit (cloud)
| # | Step | Who | Expected |
|---|---|---|---|
| V0.1 | #122: PR #123 merged after `CI / linux` is green on its head; owner says yes to deleting the old artifacts listed on #122 | Owner (chat) | Claude deletes them; usage recalculates in 6-12 h |
| V0.2 | Owner approves one review installer build of `develop` in chat; Claude dispatches it | Owner, then Claude | Run green, artifact `PharmacySetup`; run id, zip sha256 and expiry recorded in `HANDOFF.md` |
| V0.3 | `develop` must not move between that build and the visit, except the one docs change kept for step B9 | Claude | The installed build is older than `develop` only at B9 |

## Pharmacy PC `desktop-5jvh3qd` (real devices; after closing hours)
| # | Step | Who | Expected |
|---|---|---|---|
| B0 | `hostname` | Claude | `DESKTOP-5JVH3QD`; anything else: stop |
| B1 | Backup first: `POST /api/backup/run` with header `X-Requested-With: pharm`; copy the file to the USB stick | Claude, then Owner (USB) | Backup written and verified; file name + size + sha256 on #18; copy on USB (open reminder since 11/01) |
| B2 | Record counts before install (users, items, sales, stock rows, audit, migrations) and `/api/health` (`version`, `boot_id`) | Claude | Numbers on #18 |
| B3 | Download the artifact in Edge; check the zip sha256 against the GitHub digest | Owner downloads, Claude checks | Digest = the one in `HANDOFF.md` (digest is of the zip, not the exe) |
| B4 | R10: run `PharmacySetup.exe` **while the old app is running** | Owner | "The app is running… OK to close", then install completes with no "unable to automatically close"; app reopens from its shortcut |
| B5 | `/api/health` and the same counts as B2 | Claude | New `version` and a new `boot_id`; counts identical; `/` returns `no-cache` and the new `app.js?v=` |
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
| B13 | Update button (#107): the coordinator merges the small docs PR with this visit's results (after `CI / linux` is green on its head), so `develop` moves; Settings → «تحديث» | Claude prepares the PR and records the counts again right before the press (B8/B9 add rows), Owner merges and presses | Offers the update; the app restarts by itself; `/api/health` new `version`/`boot_id`; counts identical to the snapshot taken just before the press; channel still develop |
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
