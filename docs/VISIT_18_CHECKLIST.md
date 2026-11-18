# #18 retest visit checklist (2026-11 fixes: #100, #98, #107, #114, #116)

This is the current checklist for the next #18 visit; it is also posted on #18. It follows the owner's
location rule (each visit runs its whole list in one go) and the `pharmacy-system-device-test` workflow.
Who: **Owner** = the owner by hand; **Claude** = Claude runs it on the PC through the device bridge.
Rules: hostname first and stop on a mismatch; synthetic data only; never touch E-Stock, Radmin VPN,
printer drivers or Windows security settings; start the app from its own shortcut; post each group's
results on #18 right away (hostname, installer run, table of step / result / evidence, settings left).
Any failure = its own `launch-critical` Issue.

Checked against `develop` on 17/11/2026 (night session B): screen names, messages and the behaviour changes of
16/11 below are today's. Status from `docs/HANDOFF.md` (17/11 00:30): test 1 (#98) and test 2 (driver labels +
scanner) of the day script passed on the cashier PC on 16/11 (#18); the steps stay here as the record.
The #19 trial day has its own section: «#19 trial day — Heikal PC».

## Day script 14/11 — the 3 pharmacy PC tests (run in this order)
Written by the N2 night session, 13/11. One table per test: **Owner** = the owner by hand, **Claude** = the day chat
on the PC through the device bridge. The Arabic line is what the owner reads; the last column is what he should see.
Order: test 3 first (its update also brings the latest `develop` for tests 1 and 2), then 1, then 2.
Stop rule: a step that does not give the expected result → photo/screenshot, stop that test, post on #18, go to the next test.

**Before (Claude, 5 min):** `hostname` = `DESKTOP-5JVH3QD` (else stop) · `visit.pre()` (backup + snapshot) and post `r.md` on #18 ·
`CI / linux` on the `develop` head is green (OWNER_RULES §4; if CI is still blocked by GitHub billing, the day chat decides,
and says so on #18) · note which of #240 (#98 warning) and #241 (#206 clock) are merged: only merged PRs reach the PC.

### Test 3 — update and rollback (gate G6), 15 min
| # | Who | Step (owner's line in Arabic) | Expected |
|---|---|---|---|
| 3.1 | Claude | `s = await visit.pre({backup: false})`; write down «النسخة المثبتة» = **A** | Snapshot on #18; its `snapshot` row says PASS (saved in localStorage), so it survives the reloads below |
| 3.2 | Owner | افتح «المزيد» ← «البرنامج والنسخ الاحتياطي»، وعند قسم «البرنامج» دوس «حدّث البرنامج الآن»، واستنى من غير ما تقفل حاجة. | «جارٍ التحديث…» ثم «يعيد البرنامج تشغيل نفسه…»، والصفحة ترجع لوحدها في أقل من دقيقتين، و«النسخة المثبتة» بقت رقم جديد (**B**) |
| 3.3 | Claude | The page reloaded, so `s` is gone: evaluate the runner file again, then `await visit.post(undefined, {audit: 1})` (it takes the 3.1 snapshot from localStorage) | New `boot_id`, `installed` = B, data counts identical, audit +1 (`system.update`, `ok: true`) |
| 3.4 | Claude | Rollback (command below), then stop the old server | `Previous code selected. Restart the program; no database restore was performed.`; then `/api/health` no longer answers |
| 3.5 | Owner | افتح البرنامج من الأيقونة اللي على سطح المكتب «منصة الصيدلية»، وادخل «المزيد» ← «البرنامج والنسخ الاحتياطي» ← قسم «البرنامج». | البرنامج يفتح عادي، و«النسخة المثبتة» رجعت **A**، ومكتوب «يوجد تحديث جديد» |
| 3.6 | Claude | Evaluate the runner again, then `await visit.post()` (snapshot from localStorage; no `audit` option: the rollback writes no audit row, a new login may) | `installed` = A, a new `boot_id`, data counts identical to 3.1 |
| 3.7 | Owner | دوس «حدّث البرنامج الآن» تاني عشان الجهاز يفضل على آخر نسخة. | زي 3.2، و«النسخة المثبتة» = **B** |
| 3.8 | Claude | Evaluate the runner again, then `await visit.post()` once more (still before any test sale); post the 3.1–3.8 table on #18 | G6 «Windows حقيقي» can move to ✅ in `STATUS.md` (manager) |

Rollback commands for 3.4 (Claude, `cmd` on the PC; the program keeps running the new code until it is stopped):
```bat
cd /d "%LOCALAPPDATA%\PharmacyPlatform"
set PHARM_DATA_DIR=%LOCALAPPDATA%\PharmacyPlatform\data
"%LOCALAPPDATA%\Programs\PharmacyPlatform\python\python.exe" "%LOCALAPPDATA%\Programs\PharmacyPlatform\program\manage.py" update --rollback
powershell -NoProfile -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like \"$env:LOCALAPPDATA\Programs\PharmacyPlatform\*\" } | Stop-Process -Force"
curl.exe -s http://127.0.0.1:8000/api/health
```
The filter stops only this program's Python, never E-Stock. Rollback changes the code only, never the data
(`docs/UPDATE.md`). If 3.2 fails (red message), copy the message to #18 and skip to test 1: a failed update keeps the old code.
Since #288 (16/11) a program refuses data written by a newer program (a database update it has no file for) and changes
nothing. The in-app update never carries a database update (`AGENTS.md` §4), so 3.4–3.6 are not affected; a database
update delivered by an installer cannot be undone with `update --rollback` (the old code would refuse to start on it).

### Test 1 — receipt printer switched off during a sale (#98), 15 min
Use the test item only. If #240 is not merged, step 1.3 shows only the drawer line; that is the known gap #240 fixes, not a new failure.
| # | Who | Step (owner's line in Arabic) | Expected |
|---|---|---|---|
| 1.0 | Claude | Read the receipt settings | Printer `win:POS-80C`, drawer with cash on; note the drawer timing |
| 1.1 | Owner | الطابعة شغالة: اعمل بيع نقدي لصنف التجربة، دوس F4 (إيصال) وبعدين F9. | الإيصال يطلع والدرج يفتح، ومفيش أي تحذير |
| 1.2 | Owner | اقفل طابعة الإيصال من زرار الكهربا بتاعها، وسيب السلك متوصل. | — |
| 1.3 | Owner | اعمل نفس البيع تاني: صنف التجربة، F4، وبعدين F9. | البيع يتحفظ (رقم فاتورة)، وفي خلال 6 ثواني يظهر تحذير أحمر فيه سطرين: «الإيصال لم يُطبع مباشرة… الطابعة لا تستجيب… اتلغى أمر الطباعة» و«أمر فتح الدرج لم يصل للطابعة». **مفيش** «أُرسل» |
| 1.4 | Owner | لو ظهرت شاشة طباعة من المتصفح: دوس «إلغاء». ماتطبعش منها. | الشاشة تتقفل (الطباعة منها كانت هتستنى في ويندوز وتطلع بعدين) |
| 1.5 | Claude | `Get-PrintJob -PrinterName POS-80C`; `await visit.printAudit("<time before 1.3>")` | Queue empty; audit `print.receipt` = `not_printed`, `cash.drawer_auto` = `failed` |
| 1.6 | Owner | استنى دقيقة، وبعدين شغّل الطابعة وبص عليها وعلى الدرج نص دقيقة. | **مفيش حاجة تطبع والدرج مايفتحش** |
| 1.7 | Owner | دوس «اطبع إيصال آخر فاتورة». | إيصال واحد يطلع، والدرج مايفتحش |
| 1.8 | Owner | اقفل الطابعة تاني، ودوس F8 (فتح الدرج، السبب «فكة»). وبعدين شغّلها. | تحذير إن الأمر ماوصلش، وبعد التشغيل مفيش حاجة تطبع والدرج مايفتحش |
| 1.9 | Claude | Post 1.0–1.8 on #18 (Arabic answers as said by the owner) | If all pass, #98 can be closed with this evidence |

### Test 2 — scanning a label printed in Windows-driver mode, 10 min
| # | Who | Step (owner's line in Arabic) | Expected |
|---|---|---|---|
| 2.0 | Claude | Read the label settings | Type «تعريف ويندوز» (`driver`), printer `win:Xprinter XP-235B`; if not, set them with the owner and save |
| 2.1 | Owner | من كارت صنف التجربة اللي ليه باركود 13 رقم: اطبع ملصق واحد. ومن صنف مالوش باركود (بالكود الداخلي): ملصق واحد. | ملصقين كاملين، الباركود مش مقطوع، والكتابة معدولة مش مقلوبة |
| 2.2 | Owner | في شاشة الكاشير، والمؤشر في خانة البحث: امسح الملصق الأول بالقارئ. | الصنف يتضاف **مرة واحدة** (الكمية 1) |
| 2.3 | Owner | امسحه تاني. | نفس السطر يبقى الكمية 2، مش سطر جديد |
| 2.4 | Owner | امسح الملصق التاني (الكود الداخلي). | الصنف التاني يتضاف |
| 2.5 | Owner | دوس في خانة العميل، وامسح الملصق الأول. | الصنف يتضاف، وخانة العميل تفضل فاضية، ومفيش شاشة غريبة تفتح |
| 2.6 | Owner | امسح ملصق متلزق على علبة (مش مفرود) من مسافة شبر. | يتقري من أول أو تاني مرة |
| 2.7 | Owner | شيل الأصناف من السلة وماتكملش البيع. | السلة فاضية، مفيش فاتورة |
| 2.8 | Claude | Post on #18 with a photo of the 2 labels | If 2.1–2.6 pass: driver labels and scanner pass for G7 |

**After (Claude):** read the receipt and label settings again and compare them with 1.0 and 2.0 (B14). Do **not** run `visit.post` against the test-3 snapshot here: tests 1 and 2 added sales, stock and cash on purpose, so its data rows would FAIL. Post the summary on #18 and the end report in `docs/HANDOFF.md`.

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
`visit.day19()` checks the #19 end-of-day figures (it reads today's sales report and the open or last shift, so on
the trial day do not run `visit.flows()` or `visit.print("sale")`: they add TEST sales). Contract and browser tests:
`tests/test_visit_runner.py`.

| Row | Runner call | Result it gives |
|---|---|---|
| B1 | `visit.pre()` | Backup name, size, `last_ok` (sha256 and the USB copy stay manual) |
| B2 | `visit.pre()` | Health, counts, stock totals, cash, settings, channel |
| B5 | `visit.post(snapshot)` | New `boot_id`, `/` no-cache and `app.js?v=`, every snapshot value line by line |
| B6 | `visit.post(…, {channel, noUpdate})` | Channel unchanged, no update offered |
| B7 | `visit.print("labels", {label_shift: -2})` | NEEDS-OWNER: how many labels, whole, no blank |
| B8 | `visit.print("sale" / "reprint" / "drawer")`, `visit.printAudit(since)` | NEEDS-OWNER questions; (b) audit `not_printed` + `failed` |
| B9 | `visit.flows()` | Credit sale refund defaults to «على حساب العميل» (`credit`) on the real screen; double click and a retried POST each give one return |
| B13 | `visit.post(snapshot, {audit: 1})` | New `boot_id`, data unchanged, audit +1 |
| B14 | `visit.post(snapshot)` | Receipt, label and drawer settings unchanged |
| L2 | `visit.flows()` / `visit.estock()` | Cut-off E-Stock stock file refused with `estock_totals_mismatch` (upload through `/api/imports`) |
| L3 | `visit.flows()` / `visit.estock()` | Complete single-store file accepted (draft discarded, nothing imported) |
| L6 | `visit.day19()` | DAY19_SCRIPT.md end-of-day figures; after the close, `D-close` passes for the script's own close (counted 170.50, difference −0.16: no coin under 0.25) |
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
| B6 | «المزيد» → «البرنامج والنسخ الاحتياطي» → section «البرنامج»: update channel | Owner looks, Claude reads `GET /api/system/update` | «قناة التحديث: develop (تجربة)» (#107); "no update" right after install |
| B7 | #100 labels XP-235B 38×13 (gap measured ≈ 3 mm). Shift alone cannot fix it (11/11 evidence on #100), so this visit tries named TSPL job variants. **0. Set on the screen (do not save yet):** TSPL, `win:Xprinter XP-235B`, 38×13, gap **3**, dpi 203, shift **0**, variant `current`. **1. Calibrate:** «معايرة بكرة الملصقات» (`POST /api/print/label-calibrate`, same body as the test; audit `print.label_calibrate`): the printer feeds a few blank labels and stops by itself at a gap. If it never stops or stops mid-label, send it again with `"mode": "auto"`. Then one press on the printer's FEED button: exactly one label, stopping at the gap. **2. Variants, shift 0, in this order, 3 test labels each** («اطبع 3 ملصقات تجربة» with the variant picked, or `POST /api/print/label-test` with `label_variant`): `current` → `tear` → `clamp` → `tear_clamp` → `per_label` → `tear_clamp_per_label` → `home` → `all`. One photo per variant with its name written next to the strip. Stop at the first where all 3 copies sit whole inside their own label, back to back, no blank label before or between them (`home` may feed one blank label first: that one does not count). **3.** If the winner is whole but 1–2 mm off, keep that variant and move shift by 0.5 mm (positive = lower, proved 11/11). **4.** If none passes: calibrate again with `auto`, repeat `current`, `tear_clamp`, `all`, record the photos on #100 and stop (next step needs the owner: copy the commands E-Stock's driver sends). **5. Save:** variant + shift + gap 3 → «احفظ» (or `PUT /api/settings/receipt` with `label_variant`, `label_shift`, `label_gap`); then 5 labels from an item card. **6.** Extra: shift 0 with the saved variant, 3 test labels: as in step 2 (the printer keeps no old SHIFT) | Claude sends, Owner looks and photographs | Calibration stops on its own; FEED = one label; one variant gives 3 whole labels in a row; after save, 5 labels whole with no blank between them; the variant, shift and gap saved are on #18 and #100 |
| | **للمالك:** الأول «معايرة بكرة الملصقات» والطابعة هتسحب كام ملصق فاضي وتقف لوحدها، وبعدين نجرّب طرق الإرسال واحدة ورا التانية (3 ملصقات كل مرة) وتصوّر كل شريط، وأول طريقة تطلع فيها الثلاثة كاملين ورا بعض من غير ملصق فاضي نحفظها. | Owner | — |
| B8 | #98 receipt printer POS-80C. (a) printer **on**: cash sale with receipt, long receipt (10+ lines) | Owner | Prints normally, no warning |
| | (b) printer **off**: cash sale with receipt (F4 then F9) | Owner, Claude reads `Get-PrintJob -PrinterName POS-80C` and the audit | One red warning with two lines: «الإيصال لم يُطبع مباشرة» and «أمر فتح الدرج لم يصل للطابعة»; queue empty; audit `print.receipt` `not_printed`, `cash.drawer_auto` `failed`. After power on: **nothing prints, drawer stays shut** |
| | (c) printer on again: «اطبع إيصال آخر فاتورة» | Owner | One receipt, no drawer |
| | (d) printer off: manual drawer open, and F8 | Owner | A warning each time; nothing fires at power on |
| B9 | #114 returns: credit sale to the test customer → invoices → return one unit (a return needs «سبب المرتجع»; without it: «اكتب سبب المرتجع»); then a fast double click on «تسجيل المرتجع» on another return | Owner, Claude checks the DB | «طريقة الاسترداد» defaults to «على حساب العميل» (the sale's own payment); the double click records **one** return; after the return the «إلغاء الفاتورة» box is gone (16/11: no void after a return) |
| B10 | P7 (old failure): covered by B8(b) | — | Pass only if B8(b) passes |
| B11 | P10 (old failure): covered by B7 | — | Pass only if B7 passes |
| B12 | Scanner HH360: scan an item and a printed label, focus in the search box and in the customer field | Owner | Added once, no DevTools, customer field stays empty |
| B13 | Update button (#107): the coordinator merges the small docs PR with this visit's results (after `CI / linux` is green on its head), so `develop` moves; «المزيد» → «البرنامج والنسخ الاحتياطي» → «حدّث البرنامج الآن» | Claude prepares the PR and records the counts again right before the press (B8/B9 add rows), Owner merges and presses | Offers the update; the app restarts by itself; a new `boot_id` and `installed` = the merged `develop` SHA; business-data counts identical to the snapshot taken just before the press, audit = the same rows plus the one `system.update` row; channel still develop |
| B14 | Leave the settings as found: label shift = the saved good value, drawer timing «بعد الإيصال», receipt printer selected | Claude | Settings listed on #18 |

## Home laptop `DESKTOP-0DCSIC2` (no devices)
| # | Step | Who | Expected |
|---|---|---|---|
| L0 | `hostname`; check the install paths and port 8000 per `HANDOFF.md` | Claude | `DESKTOP-0DCSIC2`; anything else: stop |
| L1 | Install the same review installer (R10 if the app is running); counts before and after | Owner, Claude counts | Install completes, counts identical |
| L2 | #116 E-Stock: `manage.py import-estock --stock <file cut off at the end> …` without `--apply` (dry run) | Claude | Refused with `estock_totals_mismatch` |
| L3 | #116: dry run on a complete E-Stock stock export, including a single-store export | Claude | Accepted as before (checks the grand-total line is there in a single-store file) |
| L4 | #114 return form on a credit sale and a card sale | Owner | «طريقة الاسترداد» defaults: credit → «على حساب العميل», card only → «بطاقة», cash → «نقدًا من الخزينة» |
| L5 | #107: Settings shows channel develop; update button after the same docs merge as B13 | Owner | Same as B13 |
| L6 | #19 dry run of [DAY19_SCRIPT.md](DAY19_SCRIPT.md) (optional; the trial day itself runs on the Heikal PC, section below) | Owner | The end-of-day figures match the script |

## #19 trial day — Heikal PC `desktop-gqgl6uf` (test machine, synthetic data only; its own E-Stock data is never touched)
Owner's script: [DAY19_SCRIPT.md](DAY19_SCRIPT.md); the steps only his hands can do (printer, drawer, scanner):
[DAY19_OWNER_HANDS.md](DAY19_OWNER_HANDS.md). Both were walked on the real screens on 17/11; `tests/test_day19_script.py`
keeps every quoted screen text and every figure true.
| # | Step | Who | Expected |
|---|---|---|---|
| T0 | `hostname` | Claude | `DESKTOP-GQGL6UF` (any letter case); anything else: stop |
| T1 | The database is the trial one: no real items, customers or sales; `r = await visit.pre()` | Claude | Backup PASS; counts noted on #19 |
| T2 | Hands list rows 1–4 (printer chosen and saved, test page, drawer) | Owner | As the list says; a missing device is written down, not a failure of the program |
| T3 | Script «قبل اليوم» (the two trial items), then hands row 5 (a barcode on «صنف تجربة أ»), then steps 1–11, with hands rows 6–11 inside steps 3, 4, 5 and 7 | Owner | Each «المفروض تشوف» cell |
| T4 | `(await visit.day19()).md` before step 12 | Claude | Every row PASS (no `D-close` row yet) |
| T5 | Script step 12 (count 170.50 with a note) and hands row 12 | Owner | «عجز» 0.16; shift report printed |
| T6 | `(await visit.day19()).md` again | Claude | Every row PASS, `D-close` included |
| T7 | Optional: the recall check under «بعد المطابقة» in the script | Owner | «منشور سحب مؤكد لا تُباع» |
Not on this day: `visit.flows()`, `visit.print("sale")` (they add sales and break T4/T6). A row that fails: photo, stop
at that step, its own Issue.

## After the visit
Every line marked pass / fail with evidence on #18; failures = new Issues; `HANDOFF.md` next steps
updated; then #19 ([DAY19_SCRIPT.md](DAY19_SCRIPT.md)) and #20.
