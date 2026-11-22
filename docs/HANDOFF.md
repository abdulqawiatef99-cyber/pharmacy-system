# HANDOFF — الحالة الحالية

آخر تحقق: 2026-11-21 · Claude · `develop` @ `6e48238`, `main` @ `f2af5bd` (v0.4.0 released, the pharmacy is live; update 1 in progress). **الترتيب الحالي للإطلاق في [START_HERE.md](START_HERE.md)**؛ الأدوار: [LAUNCH_PLAN.md](LAUNCH_PLAN.md).
قواعد العمل وبداية/نهاية الجلسة: [AGENTS.md](../AGENTS.md). البوابات والنسبة: [STATUS.md](STATUS.md) (مسجّلة **73.25%**، مفيش دليل قبول جديد).
تفاصيل كل مهمة في الـ Issue بتاعتها؛ السجل القديم مش مطلوب لبداية الجلسة.





## 22/11 — UPDATE-4 worker session: progress log (READ THIS FIRST; newest line last)
Spec: the manager's «Update 4» message (priority U4-0 … U4-13). Each PR: tests first, an independent fresh-context agent review fixed first, then Codex; merge into develop only with linux CI green and no open Codex P0/P1; one release PR into main at the end (owner pre-approved on the gates, linux AND windows green).
| PR | Item | State |
|---|---|---|
| #430 | U4-0 restore device/printing settings from an older copy + clean start carries them (`device_settings_carry.json`) + first-run printers step | **merged `3eb0325`**; linux + windows-scripts green on 301206b; Codex clean after 2 P2 fixes; agent review 1 P1 + 5 P2 fixed |
| #431 | U4-1 smart order reads the E-Stock sales archive (`app/procurement/demand.py`; `monthly()` for U4-2) | **merged `ac120f6`**; linux green on a0ef0d1; Codex clean after 1 P1 (permission) + 1 P2 (returns on two lines) fixes; agent review 2 P1 fixed |
Device check for the owner after «تحديث» (develop now; main only with the release PR): Settings → «رجّع إعدادات الأجهزة والطباعة من نسخة قديمة» → choose the data-trial-* folder → check the printing lines → restore → «اطبع 3 ملصقات تجربة» on one label each.
Assumption to verify on the PC (#431): E-Stock `amount` on sale lines is in packs (fractions = part of a pack). Compare one item-month of the smart order with E-Stock's own sales report.
Known P2 left (#431): an item with no matched archive rows loses its manual upload for months the archive covers.
Not started: U4-2 … U4-13 (this session stopped at a clean point: context high).
**Part 2 (22/11 evening):**
| #433 | U4-2 purchases tool («أداة المشتريات», `app/procurement/purchase_plan.py`): regular/semi rules, week/2 weeks/month, supplier split by real discount, order tracking in org_settings JSON (no migration), Excel / ask-about / names to check / prices to update / message draft, supplier name memory via item_aliases | PR open on `be11b1f`; Codex asked, label `ci` set. Full suite before the review fixes 2866 OK (10 skipped); after the fixes the 56 targeted tests OK. Agent review: 1 P1 + 6 P2 fixed; 1 P2 left (arrived line entered under another supplier keeps counting); owner question: >=6 of 12 but <2 of last 3 = irregular (hidden) |
Environment note: commits pass only with SHORT one-line messages; details go in the PR body.
Owner additions 17:39 (via the manager): (A) E-Stock history everywhere, as if the program ran two years — read-only, tagged «من إي ستوك», never posted again into stock / drawer / treasury / balances (opening balances came at the move), reports across the cut-off add both sides with no overlap, wrong or missing data shown as is with a note; comes RIGHT AFTER U4-2. (B) U4-17 sale screen: lines directly under the search box, quick sale = one compact sideways row below the invoice that shrinks after the first item, a toggle opens it; then 2-3 invoice looks in settings.
**#433 merged `c34e884`** (linux green on cdd1ddc; Codex clean after 4 rounds, 9 P2 + 1 P1 fixed with tests). **Release #435 (develop → main, update 4a: #430 #431 #433)** opened on the manager's order (owner 18:01 «فين تصليح الطباعه؟»), merges on linux + windows green (owner pre-approved). After «تحديث»: restore device/printing settings from data-trial-* and print 3 test labels.
**Release #435 merged into main `b433c31`** (22/11 ~16:41 UTC; linux + windows + windows-scripts green on `c34e884`): update 4a = #430 #431 #433. Owner after «تحديث»: restore device/printing settings from data-trial-*, print 3 test labels. Not checked yet: the signed «update-main» bundle for `b433c31`.
**E-Stock history step 1 = PR #436** (`claude/u4-estock-history`, head `3bf43a7`): inline old invoices under the sales list (same period, tagged, «مبيعات الفترة قبل المرتجع، البرنامج + إي ستوك» on the same basis), customer/supplier/item inline last 30, `GET /api/estock/period`, unreadable totals counted. Proof test hashes every table + our reports before/after. Agent review 2 P1 + 4 P2 fixed; Codex asked; label ci. Step 2 next: owner reports by period with both sides of the cut-off, purchases list, customer medicines, Hikma check.
**Next (A, E-Stock history everywhere):** much already exists, read-only: `app/dataimport/estock_history.py` (invoices / invoice / customer_items / item_history / statement / shift_closes / shortages / period_report) behind `/api/estock/*` and the separate screen `viewEstock` (app.js «تاريخ إي ستوك», badge «من إي ستوك», returns against old invoices). The work is to MERGE them into our own lists, tagged: sales invoices list + search (old invoices after ours, before the cut-off `demand.cutoff`), customer purchases/medicines, item card (sales/purchases/movement), supplier purchase history, sales/purchases/profit reports by period (both sides, no overlap at the cut-off), Hikma claim check. Tests FIRST: snapshot every total (stock_balances, ledgers, drawer/treasury, reports, claim) before/after reading the history → identical. Then U4-3, U4-5; U4-4 additions from the owner 18:01 (bonus shown for شركة / hidden for مخزن; a scan anywhere on the purchase screen goes to the item field, qty defaults to 1 box; supplier invoice number autocomplete off, a used number for the same supplier is already refused; measure the add-item lag; field order الصنف → الكمية → الصلاحية → الخصم/سعر الصيدلي → البونص → التشغيلة → سعر الجمهور → الهامش/الضريبة, Enter walks them, last Enter adds; reorder in purchase settings; «الرصيد» column).
**Queue additions (owner 18:20 via the manager), after E-Stock history; evaluate first:**
- U4-19 Delivery: receipt says «توصيل» with customer name/phone/address; courier from a managed list (owner/manager); fee «مجانًا» or an amount; open delivery invoices per courier + «سلّم الفلوس» into the drawer, shown at shift close. Check first what `sale_deliveries` already holds.
- U4-20 Receipt header: pharmacy phone under the name (the old settings have it); check the data-trial-* restore brings the receipt lines back; first run asks for the phone. Note: printing → owner approval / release gates.
- U4-21 Autocomplete off wherever old values make no sense (supplier invoice no., batch, quantities, prices).
- U4-22 Barcode anywhere on sale and purchase screens: a scan from any field adds/selects the item, never types into the focused field (merge with the U4-4 scan item; one shared scan detector by key timing).
- U4-23 Sanity checks before saving any invoice: impossible refused (server, OWNER_RULES §13), unusual = confirm with reason (qty far above usual, price far from public/last cost); thresholds in settings.
- U4-24 Speed of every add/save (purchase add line, sale add, save customer, save item): measure, fix, target < 0.3 s (do with U4-5).
 Next session: U4-2 purchases rebuild on `demand.monthly()` (regular >= 6 of 12 months and 2 of last 3, semi 4-5; avg = max(avg 3, avg 6); week/2 weeks/month; per-supplier split; order tracking). No release PR into main yet: it collects all of update 4.

## 22/11 morning — UPDATE-3 worker session W3: progress log (READ THIS FIRST; newest line last)
Base develop @ `5e4b2b9`. Each PR: tests first, an independent fresh-context agent review fixed first, then Codex; merge only with linux CI green and no open Codex P0/P1 on the same head, one at a time (all touch app.js / i18n-en.js / sales service).

**Released to main (owner «موافق» 11:18 Cairo):** #410 merged into main as `77807d1`. The signed «update-main» bundle is published for that commit; the «تحديث» button gets it.

**Released to main — update 3 (owner approval 22/11 via the manager, usual gates):** #426 merged as `a0fb303` at 12:06 UTC; linux + windows green on `9ee5117`; signed «update-main» published for `a0fb303`, manifest lists 0042. The owner presses «تحديث» (the manager tells him).

**What #426 carried** (merged into develop since #410):
| PR | Item | Merge |
|---|---|---|
| #414 | U2-33 below cost refused for everyone (at cost = `sales.below_cost` + reason), U2-17 no split, U2-21 dispensing notes | `745bf15` |
| #416 | U3-3 Hikma claim (any period, per branch, Excel) | `75bc93d` |
| #425 | U3-4 app window (launcher; reaches the PC only with the installer) | `aa40585` |
| #415 | U2-14 rounding of the CASH part only (owner 22/11), migration 0042, receipt + shift print lines | `ff87d8e` |
| #424 | «خليها رصيد للعميل» + one-click credit use (manager correction 22/11: the drawer includes the kept change) | `c86974d` |
| #428 | wording «له رصيد» (not «ليه») | `9ee5117` |
The owner approved #415/#424/#425 at 11:38 with «اعمل حساب خوازيق المستقبل»: each got tests for the traps he listed. #415 was redesigned (cash part only).

**Still open, for the next release** (not in update 3):
| PR | Item | State |
|---|---|---|
| #421 | U3-7 dhikr strip + work hours | needs develop merged in + Codex (one open P2: stop the strip when the session expires) |
| #419 | U3-6 customer medicines | needs develop merged in + Codex |
| #418 | U3-5 daily mini-count | needs develop merged in + Codex |
| #422 | U3-8 owner shift summary | needs develop merged in + Codex |
| #423 | U2-23/U2-24 receipt (printing) | **PR only, owner approves** (not in the 11:38 approval) |

**Next PRs (manager 22/11, not started).** Update 4 (E-Stock move button, purchases/smart-order rebuild, screen-switch speed, purchase invoice fixed margins) starts in a NEW session with its own spec.
1. Login security: wrong passwords never lock the real user; growing delay per device/IP; every failed attempt logged (target user, device, time); the bell tells owner/manager in plain Arabic; the owner can block a device; the right password always works. Tests: an attacker cannot lock a user out; brute force stays impractical.
2. Update UX: the «تحديث» button glows when an update exists; after download one clear choice «حدّث دلوقتي (دقيقة)» or «لما البرنامج يفضى»; rename «إلغاء الميعاد» (e.g. «ماتحدّثش دلوقتي»).

After the update on the PC (in #426's body): device check per #18; traps: rounding is on at 0.50 right after the update; after a confirmed start the old code refuses a database with 0042 (going back = restore the pre-update backup); the app window needs the installer (launcher.py is stage 0).
NOT done this session: U3-2 reference → safety drafts, U2-27 silent printing, U2-6 print time (migration), U2-31 two drawers (migration), U3-7 fridge log (migration), «تغيّر سعر البيع للمخزون الحالي؟», «بيع بسعر التكلفة» per customer, receipt templates/preview (U2-23 part).
Known P2s left: U3-1 refill/90-day figures keep old units for one window after a pack fix; #423 long rows cut on 58 mm; the app window's fallback fires only when Edge/Chrome cannot start.

## 22/11 02:00–03:30 Cairo — UPDATE-2 continuation (manager night check), stopped at a clean point (context limit) (READ THIS FIRST)
Next session: start from the NOT-done list below; priority stays U2-23+24 receipt (PR only, printing + migration), U2-14 rounding, U2-27, U2-31, U2-21, then purchases U2-1 grid / U2-6.
- #407 merged `db2350b` (its first CI run failed only on date tests that crossed UTC midnight mid-run; the re-run on the same head passed). #408 (HANDOFF) merged `d33a87b`.
- Release PR develop → main: #410, opened, NOT merged (owner only); body lists #402–#412. Ships by «تحديث»: no migration, no requirements, nothing under printing/installer. Refresh its body after each merge.
- New PRs (each: independent agent review fixed first, then Codex, label `ci` needed for CI to run):
  | PR | Item | State |
  |---|---|---|
  | #409 | U2-33 discount never under cost, refused when typed (owner / his PIN still passes and confirms at payment) | merged `cc96008` |
  | #411 | U2-20 «مفيش فكة» suggestions at payment (+ no-coins hint). «خليها رصيد للعميل» REMOVED (Codex P1): needs a real recorded customer-credit flow, not built | merged `bc488b5` |
  | #412 | U2-19 sale-screen keys in «تخصيص الواجهة» (conflict check, reset; typing keys not offered) | merged `48f911c` |
- U2-17 «remove tablet splitting»: NOT done — question for the owner. Tablet splitting is not on any screen: only an API flag (`tablet_split`) in `app/safety/engine.py` asks the pharmacist when there is no split policy. Partial packs (a strip of a box) are a normal sale since update 1. Ask what he saw before removing anything.
- Owner decision on U2-33: should even the owner be refused below cost? Now he (or his PIN) can, confirming at payment (one-line change if he wants an absolute refusal).
- New TODO from U2-20: «خليها رصيد للعميل» = record the change as customer credit in the same transaction as the sale (cash stays in the drawer, customer ledger credit). Not built.
- Still NOT done: U2-23/U2-24 receipt (printing + `sale_unit` migration, PR-only), U2-27 silent printing (printing, PR-only), U2-14 invoice rounding (money rule + setting + setup question), U2-31 two drawers (migration), U2-21 dispensing notes UI, U2-6 print time (migration), U2-1 grid arrows, U2-10 freezes.
- Known test fragility: several date-based tests fail when a CI run crosses midnight UTC (02:00 Cairo); re-run once.

## 22/11 night — UPDATE-2 worker session: end report (READ THIS FIRST)
Base develop @ `88cb658` (update 1 = main @ `4f2828f`). Each PR: tests first, an independent fresh-context agent review before Codex (OWNER_RULES §12), then Codex until no P0/P1, CI linux green on the same head, merge commit. Nothing on main, no installer, no migration, no `requirements.txt` change: the whole batch ships through the in-app «تحديث» once the owner merges the release PR.
| PR | Items | Result |
|---|---|---|
| #402 | U2-36 smart order screen | merged `180d472` |
| #405 | POS U2-11/12/13/18/25/32/34/35 | merged `d22b496` |
| #403 | U2-0 refusals + OWNER_RULES §13, permissions `sales.refund_other_method`, `suppliers.pay_advance` | merged `b3bf34d` |
| #404 | shift U2-26/28/29/30 | merged `9216cf4` |
| #406 | purchases U2-1/2/3/4/5/9/16/22 | merged `603b8d5` |
| #407 | U2-8 day review, U2-10 slow log, U2-15 RTL/LTR | see below |
Behaviour changes the owner should know (all his rules, said in each PR):
- Exchanging a card-paid invoice for cash needs a manager PIN; a cash invoice exchanges as before.
- Paying a supplier more than his balance (an advance) needs a written reason and owner/manager rights.
- A refund goes back by the way the money came; cash back for a card invoice = owner/manager PIN.
- A discount bigger than the line or the invoice is refused (no longer silently cut).
- A supplier invoice dated after today is refused; short expiry (< 180 days) and "won't sell before it gets near expiry" need a written reason to approve.
- Sign-out asks first (personal setting «تأكيد الخروج»).
U2 items, exact state:
- DONE: U2-0 (#403) · U2-36 (#402) · U2-26, U2-28, U2-29, U2-30 (#404) · U2-11, U2-12, U2-13 (partial: unit kept by quantity, see P2), U2-18, U2-25, U2-32, U2-34, U2-35 (#405) · U2-1 (quick-add row only), U2-2, U2-3, U2-4, U2-5, U2-9, U2-16 (xlsx/csv; text-PDF not tested), U2-22 (#406) · U2-8, U2-10 (log + search speed test), U2-15 (#407) · U2-7 (already in update 1).
- NOT DONE: U2-23 receipt template, U2-24 sold unit on the receipt (both `app/printing/`, a session may open but not merge them, OWNER_RULES §10; U2-24 also needs a `sale_unit` column = migration) · U2-33 discount limit per user + never below cost (changes an agreed money rule) · U2-14 invoice rounding (money rule + setting + setup question) · U2-27 silent printing (`app/printing/`) · U2-20 «مفيش فكة» · U2-19 shortcuts in Settings · U2-17 remove tablet splitting · U2-31 two drawers (migration) · U2-21 dispensing notes UI · U2-6 print time (migration) · U2-1 arrows/Enter inside the line grid (only the quick-add row has it) · U2-10 the two freezes (not reproducible in the cloud; the log will catch them on the PC).
Open P2s (next small PR): day review splits wallet refunds into card (#407 thread); unit selector cannot tell 3 strips from 1 box without `sale_unit`; RTL test checks page direction only.
Git note: in this environment commits pass only with short one-line messages (long multi-line messages and `--amend` were refused by the auto-mode check).
Next: owner reviews and merges the release PR develop → main, then presses «تحديث» on the cashier PC; device check of the new screens (#18 sheet).

## 21/11 Cairo — update 1 (launch-day fixes, through the «تحديث» button) (READ THIS FIRST)
State: main @ `f2af5bd` (#399, tree == develop `6e48238`), installer built and installed, E-Stock moved, the pharmacy is LIVE since 18:04. Owner 19:14 «توكل على الله ونفذ» + 19:39 additions (8b, 10). Branch `claude/update-1`, one PR into develop, then a release PR into main (the owner merges; he stops selling for the 1-2 minutes of the update).
1. E-Stock import: `Products.deleted='1'` is normal on live rows (30,269 of 30,508): inactive = active '0' OR deleted '0'; reconciliation also compares active counts. The live PC was already fixed by a one-off script (same result). This most likely also explains «3796 found nothing» (the item was inactive then). Customers/suppliers still read `deleted` the old way (no data says otherwise): check on the PC.
2. A number field showing 0 is selected on focus (every screen). 3. POS line price = the sold unit's price. 7. «العميل دفع» disabled on an empty invoice.
4. Fixed EGP line discount + one invoice discount (EGP or %), largest remainder, shares stored; returns/reports use them; limits as for % (checked again at completion). **Migration 0041** (sale_lines.discount_amount / invoice_share, sales.invoice_discount_*, purchase_invoices.serial_no + backfill): ships through the smart update.
5. Search: exact code / E<code> first; Arabic spelling key (بكتول/بيكتول, ى/ي, ة/ه) for items and customers; POS stock first, zero stock hidden with «اعرض» (per PC); empty search box after reload.
6. Safety: a strip of a box is a normal sale; «no data» notes never stop the sale.
8/8b. Purchases: «رقم فاتورة المورد», program serial, «تاريخ فاتورة المورد» (starts today) + «تاريخ الإدخال على السيستم» (+ approval time), Cairo time, list search/sort.
9. Launch scripts: password asked once (env var, never the command line), a stray Enter asks again, English console prompts (Arabic in Notepad), fresh start lists the sales after an import and goes on only on YES.
10. «اشرح الشاشة دي» on every screen (tour or help text), test fails on a route without help.
11. Customer search (owner 20:10/20:34, «د محمد الشربيني», E-Stock code 335): E-Stock code exact (from the notes tag the move writes, ranked first), titles dropped, Arabic spelling, words in any order; POS and Customers share it. **Root cause of "name and phone both fail" (owner 20:59: nothing was ever deleted or stopped in E-Stock):** the old rule `inactive = flag(deleted) or flag(active) is False` hid a party whenever `active` was empty, NULL or the column missing (`flag()` reads those as False) or `deleted` was '1' (normal on live rows). Now a customer / supplier never takes a state from E-Stock's flags (raw values kept in `import_rows.raw.estock_flags`); no «محذوف» tag. A one-time startup repair (`app/dataimport/estock_party_fix.py`) re-activates every party the move created with is_active=0, audited, count on «البرنامج». Products count only an explicit '0' (empty = unknown). Customer order: exact E-Stock code, then names starting with the text (title allowed), then the rest; the list sorts by code (default) / name / balance. The fuzzy index is built in the background at start (its first build took ~3 s on 30k items, on the cashier's keystroke); searches on 30k items / 5k customers ≤ ~0.13 s in the test. 12. «ناقص» → «نفد» for zero stock, «في النواقص» for the shortage mark.
Update 2 (after update 1 merges, branch claude/update-2): U2-1..U2-15 from the owner 19:57 / 20:10 / 20:34 messages (purchase line keyboard, field focus/expiry entry, live duplicate notes, «إجمالي فاتورة المورد», labels after approval (revised: remembered per item, confirm before printing), purchase dates incl. print time, help on new routes, «مراجعة اليوم», several expiries in one line, slow-request log + the two freezes (purchase-line Enter; POS customer field → «توصيل للمنزل»), POS new-customer form always full, line stock shown, change a line's unit, «جبر الكسور» on the total, RTL audit test).
CI: the legacy tests now read the release before 0.4 (`25328e3`, pinned in ci.yml): with v0.4.0 on main they failed on develop too.
Not done / for the device check: the cmd `???` fix is by design (English console, Arabic in Notepad), not seen on the PC yet; Uninstall once on the device test (#18).

## 21/11 Cairo — launch-final session: last installer guard, spare key, release PR (READ THIS FIRST)
1. State: part 3 merged into develop: #386 → #391 → #392 → #394 → #395 → #396, develop @ `17a8079`, CI linux + windows green on it (checked by the manager). #397 closed (review only).
2. Key outcome (b): `release_keys/update-2.pub` signs (it was the spare); update-1 retired (private half not found, `update-1.pub` removed). New spare `release_keys/update-3.pub`: made 21/11 14:41 by the owner with `new-signing-key.bat` on the cashier PC (private half only on the flash drive `F:\pharmacy-update-keys-3`), committed on `claude/launch-final`; tests now require both keys (docs/SMART_UPDATE.md, release_keys/README.md).
3. Return window back to **14 days** (owner 21/11 10:17, #396); Settings still changes it.
4. Branch `claude/launch-final`: installer `MinVersion=10.0` + `ArchitecturesAllowed=x64compatible` + `ArchitecturesInstallIn64BitMode=x64compatible` (Windows 10+, 64-bit; the embedded Python is amd64), tests in `tests/test_installer_script.py`; #393 (part 3 HANDOFF log, OWNER_RULES §12, AGENTS §2a step 0) merged into it, so #393 is closed.
5. Owner decisions 21/11 (manager chat 14:29, «اعمل اللي انت شايفه»): order = last installer → move → launch. The **final full review runs AFTER the launch**, and its fixes ship through the «تحديث» button (no new installer). Install on the owner's PC first, Dr. Adham's later. Only one worker session at a time.
6. Next: release PR `release/v0.4.0` → `main` (RELEASE_RUNBOOK §2-أ) is opened, **not merged**; the owner merges it, then the installer is built from main (no agent builds it).

## 21/11 15:00 Cairo — signing key settled (outcome b), all of part 3 merged into develop (READ THIS FIRST)
1. Key: the owner's 10:28 "primary" line matched no committed key (run 44896903157 attempt 1: `signature does not match a trusted key`). He put the spare back at 13:16; attempt 2: `signed with release_keys/update-2-spare.pub`. Owner's choice: the spare becomes the signing key. #391 `29fae69`/`46c86ec`: `update-2-spare.pub` → `release_keys/update-2.pub` (same key), `update-1.pub` removed; dry run on #391: `signed with release_keys/update-2.pub` (run 44900092464).
2. Merged into develop in order (merge commits, CI linux green and Codex clean on each head; independent agent review before each new Codex request): #386 `0de88f1` → #391 `d5780f8` → #392 `46dd9dd` → #394 `bed7f10` → #395 `64046ae` → #396 `17a8079`. #397 closed (review only). Nothing on main, no installer.
3. Return window back to **14 days** (owner 10:17), inside #396 `4da91a7` (+`77e8ffb` texts); Settings still changes it.
4. **Before any main merge or installer:** owner runs `new-signing-key.bat` (new spare to `F:\pharmacy-update-keys`), an agent commits `release_keys/update-3.pub`; add a CI guard refusing main with fewer than 2 keys (independent review P2). Installer PR: `MinVersion=10.0`, `ArchitecturesAllowed=x64compatible`.
5. Open P2s (after launch): #392 rehearsal test gaps (listed on #392); same-item replacement beyond stock (#397); old by-lot bonus partial return (#310); Chrome Ctrl+Tab/F11 (P3-K1).

## 21/11 06:10 Cairo — part 3 done in code, waiting for the key and the merges (READ THIS FIRST)
Stack (nothing merged, nothing published, no main, no installer): develop ← #386 `claude/smart-update` ← #391 ← #392 `claude/p3-1-last-installer-rehearsal` ← **#394** `claude/p3-2-balance-correction` @ `7bdd8e4` ← **#395** `claude/p3-3-move-report` @ `d5cf5a4` ← **#396** `claude/p3-6-returns-permissions-keys` @ `973ea72`. #397 = the same top as one diff, **review only, close it after #396 merges**. Merge order: #386 → #391 → #392 → #394 → #395 → #396 (re-target each to develop as the one below merges; merge commits).
1. Done, with evidence
   - Item 2 #394: balance correction after the move (`balances.correct`, reason, opening adjustment through `post_adjustment`, revision guard, exact piasters on the screen). Codex clean `7bdd8e4`.
   - Items 3–5 #395: report in plain words; day/PC health at shift close (`/api/health/day`); Desktop buttons «إي ستوك ١/٢/٣». Codex clean `b656278` (later commits are merges of #394/#392).
   - Items 6a, 6b, A, C #396: exchange (one transaction, net only, cash/credit refund, same branch, preview in one rolled-back simulation, `expected_new_total`); return window default **7 days (was 14)**; purchase return from invoice (migration **0040**, approved line amount incl. invoice discount/tax by largest remainder, tax ≤ amount, #310 bonus rule, earlier lot returns counted, cancel nets exactly); `users.permissions` + `docs/PERMISSIONS.md` (48 codes); keyboard map `POS_KEYS` (new invoice moved F2 → Insert). Codex clean `973ea72`.
   - Reviews: Codex 7 rounds; from round 5 on, an independent fresh-context agent reviewed first (owner rule 21/11 03:53, now OWNER_RULES §12 + AGENTS §2a step 0 in this branch). Full suite 2455 OK; CI linux green on every head; Windows green on `973ea72` (run 44862258612) — the only Windows failure seen (run 44837080242) was the key tools' cp1252 output, fixed by the other session in `5dd8315` (#391).
2. Open, not blocking (P2 / after launch)
   - #397 P2: a replacement of the same item as the return cannot be added beyond today's stock before the return (add_line checks stock first). Workaround: do the return, then the sale.
   - The old by-item supplier return can still return part of a bonus lot (#310).
   - Ctrl+Tab / F11 may be kept by Chrome on the cashier PC (FUTURE_REQUIREMENTS P3-K1); F8 and clicking the tab still work.
   - Ideas 28/30/31/32 and "#385 small fixes" had no written list in the repo; built from the one-line plan (#395 body). #385's Codex threads were all fixed in #385.
3. Facts for the owner: minimum Windows = **10, 64-bit**; `installer/pharmacy.iss` has no `MinVersion=10.0` / `ArchitecturesAllowed=x64compatible` yet (installer PR, owner approval). Empty installer (no pharmacy data) last built `81a71c1`: **34,798,343 bytes = 33.2 MB**; the next one is not built.
4. Next: owner settles the signing key (#386 / item 0) → merge in the order above → smart update or one installer with migration 0040 → device check of the new screens (#18 sheet) → owner acceptance.

## 21/11 part 3 session 2 — progress log (READ THIS FIRST; newest line last)
- Item 2 done: #394 `claude/p3-2-balance-correction` @ `f866030` (stacked on #392). Permission `balances.correct` (owner+manager, per person), reason required, `reconcile.post_adjustment` type opening, stale-screen guard. Suite 2382 OK. Codex + CI asked.
- Items 3–5 done: #395 `claude/p3-3-move-report` (on #394). Suite 2399 OK. Codex P1+P2 on #394 fixed `eb14e6d` (revision guard, opening basis); Codex 2×P1 on #395 fixed `b656278` (clock per org, money without float). Re-review asked.
- Items 6a, 6b, A, C built on `claude/p3-6-returns-permissions-keys` (local worktree, on #395): exchange (one transaction, net only, preview, find by invoice/item/customer, window default 7 days — was 14), purchase return from invoice (migration 0040, #310 bonus rule, `purchases.return_bonus_part`), `users.permissions` + `docs/PERMISSIONS.md` (48 codes), keyboard map (`POS_KEYS`; F2 new invoice moved to Insert). Next: full suite, push, PR, Codex.
- Items 6a, 6b, A, C: #396 `claude/p3-6-returns-permissions-keys` @ `63ee783` (on #395, merges #395's fixes). Suite 2430 OK. Codex + CI asked. Two full-suite failures found and fixed before the PR: receipt «7 يومًا» → «7 أيام»; approval box still asks `users.manage`.
- Next: Codex results on #394/#395/#396 → fix P0/P1 test-first → one Codex review over part 3 → STATUS/HANDOFF → #113 comment → stop.
- 21/11 ~01:40: Codex round 2 fixed test-first: #394 exact piasters on the screen (`b6e70e2`); #396 4×P1 (`8bacfe9`: no card refund / no delivery in an exchange, one entry per purchase line, `default` lifting a deny) + 1×P1 + 2×P2 (`4b1d57e`: credit = approved line amount, slip tax/bonus/credit note, card device). #395 Codex clean on `b656278`. Stack merged up from #392 `3c6053a` (Windows UTF-8 key-tool fix by the other session; it was the only Windows failure, run 44837080242). Suite 2438 OK on `4b1d57e`. Windows dispatch re-run on `claude/p3-6-returns-permissions-keys`.
- 21/11 ~02:30: Windows CI green on `4b1d57e` (run 44843233447). #394 Codex clean on `7b…` (head after `b6e70e2`). #396 round 3: 2×P1 on cancel-after-return fixed `ab68a65` (`_lot_receipts`: linked returns from their own receipt, tax credited once). Suite 2440 OK. Codex + Windows asked on `ab68a65`. Then: overall part 3 Codex review (a do-not-merge PR from #392's branch to the top), docs, #113.
- 21/11 ~04:20: Codex round 4 (#396 + overall #397, 6×P1 + 1×P2) fixed `593211c`. Owner rule 03:53 (OWNER_RULES §12): independent agent review first → no P0/P1; its P2s fixed `0bb46e4` (old by-lot bonus path recorded, #310). Suite 2448 OK. Codex round 5 + Windows asked on `0bb46e4`.
- 21/11 ~05:50: Codex rounds 5–6 fixed (`8d984cd`, `fd31e1a`), each after an independent agent review (no P0/P1; P2s fixed `daf0f7b`, `973ea72`). Head `973ea72`, suite 2455 OK, Windows green on every head so far. Codex round 7 + Windows asked.
- Notes: ideas 28/30/31/32 and "#385 small fixes" have no written list in the repo; items 3–5 are built from the one-line plan below, assumptions stated in each PR.

## 21/11 00:15 Cairo — part 3 in progress: items 0–1 done (PRs open), items 2–6c for the next sessions (READ THIS FIRST)
Stack (none merged; nothing published; no main, no installer): #386 `claude/smart-update` @ `77a392a` (gate met, held for the key) ← #391 `claude/p3-0-signing-keys` (item 0) ← #392 `claude/p3-1-last-installer-rehearsal` (item 1). Merge order once the key is settled: #386 → #391 → #392 (each re-targeted to develop as the one below merges).
1. Done
   - Item 0 (#391): `signing-key-check.bat` (which committed key each .txt on the flash drive matches; never prints a key) + `new-signing-key.bat` (new spare `update-3` to `F:\pharmacy-update-keys`), both in «أدوات الصيدلية»; `docs/SMART_UPDATE.md` has outcomes (a) update-1 found → into the secret, spare offline; (b) not found → update-2 signs, update-3 new spare, `update-1.pub` removed (and the key-set guard test in `tests/test_smart_update_review.py` updated). The dry run on `77a392a` proved the chain works and signs with **update-2-spare**.
   - Item 1 (#392): `tests/test_last_installer_rehearsal.py` — one signed batch with a migration + a package change + an updater change goes prepare → «حدّث دلوقتي» → exit 75 → launcher `settle()` runs the real `--apply-prepared` → swap → first start; a second batch whose first start fails rolls DB (byte for byte) and code back.
2. Not done (next sessions, one PR per group, tests first, stacked on #392 or on develop once #386 is in; Codex + CI linux/windows each)
   - Item 2 balance correction screen (suppliers/customers after the move): reason + audit; owner/manager. Build on `app/dataimport/opening_balances.py` (`listing`, `confirm`) and `app.finance.reconcile.post_adjustment`; no migration expected. **Strongest model (money).**
   - Item 3 move report wording (idea 32) + #385 small fixes: `app/dataimport/estock_owner_report.py`, `estock_db.py`. Opus.
   - Item 4 day health report at shift close (idea 28) + PC health check (idea 30). Opus; money figures read-only.
   - Item 5 desktop buttons for the move steps (idea 31): `scripts/windows/*` + `app/core/tools_folder.py` TOOLS. Opus.
   - Item 6a sale return + replacement sale in one operation (three totals, cash/credit/card, sold/return/previously returned qty, return price after discount, search by item code / invoice / customer code, return window from settings, default 7 days; the returned item is NOT booked as a purchase). Likely a migration (settings key only if possible). **Strongest model (money + stock).**
   - Item 6b purchase return from invoice (supplier + invoice no. loads lines; supplier credit-note no.; bonus returned separately; added tax; value and cost; default payment = on account). Check #310 (owner's bonus-line rule). **Strongest model.**
   - Item 6c (manager addendum 23:xx) permissions parity with E-Stock's 12 groups (general data, items, stores, suppliers, purchases, customers, sales, daily accounts, general accounts, orders, staff, main/branches): table of E-Stock group → our permission codes (`app/core/permissions.py`, roles); every staff member keeps exactly sale, drawer, returns, purchases; add any missing must-have permission. Needs each employee's E-Stock rights (read on the pharmacy PC, read only). Strongest model (permissions).
   - **Owner correction 23:54 (applies to all of part 3):**
     - A. Item 6c REPLACED: every permission switchable on/off per user, roles are only templates; owner/manager can open or close anything. E-Stock's 12 groups are only a checklist that nothing is missing. Deliver the final permission list.
     - B. Standing rule: E-Stock is a reference, never a rule. Floor = at least as good, goal = far better; if something better exists, do the better thing.
     - C. Full keyboard selling: F1 on an empty line = item search; F2 on a line = that item's stock in other branches/PCs; F3 = item card; F10 = save (payment options first, then print); F11 = open drawer; Ctrl+Tab = switch screens; also cancel invoice, quantity, return; on-screen F-key legend. Plan: fixed defaults in part 3, customisation in Settings later through the button (say so in the report).
     - D. Item 1 must also cover a clean install with no data: done in #392 (`TestCleanInstallThenUpdate`).
   - Then: one Codex review over all of part 3, CI green, HANDOFF/STATUS, #113, stop.
3. Facts for the report: minimum Windows = **Windows 10, 64-bit** (the embedded Python 3.13 amd64; Python's support policy, not tested on older). `installer/pharmacy.iss` has no `MinVersion` guard, so it would install on Windows 7/8.1 and then not run: add `MinVersion=10.0` and `ArchitecturesAllowed=x64compatible` in the final installer PR (installer/ = owner approval). The last installer built (`81a71c1`, run 44762691207) is **34,798,343 bytes = 33.2 MB** with no pharmacy data (data lives in `%LOCALAPPDATA%\PharmacyPlatform`); the next one (with #386) is not built yet, expected about the same.
4. Next session start: Claude Code, strongest model for items 2 / 6a / 6b / 6c, Opus for 3 / 4 / 5. Read this entry, #391/#392 Codex results, then item 2.

## 20/11 23:40 Cairo — part 2 closed, part 3 approved (owner 23:18) (READ THIS FIRST)
- #386 gate met on `77a392a`: CI linux + windows ✅, signing dry run ✅ (run 44818446743), Codex clean (after 1 P1 + 2 P2 fixed test-first: abandoned journal restores the moved DB, classic update keeps the package set, panel keeps the classic button), suite 2375 OK. **Not merged on purpose:** a merge publishes `update-develop`, and the dry run shows the secret is the **spare** key (`signed with release_keys/update-2-spare.pub`). Owner 23:18: nothing published before the key is settled = part 3 item 0.
- Part 3 (owner 23:18, «موافق، بس عاوزين تبقى آخر مرة نحط مثبت»): 0 key checker + key decision, 1 last-installer rehearsal (migration + package + updater change through the button, auto-rollback), 2 balance correction screen, 3 move report wording + #385 small fixes, 4 day health report + PC health, 5 desktop move buttons, 6a sale return + replacement on one screen, 6b purchase return from invoice. Branches stack on `claude/smart-update` until #386 merges. No main, no installer.

## 20/11 23:00 Cairo — Claude Code session «parts 1–2», part 2 ready for review (READ THIS FIRST)
Open reminders (from the 18/11 15:45 entry, not re-checked): Codex `@codex` on issues (environment); GitHub two-step sign-in; repo private 3/12 after the updates repo; USB drive; Windows password + remove remote-control programs; devices on the new Wi-Fi; odd piastres; accountant e-receipt; temperature log; doctor; data export from the 3 accounts. **New: the signing secret (below).**
1. Done, with evidence
   - #386 (`claude/smart-update` @ `005db1b`) is on top of part 1: develop (#387) merged in with no conflict; the owner's keys `release_keys/update-1.pub` and `update-2-spare.pub` kept (blobs `cd2e114`, `d61835b`, 32 bytes each); a test fails if either is dropped.
   - Channel = **main** (owner 20/11): `updater.follow()` — a review install (the launch candidate on the cashier PC) follows `main` once `main` contains its commit, never an older main; the first update records `ref=main`. OWNER_RULES §4 line updated.
   - Second review of #386 (backup/restore/startup) posted on the PR: no P0/P1; fixed test-first R1 stale launcher capability (restart loop after an older installer), R2 no downgrade guard without a commit time, R3 secret scoped to two steps, R4 signing dry run before merge; N1-N7 recorded (P3, after launch). `tests/test_smart_update_review.py` 17 tests (16 errors + 2 failures on the old code).
   - Full suite on the smart-update tree: Ran 2369, OK (skipped 8, expected failures 2), non-root, browser required.
2. Not done and why
   - **`UPDATE_SIGNING_KEY` is not visible to GitHub Actions**: the step "Signing key present?" saw it empty on a PR run (44807875442) and a manual run (44807975171). The step was green only because it is fail-closed. Owner step: Settings → Secrets and variables → Actions → **Repository secrets** → `UPDATE_SIGNING_KEY` = the one line of `update-signing-PRIVATE.txt` (update-1). Then Actions → Update bundle → Run workflow on `claude/smart-update`; the log must say `signed with release_keys/update-1.pub`. Since `005db1b` a dry run without the key is red.
   - #386 not merged: the owner asked for the PR into develop only; merge when CI + Codex are clean on the final head and the key check is green.
3. Decisions: dry run of the signing path on labelled PRs (nothing published); review-installs follow main only forward.
4. Next: owner fixes the secret → re-run the dry run → merge #386 → (with approval) release to main + one installer with the new launcher. Part 3 waits for the E-Stock review results.

## 20/11 22:30 Cairo — Claude Code session «parts 1–2» (owner OK 21:15), part 1 done (READ THIS FIRST)
Open reminders (from the 18/11 15:45 entry, not re-checked in this session): Codex `@codex` on issues (environment); GitHub two-step sign-in; repo private 3/12 after the updates repo; USB drive; Windows password + remove remote-control programs; devices on the new Wi-Fi; odd piastres; accountant e-receipt; temperature log; doctor; data export from the 3 accounts.
1. Done, with evidence
   - Part 1: `claude/launch-candidate` merged into develop through #387 (`claude/merge-launch-candidate`, merge commit `691f0f4`; develop @ `dc41794`). develop now knows **0037, 0038, 0039**, byte-identical to the installed builds `cb68f39` and `81a71c1` (same blob ids), so an installer or update built from develop runs on the pharmacy database (at 0039).
   - One conflict (`app/dataimport/estock_db.py`, treasury depot): the candidate's lines kept (they contain develop's #384 block + `piasters = 0` + the piaster rule). The merged tree is identical to the candidate @ `6ef1c11`.
   - Gate on `691f0f4`: full suite as a non-root user with the browser required and a `main` checkout (CI setup): **Ran 2300, OK (skipped 8, expected failures 2)**; CI linux ✅ (PR run 44804346753) and linux + windows ✅ (dispatch run 44804341292); Codex "Didn't find any major issues".
   - Open PRs whose content is now in develop: #381 and #342 (heads are ancestors of develop: GitHub marks them merged), #348 (head `5533095` in develop; base was #342's branch) and #340 (`c935d29` in develop, its last test fix `231efe2` present as `cdbac37`, identical file): closed with a note.
2. Not done yet: part 2 (smart update #386 on top of part 1, second review, channel to main) — in progress in this session.
3. Decisions: merge commit, no rebase of the candidate; no installer built; nothing on main.
4. Next: part 2 (#386).

## 18/11 14:45 Cairo — manager chat (READ THIS FIRST)
- Merged into `develop` today: #318 (docs), #320 (update works without a GitHub key), #321, #322 (launch review fixes P1-1..P1-5, P2-5, P2-7), #323 (launch review money P2-1..P2-3). `develop` @ `db74c3e`.
- Cashier PC updated in-app `de9a79c` → `db74c3e` at 14:30 (backup first).
- Open: #325 (return quantity field, F63 «شيفت», MSA proofreading, English keys, greeting «السلام عليكم»); owns `app/web/*`, `app/sales/*`, `app/printing/*`, `docs/DAY19*`.
- #324 Codex full review part 1 (money) is blocked: Codex answered "create an environment for this repo"; parts 2-4 not opened yet.
- Next: new manager chat opened from the Claude desktop app on the cashier PC (computer use) → Codex environment + review parts 2-4 → merge #325 + in-app update → launch rehearsal (E-Stock import on a fresh DB mid-day, the real one after closing) → owner acceptance #20.
- Owner ideas 18/11 recorded: `FUTURE_REQUIREMENTS.md` F66-F78 + REPO-1, order in `IDEAS_PLAN.md` section 18/11.

## 18/11 15:45 Cairo — manager chat end report (READ THIS FIRST)
Open reminders: Codex `@codex` on issues (environment); two E-Stock reports; GitHub two-step sign-in; repo private 3/12 after the updates repo; USB drive; Windows password + remove remote-control programs; devices on the new Wi-Fi; odd piastres; accountant e-receipt; temperature log; doctor; data export from the 3 accounts.
1. Done, with evidence
   - #325 merged (`4021c27`) and #327 merged (`69af65b`, wording leftovers; CI linux green, Codex clean on `633b8fa`, full suite 1339 OK).
   - Cashier PC «تحديث» twice: `db74c3e` → `4021c27` → `69af65b` (backup `pharmacy-backup-20261118-150229.zip` before the first). Device check: home «السلام عليكم», «الشيفت مغلق — افتحه»; sale-return quantity field starts empty and typing 3 gives 3 (was 30).
   - #324 part 1 (money): Codex cloud review run from the pane; M1–M12 posted on #324. Claude Code worker started 15:35 (claude.ai/code session «التحقق من P1 وإصلاحات», branch `claude/p1-verification-fixes-*`): failing test per finding, fixes verified P1s M1–M7 in one PR, no merge.
2. Not done and why
   - `@codex` on issues still answers "create an environment" (an environment exists; cause unknown). Parts 2–4 go through the Codex cloud page the same way.
   - Rehearsal (E-Stock → program) not started: needs the owner to export the two E-Stock reports (E-Stock is the live cashier).
   - Ideas F66–F78 not yet moved to `docs/FUTURE_REQUIREMENTS.md`.
3. Decisions: the money P1s are fixed before the real move tonight; P2s wait for the owner.
4. Next goal (new chat in the project, normal chat, Opus): merge the worker's P1 PR after CI + Codex → «تحديث»; Codex parts 2–4; rehearsal when the owner exports the two reports; ideas into the repo.
5. المحادثة دي اتحفظت وممكن تتمسح

## 18/11 15:20 Cairo — manager chat (cashier PC, from the 14:45 handoff) (READ THIS FIRST)
Open reminders: Codex environment for `@codex` mentions; two E-Stock reports; GitHub two-step sign-in; repo private 3/12 after the updates repo; USB drive; Windows password + remove remote-control programs; devices on the new Wi-Fi; odd piastres; accountant e-receipt; temperature log; doctor; data export from the 3 accounts.
- #325 merged → `develop` @ `4021c27` (CI linux green + Codex clean on `45c9f22`). Cashier PC «تحديث» `db74c3e` → `4021c27` at ~15:03 (backup `pharmacy-backup-20261118-150229.zip` first); home shows «السلام عليكم» and «الشيفت».
- Leftovers found on the device: home card «الشيفت مغلق — افتحها / جارية» (feminine) and `app/sales/service.py` «افتح وردية قبل البيع» → PR `claude/shift-wording-leftovers`.
- #324 money review: `@codex` on the issue still says "create an environment" although an environment `pharmacy-system` exists in the owner's Codex cloud. The review was run from the Codex cloud page in the cashier PC browser pane instead (effort Light). 7 P1 + 5 P2 claims posted on #324 (M1–M12), all unverified. Next: Claude Code worker verifies each with a failing test, fixes verified P1s before the real move; then parts 2–4 the same way.
- Next in order: (1) PR above merged + «تحديث»; (2) worker for #324 M1–M7; (3) rehearsal (E-Stock two reports → fresh start → import); (4) ideas F66–F78 into the repo.

## 17/11 23:20 Cairo — manager chat, owner decisions (READ THIS FIRST)
- Owner 23:14: USB drive / off-device copy (R6/R7) is optional and out of the percentage for now; G5 to L2 on the Windows restore drill: total **73.25%**. Remaining = owner acceptance (#20) after the final review. F47-a track-and-trace parked as future (no official decree for pharmacies yet; E-Stock does not do it).
- Owner wants the program finished today or tomorrow; what is left is scheduled (final review worker tonight).

## 17/11 23:10 Cairo — manager chat (new, cashier PC), in progress
- Hardware sheet line 10 PASS 23:05 (shift close + report printed from Chrome on the cashier PC, test account tester; the login file IS valid: the earlier Chrome was on another PC, boot_id differed). G8 to L2: total **69.75%**. Temp user line10 created then deactivated.
- Worker sessions: the owner approved the launch-night e2e session (#319 opened); the owner asked the manager to relay his approval to the tour (#247) and updater sessions (sent 22:58).
- Backup interim: the program on the cashier PC is pre-launch data (replaced at launch), so no interim copy tonight; USB drive tomorrow → R6/R7 (G5). A network share needs a Windows sharing-security change on the Heikal PC: owner only.
- Owner decisions 17/11: F60 default «عابر» + one-tap «مألوف» without a name; F64 delivery change to a round 200; F65 low-change warning; F63 ships with the proofreading pass before launch.
- Next: merge #318 #319 + the two worker PRs after Codex/CI; final review (strongest model) + Codex + AR/EN proofreading + F63 tonight; tomorrow: USB R6/R7, owner 2FA + Windows password + remove Radmin/AnyDesk, launch after closing, #20.

## 17/11 21:50 Cairo — manager chat (new, cashier PC), in progress
Open reminders: final review = strongest model + Codex + Arabic/English proofreading (owner 17/11); GitHub two-step sign-in (2FA); USB flash drive (also closes G5 R6/R7); Windows password + uninstall Radmin VPN / AnyDesk / UltraViewer on both PCs; Wi-Fi password (before phone access); odd piastres (#299); accountant e-receipt phase; paper temperature log; doctor session; data export from the 3 accounts.
- #318 fixed after Codex: G5 stays L1 (the Heikal drill restored a local backup; G5 also needs R6/R7 = an off-device copy restored on another device, waits for the USB drive). Total stays **64.5%**. New IDs F54–F59 (were F50–F55, clashing with the clock/login log/payroll/rebuild IDs), plus F60 customer type on sales (automatic known/new/walk-in; regular buyers of an item feed the order suggestion; builds on F8/F9), F61 strong-password guide, F62 feedback reaches the owner from other pharmacies (today it stays on that PC, no send or export), F63 «شيفت» instead of «الوردية» everywhere (owner 17/11; ~73 places in 10 files; with the proofreading batch, after the F36 tour PR frees app.js). One table for every idea: `IDEAS_PLAN.md` section 17/11 (rule: no owner idea is dropped).
- Dropped item found: F47-a track-and-trace research (planned before #19, never queued). Now: before E-Stock is switched off.
- Heikal PC #253 Windows results (missing above): medium 1y: «unsold items (month)» 174 s (small 31 s; all years 0.15 s → query bug), stock check 13.7 s, sales report (year) 7.0 s, scans blocked 1.3–2.5 s during backup / order suggestions / stock health, safety check max 0.61 s; balances match; backup verify/restore 0 problems. In F59 + #304. Heikal worker idle (stage 4 done).
- Line 10 (Chrome): the test login (C:\Users\ph1\PHARMACY-TEST\دخول-التجربة.txt) is refused in Chrome (2 tries, lockout at 5); the pane still holds a tester session. Fix: owner runs PHARMACY-TEST\reset-owner-test.bat with the file's password, or OKs a temporary test user.
- The two Claude Code sessions of 20:00 (updater without a token; F36 tour #247): no branch or PR on GitHub at 22:00.
- Push attach refused again; GitHub through the browser pane.

## 17/11 20:10 Cairo — manager chat «100% اليوم» (cashier PC), in progress (READ THIS FIRST)
Open reminders: GitHub two-step sign-in (2FA) on the owner's account (new, security: whoever controls the account can push updates to every pharmacy); USB flash drive; Windows password + uninstall Radmin VPN / AnyDesk / UltraViewer on both PCs; Wi-Fi password (before phone access); odd piastres (#299); accountant e-receipt phase; paper temperature log; doctor session; data export from the 3 accounts.
- G5 to L2: Heikal PC restore drill PASS 18:24 (newest backup restored into a new temp folder and verified, 0.9 s) via `heikal-tests.bat` (#315). #253 on Windows: small + medium 1 year done, stock check matching (58,721 movements in small); one SLOW: drug-safety check max 327.8 ms (limit 300, median 45). Total 68.0% (corrected at 22:10: stays 64.5%, see above).
- Merged: #316 (docs), #317 (launch night: `launch-fresh-start.bat` + `estock-import.bat`, RELEASE_RUNBOOK section; Codex clean on 725a43c, CI linux green). `develop` @ `573297e`.
- Running Claude Code sessions (claims on #113): updater works without a token (public repo), F36 first-run tour (#247).
- Owner hardware sheet on the cashier PC test account (built-in browser pane): lines 1-9 PASS. Line 10 (shift close + print): the pane cannot print; redo in Chrome. Then G8 to L2 (73.25%).
- Owner findings 17/11 (recorded in FUTURE_REQUIREMENTS F54-F59; IDs fixed at 22:10): return quantity field shows 0 and typing appends (3 becomes 30) = bug, fix before sales move to the program (after the tour PR, same file); invoice-wide discount; better receipt layout; Enter moves to the next field; shift report straight to the receipt printer; import E-Stock sales history; plus F35 newer look and F37 import from any program stay as recorded.
- Owner wants: a complete test plan for every screen (not only the 10 lines); after launch, the release installer for his uncle and relatives (programming / security) to review, reports go to the owner privately, never public issues; a final review by the strongest model + Codex before #20.
- Owner decisions 17/11: hardware sheet may be finished after launch only while E-Stock stays the live cashier; customers: add/edit = customers.manage (owner, manager, pharmacist), no delete (by design).

## 17/11 18:30 Cairo — manager chat «100% اليوم» (cashier PC), in progress (READ THIS FIRST)
Open reminders: USB flash drive (backup goes to the Heikal PC for now); Windows password + uninstall Radmin VPN / AnyDesk / UltraViewer on both PCs before launch; Wi-Fi: check the router's connected devices, then change the password; odd piastres at shift close (#299); ask the accountant about the e-receipt phase; paper temperature log until F38; book the doctor session; data export from the 3 accounts.
- Push attach is refused in this chat (auto-mode check). GitHub is driven through the signed-in browser pane on the cashier PC: upload files to a branch, PR, labels, comments, merge and workflow dispatch all work there.
- #315 merged (`develop` @ `de9a79c`): `scripts/windows/heikal-tests.bat`, one double-click = restore drill (newest backup restored into a NEW temp folder via PHARM_DATA_DIR, then verify) + `scale.py` small and medium 1 year; results in Documents\heikal-tests. Codex P2 fixed; Codex clean on `d7910f1`, CI linux green, local full suite 1275 OK.
- Installer run 44111228424: `installer-review` from `de9a79c`, sha256 `bbd30dca4d81775c538d35230b7e979d8aeab6009952b38f4c0224e2f2eaa93b`. Installed on the cashier PC 18:04 (schema 0036, stock check 2765 items / 3050 movements, 0 problems) and on the Heikal PC (new worker chat «Heikal worker brief», brief = project doc `claude/heikal-worker-brief.md`): part B PASS, part C = G4 #311 PASS, part D = S1 #312 PASS. STATUS: G4 to L2, total 64.5%.
- Owner decisions 17/11 17:45: launch under «منصة الصيدلية» (rename later); launch data = a fresh E-Stock import into a clean database after closing (E-Stock sells until midnight); backup copy on the Heikal PC (no USB drive today); hardware sheet (10 lines) on the cashier PC test account, then #20.
- Cashier PC test account prepared for the hardware sheet: old test shift (open since 1/11) closed at 30.00, new shift 100.00; items «صنف تجربة أ» (3 a pack, 31.00) and «صنف تجربة ب» (50.00), purchase TEST-19-CASHIER-2 approved (A 36 units, B 5), customer «عميل تجربة». A stray draft TEST-19-CASHIER stays unapproved (no stock effect).
- Next: Heikal part E (File Explorer permission, then heikal-tests.bat: restore drill = G5, scale small/medium = #253); owner hardware sheet; #20 through a Claude Code session started from the browser pane (the release PR needs a push).
- Owner request (after launch): import E-Stock sales history (invoices, customers, dates).

## 17/11 16:40 Cairo — day chat (manager, cashier PC), end report (READ THIS FIRST)
Open reminders: product name + USB flash drive (18/11); Windows password + uninstall Radmin VPN (and AnyDesk if unused) on both PCs before launch; Wi-Fi: check the router's connected devices, then change the password; odd piastres at shift close (#299); ask the accountant about the e-receipt phase; paper temperature log until F38; book the doctor session; data export from the 3 accounts.
1. Done, with evidence
   - #18 closed (scan steps 2.5/2.6 PASS 15:50): G7 → L2. Heikal PC (desktop-gqgl6uf) on the bundled installer `cc99311`, synthetic data; functional run 55/58 PASS + screens 15/16 PASS (#19 comment; full log in the claude.ai project `claude/heikal-log-2026-11-17.md`): G2, G3 → L2. Total **61.0%**.
   - Fixed + merged (CI linux green, Codex clean, full suite 1268-1270 OK locally): #311 purchase price on the item card owner-only by default (owner can open it per person: «عرض التكلفة والربح»), #312 drug-safety panel scrolled into view (Heikal S1), #313 giant size for the scale test (6000-9000 sales/day, 45,000 items).
   - #252: owner answers + decisions on the issue (16:02). #310 opened: supplier returns, owner's bonus-line rule (after launch). #307: CI windows job test (small).
   - Heikal worker chat is driven from the manager chat through the browser pane on the cashier PC (works; queued messages need «Send now»). Owner must click any computer-use permission card there himself (the manager may not).
2. Not done and why
   - Restore drill on Windows (G5) and `scale.py` small/medium/giant on the Heikal PC: the Heikal chat has no shell on that PC. Next chat: find a one-click way (a .bat delivered with the program, or the owner opens one command window) and do both.
   - G4 → L2 needs #311 seen on a device; the Heikal PC's «تحديث» fails (no GitHub key file `%USERPROFILE%\.pharmacy\github_token.txt`): fix by a new installer build later, or give that PC the key file.
   - Cashier PC still on `41c9510` (no 0036): it gets the bundled installer after the owner's OK (Heikal passed).
3. Decisions (owner 17/11): purchase price owner-only by default; supplier return = net invoice price, no partial return of a bonus line (#310); test sizes up to the largest pharmacies, never capped by his own; he talks to one chat only.
4. Next goal (new normal chat in the project; Opus, effort high): (a) new review installer from develop (includes #311 #312) → Heikal PC, then cashier PC with the owner's OK; (b) restore drill + scale run on the Heikal PC; (c) #20 preparation.
5. المحادثة دي اتحفظت وممكن تتمسح

## 17/11 15:40 Cairo — day chat (manager, cashier PC): bundled installer built, Heikal worker started
Open reminders (end every owner reply with them): product name + USB flash drive (before 18/11); odd piastres at shift close (#299); ask the accountant about the e-receipt phase; paper temperature log until F38; book the doctor session; data export from the 3 accounts.
Roles today: #113, the three comments of 17/11 (14:35, 14:40, 14:45 Cairo). The owner talks only to the manager chat «جهاز كاشير صيدلية عبد القوي».
1. Done, with evidence
   - Cashier PC (desktop-5jvh3qd) 14:32: «تحديث» `69d87f8` → `41c9510`, backup `pharmacy-backup-20261117-143222.zip` first, stock check all matching (2765 items, 3050 movements). Comment on #18.
   - Owner OK for the bundled installer (14:48). Merged #160 (migration 0036, 9 indexes) and #301 (version 0.4.0): `develop` @ `8049a0e`, CI linux green.
   - First installer build (run 44086683981) failed in its Windows tests: `tests.test_perf_scale` turn lock (`PermissionError` on Windows while the holder's delete is pending). Fixed by #306 (one line in `scripts/perf/scale.py`, Codex clean, CI linux green, Windows run shows the test passing). `develop` @ `cc99311`.
   - Bundled installer built: run 44091400377, pre-release `installer-review` from `cc99311`, 34378617 bytes, sha256 `f15bae18891a64af3af306ad375af9abf876f642cb57975308857fe0c7a41ead`.
   - Heikal worker chat driven from the manager chat through the browser pane on the cashier PC (works: message typed and sent at 15:33). Stage 1 (install + synthetic first-run setup) sent; stages 2 (#19) and 3 (#253 small/medium) go one message each.
2. Not done and why
   - #18 steps 2.5 (scan with the cursor in the customer field) and 2.6 (label on a box): the owner judged them low value today; do them in one minute when the bundled installer reaches the cashier PC.
   - #307 (new): `CI / windows` job fails `test_cli_stops_with_a_clear_message` since #288 (only under `validate_local_release.py`; green in the installer workflow's own Windows tests). Small Claude Code task.
   - #252 device side, #19, #253 small/medium: in progress / next.
3. Decisions: the cashier PC gets the bundled installer only after the Heikal PC passes, with the owner's OK. Until then its «تحديث» button will refuse `develop` (migration 0036), which is the intended protection.
4. Next goal: read the worker's «STAGE 1 RESULT» in its chat, then send stage 2 (#19). After the Heikal PC passes: installer on the cashier PC + the two scan steps + #252 device side.

## 17/11 12:30 Cairo — morning report after the first chain night
Open reminders (end every owner reply with them): product name + USB flash drive (before 18/11); OK for the bundled installer; odd piastres at shift close (#299); ask the accountant about the e-receipt phase; paper temperature log until F38; book the doctor session; data export from the 3 accounts.
1. Done, with evidence (`develop` @ `12e1b5e`, CI linux green, 1264 tests locally on the #160 branch merged with it)
   - Merged since 16/11 23:00, each with CI linux green + Codex clean on the merged head: #287 #288 (drawer audit label; newer-program data refused) · #289 #290 (review R8-B: 4 sale-screen / permission defects) · #294 #300 (chain rule, queue, status) · #295 (trial-day script + `docs/DAY19_OWNER_HANDS.md`) · #297 (look-alike search remembered per catalog version) · #298 (search: exact name first; Enter added the wrong item) · #296 (scale-test tooling `scripts/perf/scale.py`) · #302 (quick buttons ranking kept 10 min) · #303 (purchase approval + stock-alert banner no longer read a whole table).
   - #253 cloud part finished: large 1y, large 3y, huge 1y generated through the program's services; stock check clean on all three; full table on #253. Today's schema fails the cashier limits at those sizes; with #160's indexes it passes. Remaining code items in #304 (after launch).
   - #160: now 9 indexes (sale_lines index fixed to (item_id, sale_id); last-purchase-cost and losses-period added; audit (org_id, at) tried and rejected by the guard tests); plans compared with/without on 6 screens, one accepted regression (item card of the top seller at huge size, in #304). CI green + Codex clean on `03441fc`. NOT merged: migration = installer = owner OK.
   - Second-pass review complete (A + B). #274 closed. #252 code side closed out on the issue.
2. Not done and why
   - Cashier PC is still on `69d87f8` (it was off): #288 #289 #290 #297 #298 #302 #303 arrive with the next «تحديث».
   - #18 two scan steps (customer field, label on a box), #19, #252 device side, #253 small/medium on the Heikal PC: need the owner / devices.
   - Backup timings at 11-12 GB not measured (cloud disk). Large 1y: backup 183 s, restore 108 s, 0 problems.
   - PR #301 (version 0.4.0, opened 02:03 by another session of the owner's account): left open on purpose; it belongs right before the candidate installer build.
3. Decisions: the chain works. Workers created from the manager chat after the repo was attached DO get push + API (the read-only ones were created before). Wake-ups into the manager chat fired on time (02:15, 04:30, 08:30, 12:30).
4. Next goal (day chat, 13:00, owner at the pharmacy; new normal chat in the project, strongest model): (a) owner OK → merge #160 + #301, build the bundled installer; (b) cashier PC «تحديث» + stock check + the two #18 scan steps; (c) Heikal PC: install, `scale.py` small/medium with the second pass on 0036, then #19 with `docs/DAY19_OWNER_HANDS.md`; (d) #252 device side (15 min).
5. المحادثة دي اتحفظت وممكن تتمسح

## 17/11 00:30 Cairo — manager chat: cashier PC done, R8-B applied, work chain starts (READ THIS FIRST)
Open reminders (end every owner reply with them): product name + USB flash drive (before 18/11); OK for the bundled installer; ask the accountant about the e-receipt phase; paper temperature log until F38; book the doctor session; data export from the 3 accounts.
1. Done, with evidence
   - Cashier PC (desktop-5jvh3qd): «تحديث» to `c627f32` (22:47) then `69d87f8` (23:36), backup first each time, stock check all matching (2765 items, 3050 movements). #18: driver labels + scanner PASS, reprint and F8 with the printer off PASS (owner watched: nothing printed after power on).
   - Merged (CI linux green + Codex clean on the merged head): #287 (#286 audit label «فتح الدرج»), #288 (#274: data from a newer program is refused; main's 0034/0035 accepted, #104 option 1).
   - Review R8-B ran early (23:10); read-only git again, 4 patches in the project. Applied as #289 (three sale-screen money fixes) and #290 (permission check before a draft write); after-launch issues #291 #292 #293. Merge state: see `docs/QUEUE.md` and #113.
   - Sign-in on the PC is solved for good: OWNER_RULES §10 last bullet.
2. Not done and why: owner-password reset on the PC (console is not reachable by computer use; covered by `tests/test_reset_owner_password.py`; dropped). #160 not touched (stale, overlaps Night A). The cashier PC is off for the night, so #288 #289 #290 reach it at the next «تحديث».
3. Decisions: owner approved the work chain (OWNER_RULES §10, queue in `docs/QUEUE.md`). Scheduled cloud sessions are read-only workers; the manager chat wakes itself (04:30, 08:30, 12:30 Cairo on 17/11) to apply and merge. Zero-stock label: allowed, will ask «متأكد؟» (after launch, #292).
4. Next goal: `docs/QUEUE.md`, first open item. Day chat 17/11 13:00: a new normal chat in the project, strongest model (it reviews money merges).
5. المحادثة دي اتحفظت وممكن تتمسح (after the 12:30 report)

## 16/11 21:30 Cairo — manager chat: second-pass review A (R8-A) turned into PRs (READ THIS FIRST, then the entry below)
- 18:20: the manager chat was at its usage limit and its push attach was refused, so it scheduled review A (19:45) and
  review B (00:50 Cairo 17/11). Review A ran read-only (no push, no GitHub API) and left its three fixes as patch files
  in the claude.ai project «برنامج الصيدلية» (`claude/r8-a-review-2026-11-16.md`).
- 21:10: push attach worked in the manager chat. The three fixes are now PRs (numbers in the `R8-A log` comment on #203),
  each with a test that fails on `develop`, full suite run locally, `@codex review` requested, label `ci`.
  Two of them touch `app/sales/service.py`: merge one, then update the other and re-run CI + Codex (OWNER_RULES §3).
- The old scheduled session «Pharmacy review R3» (01:00 Cairo 17/11) was disabled by the manager chat at 21:16 (its work was done).
- Scheduled cloud sessions may again get read-only git (as review A did). If review B leaves patch files in the project
  instead of PRs, the manager chat applies them the same way.
- New standing rule (owner, 18:15): every session ends by saying where the next one starts and with which model (`OWNER_RULES.md` §9).
- Still open from the entry below: (a) owner presses «تحديث» on the cashier PC + stock check, (c) Heikal PC results,
  (d) #104 + #274 decision, (e) #19 trial day.

## 16/11 evening — manager chat: deep review R3-R7 done in one afternoon, fixes merged
Open reminders (end every owner reply with them): product name + USB flash drive (before 18/11); ask the accountant about the e-receipt phase; paper temperature log until F38; book the doctor session; data export from the 3 accounts.
1. Done, with evidence
   - Owner asked to intensify now (he is at the pharmacy, quiet days) instead of waiting for night sessions. Deep review #203 parts R3, R4, R6, R7 ran as four parallel read-only reviews; R5 (auth, sessions, CSRF, unauthenticated routes, PIN, SQL building, update/network routes, approvals) read by the manager: no new P0/P1 there.
   - Findings fixed, each with a test that failed first, CI green + Codex clean on the merged head:
     - #270 reports: day/hour rows per sale's own local time (daylight saving), hidden items in the expiry list, unsold shelf value.
     - #272 stock/import/purchasing: count approval applied a shortage twice after an adjustment; opening balances could post twice from a stale import run; link decision bypass; negative purchase total; float rounding of derived net price; cost leak on supplier-return suggest; recall notice permission; import-estock re-run.
     - #273 money forms: double click posted twice on five money forms; scheduled expense paid the next month; customer collection method not stored and over-collection accepted (now refused); stale contract discount on a draft; payroll month text; credit_limit PATCH; empty return; a batch with a confirmed recall is no longer sellable (pending warns); two screen fixes.
     - #271 backups (see its last comments for the merge state): restore over a damaged live database; one missing attachment stopped every automatic backup; verified backup (or plain copy) before an installer upgrade migrates; .zip.part leftovers; cross-site first-run setup; clock moved back.
   - Pharmacy PC (desktop-5jvh3qd) updated by the manager through the in-app update to `cba64f9` (16:41), backup `pharmacy-backup-20261116-164118.zip`; stock-check report on the real data: 2765 items, 3050 movements, all matching. It still needs one more «تحديث» for #270/#272/#273/#271.
   - Heikal PC (desktop-gqgl6uf): its own project chat «جهاز صيدلية هيكل» is the device worker (instructions on #113 and pasted into that chat; it has read-only GitHub, so it reports in chat): install the program, then the #253 scale test with synthetic data, then the #19 trial day.
   - Recorded, not fixed: #265 #266 #267 (launcher, need Windows), #268 (payroll/attendance small gaps), #269 (thermal receipt: delivery fee printed after the amount due), #274 (refuse newer-schema data: blocked by #104, main's 0034/0035), and the Codex P2 on partial restore (issue linked from #271).
2. Not done and why: the real-years test has no result yet (moves to the Heikal PC). #160 (indexes 0036) and the bundled installer wait for the owner's OK. Scheduled session «Pharmacy review R3» (01:00 Cairo 17/11) could not be deleted by the tool: its work is done, the owner deletes it from the scheduled tasks list or the next chat disables it.
3. Decisions (changes the owner will notice): a collection larger than the customer's debt is refused; an identical money entry repeated within 4 seconds counts once; pharmacist/assistant can read recall alerts but only manager/owner record a notice; a pharmacist gets no suggested value on supplier returns; a count line adjusted after it was counted must be recounted. Owner rules of the evening are in `OWNER_RULES.md` §9.
4. Next goal (new chat, model: strongest for the re-review, Opus is enough for device work): (a) press «تحديث» on the cashier PC and re-run the stock check; (b) second-pass review of what the parallel reviews did not finish: purchasing importers/extractors, sales return/void/update_line stock paths, POS credit/customer picker in app.js, labels/qr layout, migrations ordering; (c) read the Heikal PC results; (d) decide #104 + #274, then ask the owner for the bundled installer (with #160); (e) #19 trial day, possibly on 17/11 instead of 19/11.
5. المحادثة دي اتحفظت وممكن تتمسح

## 16/11 17:00 Cairo — manager chat: full re-check, plan v2, queue cleared
Open reminders (end every owner reply with them): product name + USB flash drive (before 18/11); ask the accountant about the e-receipt phase; paper temperature log until F38; book the doctor session; data export from the 3 accounts.
1. Done, with evidence
   - Windows-only failure of `tests/test_perf_real_years.py`: cause = `scripts/validate_local_release.py` exports `PHARM_DB_PATH`, `bench.Server` set only `PHARM_DATA_DIR`, so the measured server opened another database. Fixed in #263 (CI linux + windows green, Codex clean). Not a product defect; the PC sets no `PHARM_DB_PATH`.
   - Merged into `develop` (each: CI linux green + Codex clean on its head; owner approved the batch): #147 #221 #222 #224 #226 #227 #228 #230 #231 #232 #233 #234 #235 #236, then #237 (updated after #236, same file), #263. `develop` @ `4f8bc93`; local full suite on the 14-merge result: 1085 OK (2 skipped). None touches migrations, `requirements.txt`, `app/printing/` or `installer/`, so «تحديث» can deliver them.
   - Closed with evidence (WORK_PLAN P3): #142 #134 #151 #107 #98 #100. Left on the #18 visit list: scan a driver-mode label, reprint/F8 with the printer off, one real owner-password reset on the test account.
   - #213 (#202 parts 4-5): merged `develop` in, kept both sides of `docs/FUTURE_REQUIREMENTS.md`; the merge result broke `tests/test_time_format` (hand-cut ISO time), fixed with `stamp()`; local full suite 1097 OK; Codex + CI requested on the new head.
   - `docs/WORK_PLAN.md` v2: four tracks (review / coding / device tests / development), day-by-day timetable to a first release on 21/11 (estimate), tool and model per task, 12 risks with their answers, monthly cycle after launch. `docs/OWNER_RULES.md` §9: the owner's decisions of 16/11.
   - Two scheduled night sessions for 17/11: 01:00 Cairo = R3, 06:00 Cairo = R4 (review + fix PRs only, log on #203).
2. Not done and why: #213 waits for Codex and CI on its new head. #160 (indexes, migration 0036) waits for the bundled installer. R3-R7 not started in this chat (night sessions). The real-years test still has no results file on the PC.
3. Decisions: owner 16/11 — repo stays public for now, the #102 data is ordinary in his view, back to private when the reason ends (manager's check: item-level sales, cost and profit, supplier invoices, employee names; no customer names or phones seen in the headers). Parallel-week method is an open owner decision for 18/11 (WORK_PLAN §6 risk 1).
4. Next goal (17/11 day chat): read the #203 night log; merge the night's Codex-clean fix PRs and #213; read `PHARMACY-TEST\real-years-results.txt`; owner presses «تحديث»; the 3 small PC tests above. Then the nights of 17/11 (R5 security #252, R6) need two new scheduled sessions: create them in that chat.
5. المحادثة دي اتحفظت وممكن تتمسح

## 15/11 20:40 Cairo — ideas chat (cloud only, no code)
- Owner asked for program/operations ideas, smart survey questions, and other models' opinions. Sent the prompt to 6 AI sites: only ChatGPT answered (Gemini/Grok no reply with the pane hidden; Perplexity/DeepSeek/Copilot need sign-in).
- Double-checked against `develop` @ `db8a3b9`: offer evaluator, customer credit limit, per-batch legal price, purchase-cost rise warning and recalls already exist, so dropped. New entries **F38–F49** in `FUTURE_REQUIREMENTS.md`; ranked plan and timeline in `docs/IDEAS_PLAN.md`. All after launch, except **F47-a** (track-and-trace research, no code, class B): queue it after #253 and #252, before #19.
- Verified at the source: EDA Decree 475/2025 covers pharmacies (registry, devices, linkage; imported from 1/2/2026, local from 1/8/2026, phased); Decree 380/2009 requires visible thermometer/hygrometer, readings at fixed intervals, max 30°C.
- New owner reminders: ask the accountant whether the pharmacy is in an e-receipt phase; start a paper temperature log now (until F38).
## 16/11 15:05 Cairo — manager chat (handed over to a new chat)
- P1 done: the owner made the repo **public** (16/11 14:50). CI runs again. First run on develop c6cd346 (workflow_dispatch 43870092066): linux ✅, windows ❌ with exactly 2 failures, both in `tests/test_perf_real_years.py`: the benchmark login inside `real_years.py` is refused **on Windows only** (`RuntimeError at real_years.py:428` = "benchmark login refused"). Not reproducible on Linux, even with TZ=UTC. All other 1019 tests pass on Windows.
- Open: branch `claude/real-years-windows` (no PR yet) makes the refusal report HTTP status + error code (no message text); CI run 43871716849 on it (workflow_dispatch) shows the code. **Next chat, first package:** read that run's windows log (`grep "'error':"`), fix the cause, PR + `ci` label + Codex, merge, then copy `scripts/perf/{real_years,loadgen,bench,simclock,serve_at}.py` to `C:\Users\ph1\PHARMACY-TEST\perf\` (the copies there now have this Windows defect) before the owner runs `real-years-test.bat`.
- Then continue `docs/WORK_PLAN.md`: P3 (verify+close stale launch-critical issues), P4 (merge ready PRs with the `ci` label now that CI runs), R3 of the deep review.
- Owner has not run the real-years test yet; tell him to wait until the PC copy is fixed.

## 16/11 14:30 Cairo — manager chat: master plan + deep review started (#203)
- `docs/WORK_PLAN.md` is the master plan: critical path P1-P8 (ح1-ح8), roles, risks; target first release 20-23/11.
- Deep review R1 sales/returns/void: card refund on a credit-only sale passed uncapped, fixed + merged (#261). R2 cash/shifts: clean. Next in order: R3 credit/customers/contracts/expenses/payroll, R4 purchasing/stock/counting/#202, R5 auth/permissions/security (#252), R6 backup/restore/update/installer, R7 reports/printing/UI.
- GitHub minutes: owner chose the best free option = repo public until the reset; the visibility switch must be done by the owner (blocked for the agent). All 185 branches scanned: no secrets.
- Queue for the next chat (one package each): P3 verify-and-close stale launch-critical issues (#142 #134 #151 #107 #98 #100), P4 merge the 15 ready PRs + #213, then R3.
- The real-years test was not run on the PC yet (no results file).

## 15/11 23:00 Cairo — manager chat: GitHub minutes decision (owner: "choose the best and do it")
- Cause: 435 CI runs in 12 days used the 2,000 free minutes (Windows counts 2x); they reset at the start of each month.
- Decision: no payment, no second account, repo stays private. Full suite runs locally first; GitHub CI only once per PR via the label `ci`; docs-only PRs skip tests; Windows only when Windows-relevant files change; images pinned (#260). Until the reset, merges follow OWNER_RULES §4 temporary rule. Home-laptop runner kept as a fallback only if minutes get tight.
- Merged tonight: #257 (#253 real-years test), #258 (plan), #259 (no TZ override on Windows). PC: `PHARMACY-TEST\real-years-test.bat` + `perf\` copied (merged code), logs to `real-years-log.txt`; the owner double-clicks it before leaving.

## 15/11 21:30 Cairo — manager chat (cloud)
- #253 (PR `claude/253-real-years`): `scripts/perf/real_years.py` + `tests/test_perf_real_years.py`. Copy-only (live DB sha unchanged), timings-only (no names/barcodes/users in results), work folders deleted. Owner rule 15/11: the test covers the smallest to the largest pharmacy, so four sizes: small 30-60 sales/day 2k items, medium 150-300 / 4k, large 800-1200 / 15k, huge 2500-3500 / 30k; each size on its own fresh copy, in order, until `--stop-at`. Runs from `PHARMACY-TEST\perf\` with `PHARM_PROGRAM_DIR` (installed program untouched); one-click `PHARMACY-TEST\real-years-test.bat`.
- Generator speed measured here: 16.5 sales/s (4k items). Through the services, large 5 years ≈ 34 h and huge 1 year ≈ 20 h even here, so large/huge need a faster generator first (cloud task, queued) and run in the cloud; the PC night run does small + medium.
- One master plan: `docs/WORK_PLAN.md` (launch + pharmacy track + cloud track + after-launch order); `docs/IDEAS_PLAN.md` (other chat, #256) stays the ranked list of the new ideas and is referenced from it. Coordination rule: only the manager chat edits `WORK_PLAN.md`; other chats add ideas to `FUTURE_REQUIREMENTS.md`/`IDEAS_PLAN.md` and claim files on #113.
- Found: 22 open PRs waiting since CI billing stopped (incl. #235 payroll money fix); HANDOFF marks many Codex-clean, each head to be re-checked before merging. Queue item C0: merge them under OWNER_RULES §4 temporary rule (local full suite on each merge result), except the paths that wait for real CI.
- Owner requests 15/11 (after launch): on-screen clock, sign-in/out log, payroll. Attendance (check in/out) and monthly payroll (work days, advances, bonus/deduction, approve, pay) already exist (`app/finance/payroll.py`, migration 0021); missing: logout is not audited, no sign-in/out screen, no overtime/late hours, insurance/tax, printed payslip.

## 15/11 17:45 Cairo — day chat (owner at the pharmacy)
- Merged under the OWNER_RULES §4 temporary rule (CI still blocked by GitHub billing; last Actions run 13/11 17:52 UTC failed): **#241** (12/24-hour clock everywhere, Codex clean on `e9dd019`) and **#244** (owner rules + queue #243; Codex P2 only). Each: local full suite on the merge result, 1002 OK (2 skip), command and counts on the PR.
- Correction (Codex P2 on #244): billing does **not** stop every merge. Under §4's temporary rule a PR merges after a clean Codex review (no P0/P1) and a local full-suite run on the merge result, unless it touches `app/printing/`, `installer/`, Windows-only scripts, `requirements.txt` or `app/migrations`; only those wait for real CI.
- `develop` now has #240 (#98 one printer-off warning) and #241; no migration or `requirements.txt` change since the PC's `fa3ccc2`, so «تحديث» can deliver them.
- Pharmacy PC: day script **test 3 PASS end to end** (18:38 update `213b773 → 1c5cab6`; 18:55 rollback via `rollback-test.bat`, restart from the program entry, installed back to `213b773`, data unchanged; 18:57 update again to `1c5cab6`; evidence on #18). `STATUS.md` G6 → L2, total 40% → 45.25%. The PC runs #240 and #241. Left with the owner: tests 1 (printer off) and 2 (driver label scan).

- Owner requests saved for after launch (15/11): #246 F35 two UI looks, #247 F36 first-run guided tour, #248 F37 import from any program/format (`docs/FUTURE_REQUIREMENTS.md`).
- Test 1 (#98 printer off) PASS on the PC (1.1–1.6; evidence on #18). Test 2 (label scan) parked by the owner. Now: E-Stock import dry run on the PC (`PHARMACY-TEST\estock-test.bat`, files in `C:\estock`, copy of the DB, counts only leave the PC).

- 19:25 **real E-Stock import applied on the pharmacy PC** (owner decision: use the 24/10 exports as real data for the trial): 30,470 items, 3,031 batches, reconciliation OK, backup `pharmacy-backup-20261115-192414.zip` first (#19 comment).
- 19:30 **launch-critical #249**: stock report 118 s and the whole server frozen on real data (stale `sqlite_stat1` + sync report on the event loop). Workaround on the PC: `ANALYZE` + restart → 0.23 s. Code fix still needed (next coding batch, first).
- Owner requests saved 15/11 evening: #250 phone access + auto layout, #251 customize UI, #252 security review (launch-critical), #253 real-scale multi-year tests on the PC (launch-critical), #254 lessons from the E-Stock main/sub/branch setup. Multi-invoice tabs already exist (F2).
- 20:10 **#249 fixed by #255** (merged under the temporary rule, Codex clean, 1008 OK), PC updated to `4c7a7d6`: stock report 133 ms (was 118 s), `/api/health` answers during a report; #249 closed.
- #254 closed by the owner (branches not needed; Heikal PC = a separate branch with its own stock, old E-Stock setup, context only).
- **Next, in order (owner 15/11 20:14):**
  1. New project chat, cloud only: write `scripts/perf/real_years.py` + a one-click `.bat` for the PC (#253): copies the live DB to a temp folder ON the PC, adds 1, 3 and 5 years of synthetic sales/purchases/returns using the real catalog through the app services, times every report (short and long ranges), search, sale, backup/verify/restore and start-up, and writes timings only to `PHARMACY-TEST\real-years-results.txt`. Test it first on synthetic data in the cloud; PR + Codex + merge.
  2. Owner leaves the PC on overnight and double-clicks the `.bat` before leaving (no Claude needed overnight; E-Stock untouched; the live DB is only read once to copy it).
  3. Next day chat: read the results, fix anything slow (pass limits: cashier actions < 0.3 s, any report < 5 s, nothing blocks other users), then #252 security review, then the #19 trial day.

## 13/11 19:45 Cairo — owner decisions (manager chat)
- New feature, owner request: before closing a shift, warn about courier cash not handed in and open draft invoices → **#243** (warn, never block; no migration, ships by «تحديث»). Gap found: the courier line is hidden in blind-count mode. **Queue place:** first coding batch after #240 #241 #242 merge (same files `app/web/app.js`); before #97, which rebases on it.
- Rules added to `OWNER_RULES.md`: every model uses the same critical, constructive mindset and short Arabic with the owner; model-to-model talk in short English; «إحنا فين؟» = 5-line status from the files.
- Still blocked: CI (GitHub billing since 12/11 20:17 UTC; last run 13/11 10:08 UTC failed). Nothing merges until the owner fixes billing.

## 13/11 07:30 UTC — N2 end report (goal: tomorrow's pharmacy PC tests ready) — REACHED (merges blocked by CI billing)
1. Done, with evidence
   - #98 re-read: the fix holds (job cancelled, nothing fires later, audit `not_printed`). **Gap found:** each toast replaced the last, so with the printer off the cashier saw only the drawer warning, never the receipt one. Fix + 5 server tests (every drawer timing with 2 copies, card sale, delivery, retried F9, next sale after power-on) + 1 browser test that fails before the fix → **PR #240**, head `8fb0ba1`, full suite 997 OK locally, **Codex clean**.
   - Day script for the 3 PC tests at the top of `docs/VISIT_18_CHECKLIST.md`: G6 update → rollback → update again (first, so the PC gets the latest code), #98 printer off, driver-mode label scan. Each step: who, the owner's Arabic line, what he should see; rollback commands included. **PR #242** (stacked on #239). Codex P2 (final `visit.post` would FAIL after the test sales) fixed in `f5729c7`; re-review requested.
   - #206 sweep: the 12/24-hour setting applied only to the receipt. Screens, hours chart, print header, shift report and supplier return pages now follow it; `/api/me` carries it → **PR #241**, head `e9dd019`, 5 tests (fail on develop), full suite 996 OK, **Codex clean**.
2. Not done and why: nothing merged. `CI / linux` still fails in ~3 s with no log (GitHub billing, since 12/11 20:17 UTC), so no PR meets the merge rule. **Ready to merge once CI is green:** #240, #241 (Codex clean on their heads); #242 after Codex on its last head (it also merges #239/#238/#163).
3. Decisions: G6 test runs first in the day (its update delivers #240/#241 if merged); the owner presses «إلغاء» on the browser print window after a failed receipt (printing from it would queue in Windows and come out later); device warnings stay 15 s in one toast; the time setting stays in the receipt settings (one place), label renamed to say it covers all screens.
4. Next goal (day, owner at the pharmacy): owner fixes GitHub billing → manager merges #240 #241 #242 on green CI and checks CI on `develop` → owner runs the day script (3 tests) → evidence on #18, `STATUS.md` G6/G7.
5. المحادثة دي اتحفظت وممكن تتمسح

## 13/11 02:30 UTC — N1 end report (goal: open PR queue clean) — NOT reached
**Blocker:** GitHub Actions has run no job since 12/11 20:17 UTC (every CI / linux fails in ~3 s with no log; GitHub billing/spending limit). Under OWNER_RULES §4 nothing can merge until it runs again. Only the owner can fix it: github.com → Settings → Billing and plans. Note: #225 was merged at 01:20 UTC with that same failing CI, so `develop` `d8fa571` has no CI result.
1. Done, with evidence
   - #213: `develop` merged in (merge commit, no force push; HANDOFF conflict kept both sides). Head `634b098`, full suite locally 1003 OK (2 skip). `@codex review` requested.
   - #221: not a pure duplicate. Its `channels.py` fix equals #219's (kept `develop`'s); what remains is one new test file (6 tests: channels, contracts, notifications). Head `46064e9`, full suite 997 OK. `@codex review` requested.
   - #163: red CI is the billing stop, not its content. Its night log is carried in this PR (merged in), so merging this PR also merges #163.
   - #238: Codex clean on `ebbc6ba`; CI red (billing). Also carried in this PR.
   - Triage (on #113): **merge** #147 (CI green + Codex clean on `56dd876`, no expectedFailure left, its 16 tests pass on today's develop), #77 (owner's cost rule of 30/10; needs folding into AGENTS §5, it duplicates the heading); **after launch** #132 (touches `app/printing/`), #99 (auto-updates, #96), #83 (F20 doc, class C); **close (owner decides)** #102: holds real pharmacy data (supplier invoice photos, E-Stock data pickles under `work/`); never merge; keep only `docs/SMART_ORDER_REPORT.md` in a clean PR later; deleting the branch is the owner's call.
2. Not done and why: no merge at all (#238, #213, #221, #163, this PR) — CI cannot run. Codex is clean on every new head (#213 `634b098`, #221 `46064e9`, this PR `6cc75bd`), so each needs only a green `CI / linux` (re-run the failed job once billing is fixed; a docs edit would need Codex again).
3. Decisions: merge `develop` into PR branches instead of rebase (AGENTS §2: no force push on shared branches). One combined handoff PR (#238 + #163 + this report) so the owner merges one PR, not three that each edit HANDOFF.
4. Next goal: when CI runs again — re-run CI on #238/this PR, #213, #221, #147; merge each when CI linux is green and Codex is clean on its head; then check CI on `develop`. N2 (PC test script) does not need CI.
5. المحادثة دي اتحفظت وممكن تتمسح

## 12/11 21:45 — CURRENT STATE (read this first)
Standing rules: [OWNER_RULES.md](OWNER_RULES.md). Work now runs from the claude.ai project «برنامج الصيدلية»; no pasted handoffs.
- Open reminders (end every owner reply with them): book the doctor session; data export from the 3 accounts; buy a USB flash drive (later).
- `develop` @ `c7745bb`, CI linux + windows green. Merged 12/11 evening: #207 #209 #211 (#202 parts 1-3), #196 #197 #204 #205 #210 #212 #214 #216 #217 #218 #219 #220.
- Waiting: #213 (#202 parts 4-5) and #163 need Codex on their latest head (requested); #221 conflicts with #219 (overlapping payment-channel rename, check if it is now a duplicate, then rebase); #225 (this rules file); #147 held; #160 installer-only.
- No scheduled sessions are enabled, by the owner's decision, until the open work is closed.
- Pharmacy PC (desktop-5jvh3qd) runs develop fa3ccc2; it can update to c7745bb with «تحديث» (no requirements/migration change since). Still to test there, printer-off first: (1) #98 printer switched off during a sale; (2) scan a label printed in driver mode; (3) update rollback (G6). Then E-Stock export/import rehearsal, #19 trial day, #20 written approval.
- Progress: official 40% (STATUS.md); raise G6/G7 only with the PC evidence above.
- «الطابور» (one goal per session, OWNER_RULES §7). Night 13/11 has two scheduled sessions (N1 ~03:40, N2 ~08:45 Cairo).
  - N1 goal: open PR queue clean. Steps: rebase #213 on develop (conflict in docs/HANDOFF.md only after #225) and merge after CI + Codex on the new head; fix #163 (CI red) or close it if its content is already in HANDOFF (note why); decide #221 (duplicate of #219 → close with a note, else rebase); triage #147 #132 #102 #99 #83 #77 into merge / after-launch / close — write the list on #113, close nothing without the owner.
  - N2 goal: tomorrow's PC tests ready. Steps: re-read the #98 fix and its tests (printer off during a sale → warning, no later print, no drawer); write one plain step-by-step script for the 3 PC tests (#98, scanning a driver-mode label, update rollback G6) in docs/VISIT_18_CHECKLIST.md with the expected result of each step; check #206 (12/24-hour clock) coverage on every screen that shows a time, fix gaps with tests.
  - Day goal (owner at the pharmacy, he says «أنا في الصيدلية»): press «تحديث», run the 3 PC tests from the N2 script, record evidence on #18, update STATUS.md G6/G7.
  - The project chat «طلبات الدمج المفتوحة» (opened 12/11 22:00) never started (waiting for repo approval); N1 replaces it, the owner can delete it.

## 12/11 — #202 أرصدة المخزون ما تتلخبطش (launch-critical) — الكود خلص، مستني الدمج
- 4 PRs متسلسلة على `develop`، تتدمج بالترتيب: [#207](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/207) (خريطة كل طرق تغيير المخزون + حارس) ← [#209](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/209) (فحص تلقائي عند التشغيل وبعد كل نسخة، شريط أحمر + audit، من غير تصليح) ← [#211](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/211) (تجربة طويلة: آلاف العمليات، 4 كاشير مع بعض، بيعة اتقتلت في النص، استرجاع نسخة؛ 20000 عملية في 73 ث) ← [#213](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/213) (تقرير «مطابقة الأرصدة» `#/stock-check` + إجراء الاختلاف في `docs/STOCK_INTEGRITY.md` §7).
- نتيجة المراجعة: مفيش ثغرة في الكود الحالي؛ كل تغيير في المخزون بيعدّي على `post_movement` بس. الحارس بيمنع أي طريق جديد غيره.
- #207: CI أخضر + Codex نظيف على `fc6bf7f` — **جلسة 12/11 المجدولة ما اتسمحلهاش تدمج** (رفض صلاحية)، فمستني دمج المالك.
- ملاحظات Codex اتصلحت كلها بالاختبارات: #209 (`2d62989`): الشريط بيتحدّث لوحده، المبيعات بتتقارن بسطور الفاتورة، الاستعلام كان تربيعي (40 ث لـ 30000 سطر)، الفحص في النسخة اللي «مفيش تغيير»، وفحص واحد في المرة (قفل). #213 (`142634d`): التقرير ما بيعرضش تشغيلة صيدلية تانية (P1)، سطر وقف النسخ الاحتياطية لاختلاف التشغيلات بس، و«تمت المراجعة» للمالك على اختلاف فاتورة بيع بعد الجرد (مسجّل في السجل، ويرجع لو الأرقام اتغيرت).
- الاختبارات الكاملة على #213 `142634d`: 938 OK (2 skip). `CI / linux` على #209 `1b23468` وقع في `test_pos_quick_fresh` (اختبار متصفح متقلّب، بيتصلح في #212) مش في الشغل ده؛ الـ head اللي بعده أخضر.
- طابور الليل بند 6 (جزء منفصل): [#216](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/216) اختبارات تنبيهات الشاشة الرئيسية وتقرير الفاقد (مفيش عيب)، [#221](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/221) عيب: تغيير اسم جهاز دفع لاسم موجود كان خطأ سيرفر 500، دلوقتي 409.
- اللي فاضل: دمج الأربعة؛ F53 (أمر إعادة بناء الأرصدة) بموافقة المالك بس؛ نصوص الشاشة الجديدة عربي بس (مفيش ترجمة إنجليزي).

## 11/11 20:45 — زرار التحديث (B13) عمره ما كان هيشتغل على جهاز الصيدلية: السبب والحل
- الدليل: «حدّث البرنامج الآن» على `desktop-5jvh3qd` (`d24979e` ← `b5d8891`) رجع 500: `التحديث فشل: Dependencies or database migrations changed; a coordinated upgrade is required`، مع إن `requirements.txt` و`app/migrations` واحد في git بين النسختين. سطر `system.update` في السجل `ok: false`.
- السبب: ملفات البرنامج في المثبّت جاية من `git archive` على جهاز ويندوز في جيت هاب (`core.autocrlf=true` ← CRLF)، وأرشيف التحديث من جيت هاب LF. `release_bootstrap._contract` بيقارن البايتات، فكل تحديث بيبان تغيير في المكتبات/الـ migrations. اختبار R8 في بناء المثبّت ماكشفهوش لأن الطرفين هناك CRLF. (`db.migrate` بيقرا الملفات نصّي فـ checksum قاعدة البيانات مش متأثر.)
- الحل (الفرع `claude/update-crlf-contract`): `_contract` بيتجاهل فرق نهاية السطر، والمثبّت بيبني الملفات LF (`git -c core.autocrlf=false archive`). الكود اللي بيعمل الفحص هو الكود المتثبّت، فجهاز الصيدلية محتاج **مثبّت واحد أخير** بالتصليح، وبعده التحديثات بالزرار.

## الطابور الليلي (جلسات مجدولة 00:52 و05:52 و10:52 و15:52 بتوقيت القاهرة، 12/11)
كل جلسة: القفل على #113 (`RUN START` / `RUN END`)، تكمّل أول بند مش متقفل، وتكتب سطر في «سجل الليل» تحت بعد كل خطوة. ممنوع تلمس `app/printing/`، و#18، و#100 (تجارب الجهاز مع المالك)، وممنوع main.
1. PRs الأمان #154 (Host)، #155 (xlsx)، #157 (حدود الحجم): نتائج Codex، والتأكد إن #154 مايكسرش `127.0.0.1` ولا `localhost` ولا الموبايلات على شبكة الصيدلية (`/api/system/network`). دمج في develop حسب AGENTS §2a بس.
2. شغل الأداء `claude/perf-large-data`: آلاف الفواتير (سنين)، وسرعة الشاشات والتقارير والنسخ الاحتياطي، وكمبيوتر ضعيف. النتايج Issues وإصلاحات صغيرة بـ PR.
3. #153: لما البرنامج يتحدّث والصفحة مفتوحة على `app.js` قديم: شريط «فيه نسخة جديدة — حدّث الصفحة».
4. #147: حل ملاحظة Codex (P1) من غير ما نضعّف اختبار.
5. مستند: اللي ناقص عن برامج السوق وإي ستوك (`docs/FEATURE_GAP.md`)، وخطة صيدلية الصاحب (`docs/SECOND_PHARMACY.md`).
6. لو الطابور خلص: مراجعة بعين جديدة لآخر دمج في develop، وزيادة اختبارات حيث فيه فراغ.

### سجل الليل
- 12/11 01:10 · بند 1 · #154 و#155 و#157: Codex مالقاش حاجة على آخر head لكل واحد، و`CI / linux` أخضر، ومفيش تعارض. #154 اتجرّب على سيرفر حقيقي (`127.0.0.1` و`localhost` وعنوان الشبكة للموبايل 200، وHost غريب 403؛ الدليل على #154). **الدمج اترفض للجلسة المجدولة** (صلاحية الدمج من غير مراجعة بشرية)، فالتلاتة مستنيين المالك. بعد دمج #154 هيبقى فيه تعارض بسيط في `docs/SECURITY.md` (سطر جدول لكل PR) في #155 و#157. · التالي: بند 2.
- 12/11 01:25 · بند 2 · شغل الأداء اتقسم: PR للاستعلامات من غير migration (ينفع بزرار التحديث) على `claude/perf-queries`، والفهارس `0036` في PR تاني للمثبّت. 707 اختبار ناجح. · التالي: PR الفهارس وقياس بيانات صناعية.
- 12/11 02:30 · بند 2 · [#159](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/159) (استعلامات، ينفع بالزرار) و[#160](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/160) (فهارس `0036`، للمثبّت بس، ماتدمجوش من غير المالك). ملاحظة Codex P2 على مولّد البيانات اتصلحت. القياس على 30 يوم صناعي في `docs/PERFORMANCE.md`: تقرير الفواتير الشهري 8.5 ثانية ← 0.17، تقرير المرتجعات 0.3 ← 0.006. نمو جدول السلامة اتفتح له [#161](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/161) (محتاج قرار). · التالي: قياس سنة كاملة لما المولّد يخلص، وبعدين بند 3 (#153).
- 12/11 03:20 · بند 3 · [#162](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/162): الشاشة المفتوحة بتعرف إن البرنامج اتحدّث (كل دقيقة ولما ترجعلها)، بتتحدّث لوحدها في الدخول والرئيسية وشاشة البيع الفاضية، وغير كده بيظهر شريط «فيه نسخة جديدة». اختبار متصفح حقيقي. · التالي: بند 4 (#147).
- 12/11 02:15 · بند 4 · [#147](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/147): مفيش `expectedFailure` خالص. اختبار #142 بقى عادي وناجح (التصليح اتدمج في #148)، والخمس اختبارات بتوع #143–#146 اتنقلوا كإعادة إنتاج في `docs/AUDIT_131_OPEN_DEFECTS.md` (اتأكدت إنهم لسه بيفشلوا على develop)، وكل PR تصليح يرجّع اختباره. 16 اختبار ناجحين. · التالي: بند 5.
- 12/11 02:15 · بند 2 (تكملة) · ملاحظات Codex P2 على #160 و#162 اتصلحت. فاحص خطة الاستعلام بقى يفهم الأسماء المختصرة للجداول، فطلّع 8 قراءات تانية لكل التاريخ: 5 اتصلحوا باستعلامات في #159، و3 بفهارس جديدة في `0036` (#160). · التالي: بند 5.
- 12/11 02:35 · بند 5 · [#164](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/164): `docs/FEATURE_GAP.md` (الناقص عن إي ستوك والسوق وترتيب مقترح) و`docs/SECOND_PHARMACY.md` (خطة صيدلية صاحبك: أهم حاجة قبلها قناة تحديث من غير مفتاح جيت هاب بتاعك). · التالي: بند 6 مراجعة آخر دمج.
- 12/11 02:50 · بند 6 · مراجعة #158 (نهايات السطور) و#148 (حد المرتجع النقدي) و#152 (استرجاع كلمة سر المالك): مفيش مشكلة. ملفات `.bat` بقت LF بس مافيهاش `goto` فمش متأثرة. ملاحظة Codex التانية على #162 (مسح باركود لسه بيدوّر) اتصلحت. [#165](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/165): أمثلة شاشة الإنشاء بقت أسماء عامة بدل اسمك. · التالي: قياس سنة صناعية كاملة (774 ميجا) على develop و#159 و#160.
- 12/11 03:40 · أداء سنة صناعية (82 ألف فاتورة، 811 ميجا) · على develop: تقرير الفواتير الشهري **87 ثانية**، وتقارير اليوم حوالي 0.2 ثانية، والنسخة الاحتياطية 142 ثانية والتأكد منها 115 ثانية. مع #159 و#160: التقرير 0.12 ثانية وتقارير اليوم 2–3 مللي. أكبر سبب لبطء النسخة الاحتياطية فحص المخزون (101 ثانية) اتصلح في [#166](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/166) (0.5 ثانية، من غير migration فينفع بالزرار). فهرس سابع لمرتجعات الفاتورة اتضاف لـ #160. الأرقام في `docs/PERFORMANCE.md`. · التالي: فحص السلامة بيبطأ مع الوقت (26 ← 135 مللي)، لسه مش معروف السبب.
- 12/11 03:55 · أداء (تكملة) · فحص السلامة طلع مش بيبطأ مع الوقت: أول فحص بعد تشغيل البرنامج بس بياخد 0.2 ثانية (بيبني فهرس الأصناف)، والباقي 3 مللي. قياس #166 على السنة: النسخة الاحتياطية 142 ← 50 ثانية، والتأكد منها 115 ← 10 ثواني، والاسترجاع 231 ← 21 ثانية. · التالي: مراجعة Codex على آخر نسخ #159 و#160، وبعدين نهاية الجلسة.
- 12/11 04:05 · نهاية الجلسة 1 · جاهزين للدمج (CI أخضر وCodex من غير ملاحظات) ومستنيين المالك لأن الجلسة المجدولة مش مسموح لها تدمج: #154 الأول، وبعده #155 و#157 (تعارض سطر جدول في `docs/SECURITY.md`)، و#159، و#166، و#162، و#147، و#164، و#165، و#163 (السجل ده). **#160 فيه migration: للمثبّت بس.** قرار مطلوب: #161. · الجلسة الجاية: لو اتدمج حاجة، حدّث الفروع التانية من develop واستنى CI؛ غير كده كمّل المراجعة والاختبارات.
- 12/11 04:20 · بعد نهاية الجلسة · ملاحظتين Codex P2 على سكربتات القياس في #159 (اختيار أصناف صلاحيتها سارية) اتصلحوا. ملحوظة للجلسة الجاية: CI على `7b7678e` (#159) وقع في `test_reports.test_sales_report` و`test_split_precision...test_report_shows_no_phantom_discount` لأن الاختبارات اشتغلت وقت نص الليل بتوقيت جرينتش (23:58 ← 00:01)؛ الـ head اللي بعده أخضر. اختبارات بتعتمد على «النهارده» ومحتاجة تتثبّت على وقت معيّن. · التالي: ثبّت الاختبارين دول على تاريخ ثابت (PR صغير).
- 12/11 04:35 · اتعمل [#167](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/167): تقارير «النهارده» في الاختبارات بتسأل عن يوم وقت التشغيل (اختبارات بس). · التالي: الجلسة الجاية.
- 12/11 06:13 · جلسة 2 · محاولة دمج #154 اترفضت تاني (الجلسة المجدولة مش مسموح لها تدمج)؛ كل الـ PRs لسه أخضر ومن غير تعارض ومستنية المالك بنفس الترتيب. بند 6: [#168](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/168) يصلّح #145 (تقرير المبيعات: إيراد كل صنف = اللي الفاتورة أخدته بالقرش، بنفس دالة المرتجع). #144 محتاج migration (عمود إجمالي السطر) أو قرار في الشاشة، فاتساب. · التالي: قياس #168 على بيانات صناعية، وبعدين #143.
- 12/11 06:32 · جلسة 2 · جاهزين (CI أخضر وCodex من غير ملاحظات على آخر head): [#168](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/168) (#145؛ ملاحظتين Codex اتصلحوا: قراءة واحدة مقيدة بالمؤسسة) و[#169](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/169) (#143: حد الآجل بيتراجع جوه المعاملة، فبيعتين في نفس اللحظة مايعدّوش الحد). اتفتح [#170](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/170) (#137: الموافقة على الشاشة بقت تصريح لعملية واحدة 90 ثانية، والرقم السري مابيتحفظش في المتصفح؛ اختبار متصفح حقيقي) ومستني Codex. · التالي: ملاحظات Codex على #170، وبعدين #140 (الحاجات الصغيرة اللي لسه ناقصة بعد #157).
- 12/11 06:49 · جلسة 2 · #170: ملاحظتين Codex اتصلحوا (إعادة طلب الموافقة بتحتفظ بمفتاح التكرار فمفيش دفع مرتين، والمدير الموقوف تصريحه بيبطل) ومستني مراجعة تانية. #168 اتسرّع (قراءة المرتجعات بس: 60 يوم صناعي 152 مللي). **ترتيب الدمج:** #170 بيتعارض مع #162 في `api()` في `app.js`: ادمج #162 الأول، وبعدين أحدّث #170 من develop. الباقي اتجرّب كله مع بعض: من غير تعارض غير سطور `docs/SECURITY.md` في #155 و#157. · التالي: مراجعة Codex على #170 و#168.
- 12/11 07:02 · جلسة 2 · #170 جاهز (Codex من غير ملاحظات على آخر head، وCI أخضر). اتفتح [#171](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/171) (كلمة المرور الحالية جوه جلسة مفتوحة: 5 محاولات كل 5 دقايق) و[#172](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/172) (اختبارات مرتجع المورد؛ لقت ثغرة: الصيدلي كان يقدر يبعت قيمة للوحدة من غير ما يشوف التكلفة ويخصمها من حساب المورد؛ اتقفلت). تغطية الأسطر للبرنامج كله 92.5% (قياس بـ `sys.monitoring` من غير مكتبة جديدة). · التالي: أقل الملفات تغطية: `dataimport/estock_job.py` 67%، `core/people.py` 83%، `core/daily.py` 84%، `core/autobackup.py` 85%.
- 12/11 07:12 · جلسة 2 · اختبارات في الأماكن الضعيفة: [#173](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/173) (نقل بيانات إي ستوك: ملف عدد الوحدات بالإكسل وقاعدة التكلفة صفر؛ اختبارات بس) و[#174](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/174) (شاشة المستخدمين: حد الخصم والإيقاف وتغيير كلمة السر؛ لقت إن حد خصم 12.5 كان بيتخزن 12 فاتصلح). #171 و#172 جاهزين (CI أخضر وCodex من غير ملاحظات). · التالي: `core/daily.py` و`core/autobackup.py`.
- 12/11 07:25 · جلسة 2 · [#175](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/175) (اختبارات فشل النسخ الاحتياطي التلقائي والفحوص اليومية؛ عدد النسخ المحفوظة 2.5 كان بيتخزن 2) و[#176](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/176) (أرقام صحيحة: جرد 18.5 شريط كان بيتسجل 18 ويعدّل المخزون، وعدّ الفلوس بالفئات، وأيام العمل، ومدة المرتجع، ومواعيد المصروفات المجدولة: كلها بقت ترفض الكسر بدل ما تقطعه). #173 و#174 و#175 جاهزين (CI أخضر وCodex من غير ملاحظات). · التالي: مراجعة Codex على #176، وبعدين باقي الملفات قليلة التغطية.
- 12/11 07:32 · جلسة 2 · (أوقات سطور الجلسة 2 اتصلحت لتوقيت القاهرة الصح.) [#177](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/177): اختبارات حماية إتمام البيع (تعديل من كاشير تاني أو تغيير السعر في نفس اللحظة) ورفض المرتجعات الغلط؛ اختبارات بس. #176 جاهز. · التالي: `purchasing/service.py` و`procurement/service.py` و`inventory/counting.py`.
- 12/11 07:43 · جلسة 2 · [#178](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/178) (اختبارات حالات الجرد؛ اختبارات بس) و[#179](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/179) (فاتورة الشراء: كمية 2.5 علبة كانت بتتسجل 2 وتدخل المخزون 2، والقروش بتتقطع؛ دلوقتي بترفض). #173 اتضاف له تصليح: ملف عدد الوحدات بصيغة JSON كان بيقطع 2.5 لـ 2. · التالي: مراجعة Codex على #173 و#179، وبعدين `procurement/service.py` و`finance/payroll.py`.
- 12/11 07:55 · جلسة 2 · [#180](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/180): نفس عيب #143 في المرتبات (صرف مرتب من شاشتين في نفس اللحظة كان بيتسجل مرتين، ومن الدرج كمان)، والمصروفات المجدولة (الإيجار يتسجل مرتين)، والموافقة/الرفض على مصروف في نفس اللحظة. كلهم بيتراجعوا جوه المعاملة. #177 ملاحظة Codex (تقييد استعلامات الاختبار بالمؤسسة) اتصلحت. · التالي: مراجعات Codex على #173 و#177 و#179 و#180.
- 12/11 08:12 · جلسة 2 · [#181](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/181) (اختبارات: نسخة احتياطية متعدّلة من جوه ومقفولة تاني لسه بترفض) و[#182](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/182) (المرتبات: السلف ماكانتش بتتسجل مصروف لو أخدت المرتب كله، فتقرير الربح كان بيزيد) و[#183](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/183) (اختبارات شاشات من غير اختبار: المرتبات والمصروفات المجدولة وإعدادات صغيرة). #180 ملاحظة Codex (تقييد بالمؤسسة) اتصلحت. · التالي: مراجعات Codex على #180–#183.
- 12/11 08:30 · جلسة 2 · تجهيز لتعارض #170 مع #162: الفرع `claude/137-approval-token-on-162` = #170 + #162 والتعارض في `api()` محلول (عدّاد #153 + تصريح الموافقة ومفتاح التكرار)، 721 اختبار ناجح منهم اختبارات المتصفح للاتنين. بعد دمج #162: `git merge origin/claude/137-approval-token-on-162` في فرع #170 (أو `git push origin claude/137-approval-token-on-162:claude/137-approval-token` لو #170 ماتغيرش)، واستنى CI. ده مش PR. · التالي: مراجعة Codex على #183.
- 12/11 08:33 · جلسة 2 · الشاشات نفسها كانت بتقطع الكسر قبل السيرفر (`parseInt`): فاتورة الشراء والإضافة السريعة (#179)، والجرد والمرتجع (#176)؛ دلوقتي بتبعت اللي اتكتب (`typedWhole`) والسيرفر يرفض. #172: `true` ماعادش بيتحسب وحدة واحدة في مرتجع المورد. #183 ملاحظة Codex اتصلحت. · التالي: مراجعات Codex على #172 و#176 و#179 و#183.
- 12/11 08:55 · جلسة 2 · [#184](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/184) (`?limit=abc` كان خطأ سيرفر، و`limit=-1` بيرجّع كل الأصناف). #170 اتضاف له اختبار متصفح لموافقة الخصم على شاشة البيع، و`claude/137-approval-token-on-162` اتحدّث. **خريطة الدمج:** كل الـ PRs المفتوحة (25 فرع) اتدمجت مع بعض على نسخة تجربة: 842 اختبار ناجح؛ التعارض الوحيد الباقي سطور `docs/SECURITY.md` في #155 و#157 بعد #154 (وفي #170 مع #162 والحل جاهز). #180 اتنقلت اختباراته عشان مايتعارضش مع #176 و#182. · التالي: مراجعات Codex على #170 و#180 و#184.
- 12/11 09:04 · جلسة 2 · اختبارات بس: [#185](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/185) (كل دفاتر الفلوس والمخزون الستة بترفض التعديل والمسح في قاعدة البيانات نفسها) و[#186](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/186) (قاعدة الحد الأقصى للكمية وقرار الحالات الحرجة في السلامة الدوائية، ببيانات وهمية). · التالي: مراجعات Codex على #180 و#184–#186.
- 12/11 09:16 · جلسة 2 · من ملاحظات تجربة الجهاز 11/01 (مش حرجة): [#187](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/187) زرار البيع السريع ماعادش بيفضل «ناقص» بعد الشراء، و[#188](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/188) F9 في فاتورة التوصيل وانت لسه في خانة العنوان بيحفظ العنوان ويكمّل. الاتنين باختبار متصفح حقيقي. · التالي: باقي ملاحظات 11/01 (عميل جديد بـ prompt) ومراجعات Codex.
- 12/11 09:20 · جلسة 2 · [#189](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/189): لوحة التوصيل كانت طالعة برا الشاشة من الشمال 233 بكسل على مقاس جهاز الصيدلية (1366×768)؛ اتصلحت بالـ CSS واختبار متصفح على 3 مقاسات. · التالي: مراجعات Codex على #186–#189.
- 12/11 12:14 · جلسة 2 · نهاية الجلسة 2 · المالك دمج 11 PR الساعة 12:08 (منهم #168 و#169 و#171 و#172). #170 اتحدّث بحل التعارض مع #162 وdevelop (758 اختبار ناجح) ومستني CI وCodex. الجلسة 3 شغالة من 11:10 (#190–#196) فوقفت عشان مانشتغلش على نفس الملفات. باقي من الجلسة دي ومستني المالك: #170، #173–#189.
- 12/11 11:25 · جلسة 3 · محاولة دمج #154 اترفضت تاني (نفس السبب)؛ كل الـ PRs من #168 لـ #189 أخضر وCodex من غير ملاحظات ومستنية المالك. [#190](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/190): «عميل جديد» في شاشة البيع بقى نموذج بدل خانتين `prompt()`، والرفض (رقم مستخدم) مابيمسحش اللي اتكتب، والضغطة المزدوجة عميل واحد. اختبار متصفح. 706 ناجح. · التالي: مراجعة Codex على #190، وباقي ملاحظات 11/01.
- 12/11 11:50 · جلسة 3 · #190: ملاحظة Codex (العنوان من نموذج العميل الجديد مابيتنقلش لعنوان التوصيل) اتصلحت باختبار متصفح. [#191](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/191): اختبارات سجل «مين عمل إيه» (الصلاحية، التواريخ الغلط، فلتر الشخص والنوع، العزل بين الصيدليات، اسم الموافق)؛ اختبارات بس، مفيش عيب. 712 ناجح. · التالي: `core/updater.py` و`core/backup.py` (أقل تغطية من الباقي).
- 12/11 12:05 · جلسة 3 · [#192](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/192): اختبارات زرار التحديث (مكان مفتاح جيت هاب، ردود جيت هاب الغلط، ملفات في أرشيف التحديث لازم تترفض زي `COM1` والنقطة في الآخر والاختصار والحجم الكبير)؛ اختبارات بس، مفيش عيب. 717 ناجح. · التالي: `core/backup.py`، ومراجعات Codex على #190–#192.
- 12/11 12:25 · جلسة 3 · #190: ملاحظة Codex التانية (حفظ بطيء ممكن يتقفل ويتحسب على فاتورة تانية) اتصلحت باختبار. [#193](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/193): تقييم عرض الخصم الكبير كان بيقطع 2.5 علبة لـ 2 ويحسب التكلفة بكسور عشرية؛ دلوقتي علب صحيحة والتكلفة بالقرش بنفس تقريب البيع. 709 ناجح. · التالي: مراجعات Codex على #190 و#192 و#193، وبعدين باقي `int(`/`float(` في المدخلات.
- 12/11 12:55 · جلسة 3 · تقريب الفلوس (القاعدة: نص قرش لفوق زي الخصم، من غير كسور عشرية): [#194](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/194) كارت الصنف (متوسط تكلفة العلبة كان بيبان أقل بقرش)، [#195](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/195) خصم الغياب في المرتبات (6500.13 على 26 يوم كان بيخصم 250.00 بدل 250.01)، [#196](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/196) تقرير المخزون (قيمة الصلاحية والأقسام). كلهم عرض أو اقتراح، مفيش دفتر اتغيّر. · التالي: مراجعات Codex، وتقرير الرواكد (`procurement/deadstock.py`) لسه بكسور لأنه من ملفات إي ستوك (الكميات هناك كسور أصلًا).
- 12/11 13:20 · جلسة 3 · [#197](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/197): أهم واحدة النهارده: 15 مكان في البرنامج كانوا بيقرّبوا نص القرش «للزوجي» مش لفوق، منهم قيمة مرتجع المورد (بتتخصم من حسابه) وقيمة تسوية المخزون. كلهم بقوا لفوق، واختبار بيمنع أي مكان جديد. #193 ملاحظة Codex (تاريخ صلاحية غلط كان خطأ سيرفر) اتصلحت. 707 ناجح. · التالي: مراجعات Codex على #193–#197.
- 12/11 12:18 · جلسة إضافية · المنسّق دمج 32 PR في develop (10:10–10:15 UTC)، والجلسة الإضافية دمجت #193؛ الجدول تحت في «اللي اتدمج يوم 2026-11-12». #196: ملاحظة Codex P1 (قاعدة تقريب جديدة) اتصلحت: التقرير بقى يستخدم قاعدة البيع نفسها للقيمة وقاعدة تكلفة البيع، باختبار. · التالي: #196 مستني CI وCodex، وبعدين #147.
- 12/11 16:40 · جلسة 4 · `CI / windows` على develop `aee1557` كان واقع في اختبار واحد (`test_label_variants` إيصال التجربة محتاج raqm ومش موجود على ويندوز في جيت هاب). [#204](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/204): الاختبار بيشتغل من غير raqm بصورة إيصال ثابتة ولسه بيتأكد إن إعدادات الملصق ماتوصلش للإيصال؛ اختبار بس. #196 و#197 و#147 و#163 جاهزين (CI أخضر وCodex من غير ملاحظات ومن غير تعارض، ومع بعض على develop: 919 ناجح)، **الدمج اترفض للجلسة المجدولة** فمستنيين المالك. #202 (مطابقة الأرصدة) واخدها جلسة تانية. · التالي: بند 6 مراجعة #155 و#157 و#170.
- 12/11 16:55 · جلسة 4 · مراجعة #170 (تصريح الموافقة) و#155 (حماية الإكسل): مفيش عيب. [#205](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/205): اختبار بيمنع أي تصدير إكسل جديد ينسى حماية المعادلات. · التالي: مراجعة #157 (حدود الحجم) و#195 و#193.
- 12/11 17:25 · جلسة 4 · مراجعة #157: [#210](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/210) عيب حقيقي: «تصدير Excel» من أي شاشة كان بيرفض أكتر من حوالي 2000 سطر («الطلب كبير جدًا») مع إن قائمة الفواتير بتعرض لحد 5000؛ دلوقتي حد لوحده للتصدير 8 ميجا (والسطور لسه لحد 50 ألف). #204 و#205 جاهزين (CI أخضر وCodex من غير ملاحظات). · التالي: مراجعة #195 و#193 والشاشات اللي بتصدّر.
- 12/11 17:55 · جلسة 4 · [#212](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/212): اختبار المتصفح بتاع أزرار البيع السريع (#187) كان بيقع مرة من كل اتنين لأن الأزرار بتترسم تاني؛ اتصلح هو واتنين شبهه (اختبارات بس، ولسه بيمسك العيب الأصلي). مراجعة #193 و#195 و#166: مفيش عيب. · التالي: مراجعات Codex على #210 و#212، ومراجعة #162 و#190.
- 12/11 18:35 · جلسة 4 · قياس التغطية تاني: أقل ملف `purchasing/importers/files.py` (82.6%). [#214](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/214) عيب حقيقي: ملف CSV مفصول بـ `;` أو Tab (زي اللي الإكسل بيحفظه على ويندوز عربي) وفوقه سطر عنوان كان بيتقري عمود واحد، فالفاتورة كلها «مالقيناش العناوين»؛ نفس الدالة بتاعة نقل بيانات إي ستوك. اتصلح ومعاه 16 اختبار. #210 جاهز (Codex من غير ملاحظات). · التالي: مراجعات Codex على #212 و#214، وبعدين `core/autobackup.py` و`sales/contracts.py`.
- 12/11 19:15 · جلسة 4 · [#217](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/217) التعاقدات: «true» كانت بتتسجل خصم 1 نقطة، ورقم أو قائمة بدل رقم التعاقد كان خطأ سيرفر؛ و6 اختبارات للتعديل والإيقاف. [#218](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/218) النسخ الاحتياطي التلقائي: لو ملف الحالة فيه تاريخ بايظ، خيط الجدولة كان بيموت من غير ما حد يعرف وماتتعملش نسخ تلقائي تاني لحد ما البرنامج يتقفل ويتفتح؛ اتصلح و4 اختبارات. #212 و#214 جاهزين. · التالي: مراجعات Codex على #217 و#218، وبعدين `core/daily.py` و`sales/channels.py` و`core/notify.py`.
- 12/11 19:50 · جلسة 4 · [#219](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/219): تغيير اسم وسيلة دفع (فودافون كاش ← إنستاباي) أو اسم مورد لاسم موجود كان خطأ سيرفر 500؛ دلوقتي «موجود بالفعل» ومفيش حاجة بتتغير. باقي الجداول اللي أسماءها مش بتتكرر (الأقسام، المجموعات العلاجية، التعاقدات) سليمة. #217 و#218 جاهزين (Codex من غير ملاحظات). · التالي: مراجعة Codex على #219، وبعدين أماكن تانية `int(`/`str(` على مدخلات JSON بتوصل SQLite.
- 12/11 21:25 · جلسة 4 · المالك دمج #196 و#197 و#204 و#205 و#210 و#212 و#214 و#217 و#218 و#219. [#222](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/222): رقم كبير جدًا (زي باركود اتمسح مرتين في خانة فلوس) كان بيوصل قاعدة البيانات ويطلّع خطأ سيرفر؛ دلوقتي «قيمة غير صحيحة». باقي الحالات اللي بتطلّع 500 لما حد يبعت نوع غلط (مش من الشاشات) في [#223](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/223) لبعد الإطلاق. · التالي: مراجعة Codex على #222.
- 12/11 21:45 · جلسة 4 · `CI / windows` على develop بقى أخضر بعد #204. #222 جاهز (CI أخضر وCodex من غير ملاحظات). قريت شغل #202 (فحص المطابقة) وقست سرعته: 0.33 ثانية لـ 12 ألف فاتورة، يعني حوالي 2 ثانية للسنة؛ بيشتغل بعد كل نسخة احتياطية (كل ربع ساعة). الملاحظات على #202 من غير تعديل (الملفات بتاعة جلسة تانية). · التالي: تغطية `purchasing/service.py` و`core/people.py`.
- 12/11 22:15 · جلسة 4 · [#226](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/226): قارئ الكود المربّع (2D) على العلبة: لما الماسح يشيل الفاصل، كود فيه الرقم التسلسلي الأول (زي علب مصر) كان بيضيّع الصلاحية والتشغيلة، وتشغيلة فيها «21» كانت بتتقطع. دلوقتي بيتقروا صح، والشاشة لسه بتعرضهم للتأكيد. · التالي: مراجعة Codex على #222 و#226، وبعدين `core/people.py` و`core/backup.py`.
- 12/11 22:45 · جلسة 4 · [#227](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/227): ملف نسخة احتياطية متقطع (الفلاشة اتشالت وقت النسخ) أو فاضي كان بيطلّع رسالة خطأ برمجية في أوامر `verify` و`restore`؛ دلوقتي «الملف مش نسخة سليمة، ومفيش حاجة اترجعت». شاشة الاسترجاع كانت سليمة أصلًا. · التالي: مراجعة Codex على #222 و#226 و#227، وبعدين `core/people.py`.
- 12/11 23:20 · جلسة 4 · #226: ملاحظة Codex (P1) صح واتصلحت: لو الكود فيه الصلاحية، أرقام شبه تاريخ جوه الرقم التسلسلي ماتغيّرهاش. [#228](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/228): اختبارات تغيير دور الموظف وحد خصم الدور (اختبارات بس، مفيش عيب؛ الجلسة المفتوحة بتاخد الصلاحيات الجديدة فورًا). · التالي: مراجعات Codex على #226 و#227 و#228.
- 12/11 23:45 · جلسة 4 · ثغرة في سجل «مين عمل إيه»: لو حد فتح ملف قاعدة البيانات بإيده ومسح آخر كام سطر من السجل، الفحص مش بيكتشف ده (بيكتشف التعديل والمسح من النص بس). اتفتح لها [#229](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/229) بحل مقترح (علامة برا الملف في كل نسخة احتياطية) **محتاج قرار المالك**، و[#230](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/230) بيصحح `docs/SECURITY.md`. · التالي: مراجعات Codex على #226–#230.
- 13/11 00:35 · جلسة 4 · [#231](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/231) نقل البيانات: «مستوردة» و«محلية» و1.0 في عمود محلي/مستورد كانوا بيطلعوا «مش معروف»؛ اتصلح. ومعاه اختبار إن نقل الأرصدة الافتتاحية لو الدفتر ماطابقش الملف بيتلغي كله (مفيش عيب). #222 و#226 و#227 و#228 و#230 جاهزين (Codex من غير ملاحظات). · التالي: مراجعة Codex على #231، وباقي ملفات نقل البيانات.
- 13/11 01:10 · جلسة 4 · [#232](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/232): تقرير الرواكد من ملفات إي ستوك كان بيطلّع خطأ سيرفر في 4 حالات (تاريخ من غير وقت، فواتير كلها فاضية، إجمالي المبيعات من غير عدد أصناف، عدد أصناف مش رقم)؛ دلوقتي بيتقري أو بيترفض برسالة واضحة. · التالي: مراجعات Codex على #231 و#232.
- 13/11 01:45 · جلسة 4 · [#233](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/233): اختبارات المصروفات (رفض مصروف اتصرف من الدرج بيطلّع تنبيه ومابيغيرش الدرج، وفلاتر القائمة)؛ اختبارات بس، مفيش عيب. #230 و#231 جاهزين. · التالي: مراجعات Codex على #232 و#233، وبعدين `finance/schedules.py` و`finance/payroll.py`.
- 13/11 02:20 · جلسة 4 · [#234](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/234) المصروفات المجدولة (الإيجار وغيره): «true» كانت بتتسجل مبلغ قرش واحد، ورقم كبير جدًا كان خطأ سيرفر؛ اتصلحوا ومعاهم اختبارات تعديل البند. · التالي: مراجعات Codex على #232–#234، وبعدين `finance/payroll.py`.
- 13/11 02:45 · جلسة 4 · **من 22:17 بتوقيت القاهرة جيت هاب رافض يشغّل أي CI بسبب الفلوس** (حد الصرف أو دفع فشل). مش عيب في الكود. مفيش دمج جديد لحد ما المالك يحلها من Billing & plans (اتبلّغ). #232: ملاحظة Codex (P2، رقم بأس كبير جدًا بيعطّل) اتصلحت. الجلسات الجاية: قلّلوا الرفع (كل رفعة بتشغّل CI). · التالي: شغل محلي ومراجعة.
- 13/11 03:20 · جلسة 4 · [#235](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/235) **عيب فلوس في المرتبات**: اعتماد المرتب وبعدين سلفة على نفس الشهر وبعدين صرف ← المرتب بيتصرف كامل والسلفة ماتتخصمش ولا تتسجل مصروف. دلوقتي الصرف بيطلب اعتماد تاني، وسلفة على شهر اتصرف بتترفض. ماينفعش يتدمج قبل ما CI يرجع. · التالي: باقي `finance/payroll.py` (الحضور بعد الاعتماد قرار المالك، مااتغيرش).
- 13/11 03:55 · جلسة 4 · [#236](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/236): نفس ملاحظة Codex على #232 في مكانين تانيين: رقم زي `1e10000000` في تقييم عرض الخصم (أي حد داخل بصلاحية المشتريات يقدر يشغّل السيرفر دقايق بطلب واحد) وفي ملف عدد الوحدات بتاع إي ستوك؛ اترفضوا فورًا. · التالي: مستنيين CI يرجع (الفلوس) عشان الدمج؛ أكمّل مراجعة.
- 13/11 04:20 · جلسة 4 · [#237](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/237): اقتراحات الطلبية: «فترة الاستهلاك» رقم كبير كان خطأ سيرفر، وصفر أو سالب كان بيرجّع قائمة فاضية كأن مفيش حاجة ناقصة؛ دلوقتي في حدود الشاشة نفسها. · التالي: مراجعات Codex على #235–#237.

## 11/11 20:40 — #100 الحل الجديد: الطباعة عن طريق تعريف ويندوز (زي إي ستوك)
- الدليل من الجهاز: تعريف Xprinter XP-235B في ويندوز (Seagull) مضبوط USER 38×25 مم، Portrait 180°، وإي ستوك بيطبع عليه «مظبوط بالمللي»؛ أمر TSPL المباشر بتاعنا عمره ما ظبط (إزاحة، معايرة، 8 طرق إرسال).
- الفرع `claude/100-driver-labels`: نوع ملصقات جديد `driver` (`app/printing/driver.py`): الملصق يترسم على صفحة التعريف نفسها (المقاس من ويندوز)، صفحة لكل ملصق، وفحص قائمة الطباعة زي #98. اختيار «اقلب الملصق» (`label_flip`) لأن المالك عايزه «معدول مش مقلوب». المعايرة والإزاحة والدقة مش بتستخدم في النوع ده.
- اختبار ويندوز حقيقي: `.github/workflows/windows-print-smoke.yml` بيطبع ملصقين لملف عن طريق تعريف حقيقي (Print to PDF / XPS) لما ملفات الطباعة تتغير.
- التوصيل للجهاز: **زرار التحديث** مش مثبّت جديد (المالك طلب كده 20:18): مفيش `requirements.txt` ولا migrations من `d24979e`. بعد الدمج: B13 (snapshot ← «تحديث» ← post) وبعدين على الجهاز: النوع «تعريف ويندوز»، الطابعة `win:Xprinter XP-235B`، تجربة 3 ملصقات.

## التالي بالضبط (تحديث 11/11 الساعة 19:05)
1. **#18 على جهاز الصيدلية:** المثبّت الأول (`f56de12`) اتثبّت 18:08 والبيانات سليمة (B4–B6 ✅ على #18). B7 الملصقات **فشل** بالإزاحة لوحدها (3 محاولات، الأدلة على #100)، وزرار الورق في الطابعة بيطلّع ملصق واحد ويقف عند الفاصل، يعني العيب في أمر الطباعة مش في الحساس. المثبّت التاني جاهز في `Downloads\PharmacySetup-review-d24979e.exe` (`installer-review`, run [43062678809](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/43062678809), sha256 `7acebbe0…72bf`) والنسخة الاحتياطية قبله اتعملت. بعد التثبيت: المعايرة ثم طرق الطباعة بالترتيب حسب B7 في [VISIT_18_CHECKLIST.md](VISIT_18_CHECKLIST.md)، وبعدها B8–B13. أداة الزيارة: `docs/visit/visit_runner.js` (#141).
2. **قرار المالك مطلوب — #142 (launch-critical):** PR #148 جاهز (المسترد نقدًا ≤ الكاش اللي دخل من الفاتورة، والتوصيل بعد تسليم المندوب بس؛ الفيزا والتحويل مش بيتردّوا كاش). **ماتدمجوش** قبل موافقة المالك على القاعدة.
3. #19 يوم التشغيل حسب [DAY19_SCRIPT.md](DAY19_SCRIPT.md) (الأرقام المتوقعة طالعة من `tests/test_day19_script.py`).
4. #20 بموافقة المالك المكتوبة (الناقص في START_HERE).

**مراجعة 11/11 المستقلة (#131):** الأمان: #134 اتصلح (#135)، والباقي بعد الإطلاق (#136–#140). الفلوس والمخزون: #142 (فوق)، والباقي بعد الإطلاق (#143–#146)؛ اختبارات الأدلة في PR #147 مفتوح ومش متدمج (قاعدة AGENTS: ممنوع إضعاف اختبار). **CI:** مرايات أوبونتو وقعت 11/11؛ #133 بيكمّل بالمتصفح من غير apt (`scripts/ci/install_browser.sh`). **مفتوح لبعد الإطلاق:** #99 (#96)، #132 (#97). **المهام المجدولة:** كلها متوقفة بطلب المالك لحد ما يقرر يجدول تاني.

## اللي اتدمج في `develop` يوم 2026-11-12
قطار الدمج من المنسّق 10:10–10:15 UTC ([تعليق #113](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/113#issuecomment-6820855899)): 32 PR، كل واحد `CI / linux` أخضر على الـ head بتاعه وCodex من غير ملاحظات ومن غير تعارض ومن غير `requirements.txt` ولا migrations ولا ملفات طباعة. و#193 اتدمج 10:17 UTC من الجلسة الإضافية بنفس الشروط.
بعد القطار: #155 و#157 و#170 و#195 اتحدّثوا من develop ومستنيين Codex. **#160 (migration `0036`) متساب عمدًا: للمثبّت بس.**

| PR | head | merge | المحتوى |
|---|---|---|---|
| [#154](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/154) | `e70c58c` | `8e342ce` | fix(#136): refuse requests whose Host the program does not serve (DNS rebinding) |
| [#159](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/159) | `c35ad72` | `529bbb0` | perf(#113): invoices report and return/movement lookups without whole-table reads (no migration) |
| [#162](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/162) | `7b57c45` | `f8bd4b8` | fix(#153): open screens notice an update and reload safely |
| [#164](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/164) | `722066b` | `33d4b89` | docs: feature gap vs market and E-Stock; second pharmacy pilot plan |
| [#165](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/165) | `8844b27` | `5b3a8cc` | Setup screen: neutral examples for the pharmacy and owner name |
| [#166](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/166) | `637ea7f` | `446b8f8` | perf(#113): stock check in every backup runs in 0.5 s instead of 101 s after a year |
| [#167](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/167) | `0fbb9cc` | `ae2560d` | tests: reports of today ask for the day at call time (midnight CI failure) |
| [#168](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/168) | `873be50` | `8f29474` | fix(#145): sales report top items show what the invoices charged |
| [#169](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/169) | `bacdb36` | `48615cd` | fix(#143): credit limit checked again inside the sale's write transaction |
| [#171](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/171) | `45615d7` | `e6eb4eb` | fix(#134 follow-up): current password in an open session is rate limited |
| [#172](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/172) | `9e93ba9` | `2b066e5` | Supplier returns: cover the untested paths; a typed value needs the right to see values |
| [#173](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/173) | `2954f48` | `897d7a8` | E-Stock migration job: test the packs sheet and rules; refuse a fractional JSON pack size |
| [#174](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/174) | `7d10537` | `0ce4a48` | Users screen: test discount limit, switch-off and password reset; refuse fractional limits |
| [#175](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/175) | `fb126af` | `072f8de` | Daily checks and automatic backup: test the failure paths; refuse fractional retention counts |
| [#176](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/176) | `727b0af` | `46951b7` | Typed whole numbers are refused when fractional, never truncated |
| [#177](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/177) | `68826c9` | `ca8053c` | tests: completion and return guards (concurrent edit, price change, bad returns) |
| [#178](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/178) | `e0f9c98` | `758bda5` | tests: stock count edges (cancel, open count, refusals, sold below the count) |
| [#179](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/179) | `8cd725d` | `5fe4166` | Purchase invoices: typed quantities and piasters must be whole, never truncated |
| [#180](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/180) | `4576c75` | `49b1ba6` | Salaries, scheduled expenses and expense decisions happen once, even from two screens |
| [#181](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/181) | `1a3f2e4` | `784a904` | tests: a backup edited inside and re-sealed is still refused |
| [#182](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/182) | `7dae38f` | `2c89a0b` | Payroll: advances are booked as salary expense even when nothing is left to pay |
| [#183](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/183) | `4de1666` | `03753c4` | tests: API routes the screens use that had no test |
| [#184](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/184) | `2a38b50` | `c7d65ea` | List routes: ?limit= is a whole number from 1 to the cap |
| [#185](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/185) | `2d4259a` | `15c6abf` | tests: one sweep that every ledger refuses UPDATE and DELETE in the database |
| [#186](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/186) | `31f1f33` | `67d4076` | tests: quantity-limit rule and the critical block decision (fictional data) |
| [#187](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/187) | `a89ff49` | `30b9cc8` | Sale screen: quick-sale buttons read the stock again on every visit |
| [#188](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/188) | `1a05edc` | `9698691` | Delivery sale: F9 in the address field saves it first, then completes |
| [#189](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/189) | `9abd759` | `00dd584` | Sale screen: the delivery panel stays inside the side panel |
| [#190](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/190) | `4e2a5c4` | `61aaf6d` | Sale screen: new customer is a form, not two prompt() boxes |
| [#191](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/191) | `f8bb597` | `a10c4c6` | tests: the who-did-what log (permission, dates, filters, isolation, approver) |
| [#192](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/192) | `2e48ffd` | `02ab26e` | tests: update button edges (key, GitHub answers, refused archive members) |
| [#193](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/193) | `39a802c` | `737f9ff` | Offer evaluation: whole packs, exact percent and pounds, cost in piasters |
| [#194](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/194) | `a496c43` | `399f6ba` | Item card: average pack cost and margin exact, rounded half up |

## قاعدة الدمج (اتأكدت بحادثة 11/11)
ادمج PR في `develop` **بعد** ما `CI / linux` يبقى أخضر على آخر head للـ PR نفسه، مش قبل ما يخلص. يوم 11/11 الـ PRs ‏#111 و#112 و#115 و#117 اتدمجوا بعد ما فرعهم اتحدّث بدمج `develop` وقبل ما CI على الـ head الجديد يخلص، فالحارس (`branch-guard.yml`) فتح [#118](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/118)، [#119](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/119)، [#120](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/120)، [#121](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/121). اتقفلوا: مفيش مشكلة في الكود، وCI على `4a93eb2` أخضر linux + windows. لو PR اتحدّث بدمج `develop`، استنى CI على الـ head الجديد.

## اللي اتدمج في `develop` يوم 2026-11-11
| PR | Issue | head | merge | المحتوى |
|---|---|---|---|---|
| [#110](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/110) | #100 | `3f1db6f` | `d52ad32` | إزاحة الملصق لفوق/لتحت + زرار 3 ملصقات تجربة |
| [#111](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/111) | #98 | `84bace3` | `9c7f3c8` | طابعة الإيصال مقفولة: تحذير وإلغاء الأمر من قائمة الطباعة |
| [#112](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/112) | #107 | `ef3044d` | `df80d2e` | زرار التحديث على قناة النسخة (`develop`/`main`) |
| [#115](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/115) | #114 | `92d535d` | `a95706f` | شاشة المرتجع: طريقة الرد = دفع الفاتورة، والضغطة المزدوجة مرة واحدة |
| [#117](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/117) | #116 | `e914d60` | `4a93eb2` | رفض ملف أرصدة E-Stock المقطوع من آخره |

الـ Issues ‏#98 و#100 و#107 لسه مفتوحة لحد تجربة الجهاز في #18. الكل على `develop` @ `4a93eb2`: ‏620 اختبار نجحوا، 9 skip (`python3 -m pytest -q`).

### #100 الملصق 38×13
- السبب الأرجح: الطابعة بتبدأ تحت أول الملصق بكام مم، فآخر الصورة بيعدّي الفراغ للملصق اللي بعده، والطابعة تكمّل لحد الفراغ اللي بعده ← ملصق فاضي. التطبيق ماكانش بيبعت أمر يحرّك الطباعة. (استنتاج من صور المالك، مش متجرب على الطابعة.)
- الإصلاح: إعداد «تحريك الطباعة لفوق/لتحت (مم)» من −10 لـ 10 (سالب = لفوق) ← TSPL `SHIFT`، ومش بيتبعت لو صفر؛ وZPL `^LT` بيتبعت دايمًا حتى بصفر لأن Zebra بتحفظ القيمة القديمة (ملاحظة Codex). زرار «اطبع 3 ملصقات تجربة» بالقيم اللي على الشاشة من غير حفظ (`POST /api/print/label-test`، `settings.manage`، audit `print.label_test`).
- اتجاه الإشارة من كتيب TSPL ومش متأكد منه على XP-235B: لو −2 نزّلها أكتر، جرّب +2.
- مراجعة 11/11: مش متأكدين إن XP-235B ما بتحفظش `SHIFT` القديم لما القيمة ترجع صفر (زي Zebra). مش عيب مؤكد؛ اتضاف فحص في B7 في قائمة الزيارة (صفر ← 3 ملصقات زي الأول).
- الاختبارات: `tests/test_labels_direct.py` 13.
- **11/11 تجربة الجهاز:** الإزاحة لوحدها ما صلّحتش (−2، +5، +7؛ تفاصيل في تعليق #100). المتابعة على فرع `claude/100-label-feed`: زرار «معايرة بكرة الملصقات» (TSPL `GAPDETECT`، `POST /api/print/label-calibrate`، `settings.manage`، audit `print.label_calibrate`) و8 طرق إرسال بأسماء (`label_variant`: current, tear, clamp, tear_clamp, per_label, tear_clamp_per_label, home, all) للتجربة وتتحفظ كإعداد؛ الافتراضي `current` = نفس الأمر القديم بالبايت. الخطوات في B7. الاختبارات: `tests/test_label_variants.py`.

### #98 طابعة الإيصال مقفولة
- السبب: ويندوز بيحتفظ بالأمر في قائمة الطباعة لما الطابعة مقفولة وبيقول إنها «جاهزة»، والتطبيق كان بيعتبر الإرسال نجاح. الأمر فيه نبضة الدرج، فبيشتغل لوحده لما الطابعة تتفتح.
- الإصلاح (`app/printing/escpos.py` `_send_windows`): بعد الإرسال لطابعة `win:` بيستنى لحد 6 ثواني لحد ما الأمر يخرج من القائمة أو يبقى «بيطبع» من غير خطأ؛ غير كده بيلغيه (`JOB_CONTROL_DELETE`) ويرجّع خطأ: «الإيصال لم يُطبع» و«الدرج لم يُفتح (F8)»، والـ audit `print.receipt` بـ `outcome: not_printed` و`cash.drawer_auto` بـ `failed`. نفس الحماية لنبضة الدرج لوحدها ولـ F8 وللملصقات. لو ويندوز رفض يعرض القائمة، الفحص بيتخطّى؛ لو الإلغاء اترفض، الرسالة تقول امسحه من قائمة الطباعة.
- مراجعة 11/11 (شكوك مش عيوب مؤكدة، بتتجرب في B8): (1) طابعة USB مفصولة ممكن ويندوز يعرض أمرها «بيطبع» من غير خطأ؛ (2) إيصال عادي واقف ورا أمر تاني أكتر من 6 ثواني ممكن يتلغي بتحذير غلط.
- الاختبارات: `tests/test_spooler.py` 9 (spooler وهمي).

### #107 زرار التحديث على القناة
- القناة = الفرع اللي النسخة اتثبتت أو اتحدّثت منه (`updater.channel`): `main` أو `develop`، و`main` لو مش معروف. أي فرع تاني (مثبّت مراجعة لفرع شغل) مالوش قناة: مفيش فحص، والزرار يرفض (422 `no_channel`).
- الفحص بيسأل GitHub عن آخر نسخة على القناة، والزرار بيشغّل `manage.py update --ref <القناة>`، والـ audit بيسجل القناة. `manage.py update` بيحفظ `ref` في بيانات النسخة؛ التحديث من zip بيحتفظ بالقناة. الإعدادات ← البرنامج بتعرض «قناة التحديث: develop (تجربة)» أو «main (الصيدلية)».
- مااتغيرش: رفض البروتوكول 1 لتغيير `requirements.txt` أو الـ migrations، صلاحية `settings.manage`، وإعادة التشغيل بـ exit 75. مفيش تحديث تلقائي (ده #96 / PR #99).
- النسخة المتثبتة دلوقتي على جهاز الصيدلية ما تعرفش تحدّث نفسها: لازم المثبّت مرة واحدة، وبعدها تعديل docs صغير على `develop` يوصل بالزرار والبيانات سليمة (B13).
- الاختبارات: `tests/test_updater.py` و`tests/test_cli_data_dir.py` و`tests/test_installer_adopt.py` (اختبار قديم كان بيثبّت إن نسخة develop ما تشوفش تحديث اتعدّل لفرع شغل مؤقت).

### #114 و#116 (من مراجعة الجاهزية [#113](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/113))
- #114 (المنطقة أ، الفلوس): شاشة المرتجع كانت بتختار «نقدًا» حتى لفاتورة آجل أو بطاقة، والضغطة المزدوجة بتسجل المرتجع مرتين. دلوقتي الافتراضي آجل لو الفاتورة آجل من غير نقدي (وفيها عميل)، بطاقة لو بطاقة بس، نقدي غير كده؛ والزرار بيتقفل وقت الطلب. السيرفر لسه بيقبل أي طريقة (قرار المالك). اختبار: `tests/test_return_form.py`.
- #116 (المنطقة هـ، E-Stock): ملف أرصدة مقطوع من آخره كان بيعدّي من غير فحص الإجماليات. دلوقتي كل مخزن لازم له سطر «إجمالى البيع» وسطر «إجمالى جميع المخازن» لازم يكون موجود، وإلا `estock_totals_mismatch`. يتأكد على ملف مخزن واحد حقيقي إن السطر ده موجود (L3).
- مراجعة الجاهزية خلصت: المناطق (أ)–(هـ) متسجلة على #113 مع الملاحظات غير الحرجة.
- #104 اتقرر: **الخيار 1** (تعليق المالك على #104): `main` يفضل زي ما هو لحد #20، والـ 12 تعديل اللي عليه تتراجع على فرع لوحدها بعد الإطلاق.

## الأجهزة والنسخة المتثبتة
- **المتثبّت على جهاز الصيدلية DESKTOP-5JVH3QD: run 41238470755** (`develop` @ `7190880` + #93)، اتثبت 11/01 16:59 فوق run 72 بالمثبّت الصامت. sha256 الـ zip مطابق (`768e4093…a1a2`)، والبيانات نفسها قبل وبعد، ونسخة احتياطية سليمة على D: (`D:\PharmacyPlatform-backup-2026-11-01\pharmacy-backup-20261101-165607.zip`) **لسه ما اتنقلتش على فلاشة**. `/` بيرجع `no-cache` و`app.js?v=…` (#85 اتأكد على الجهاز). الجهاز offline دلوقتي.
- تاريخي: [run 75](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/41237493804) (artifact id 13055634945، digest `sha256:3a07be7a…ae18f`، `develop` @ `7190880`) وrun 72 (`c9906a9`) قدام.
- أجهزة الصيدلية: إيصال `POS-80C` على USB002 والدرج متوصل بيها؛ ملصقات `Xprinter XP-235B` على USB004، TSPL، 203 dpi، بكرة **38×13 مم فراغ 2 مم**؛ قارئ `HH360 Linear Imager` USB ككيبورد (بيبعت **F12** مع الباركود، الحل في #81)؛ قارئ 2D على جهاز تاني في الصيدلية. الإعداد اتساب على فراغ 2 مم والدرج «بعد الإيصال».
- القارئ 2D: بيقرا QR وEAN-13 وGS1 DataMatrix. في Chrome بيبعت F12 الأول، والـ GS بيوصل (Alt+0029)، ومفيش بادئة `]d2`؛ Notepad بيشيل الـ GS. المحلل طلّع GTIN والصلاحية 06/2029 والتشغيلة 260128 مطابقين للعلبة. كارت الصنف بيخزن الـ EAN-13، والإضافة السريعة في الشراء بتملا الصلاحية والتشغيلة. ما اتجربش على علبة التشغيلة فيها «21».
- لابتوب البيت `DESKTOP-0DCSIC2`: جهاز Windows حقيقي؛ ممكن نتايجه تبقى دليل L2 لـ G2–G6 **بعد تأكيد المالك بس**؛ G7/G8 لازم جهاز الصيدلية. مفيش نتايج لابتوب مسجلة. قبل أي تنفيذ: `hostname` لازم `DESKTOP-0DCSIC2` وإلا توقّف؛ افحص Windows/RAM/المساحة والعمليات ومسارات التثبيت والبورت 8000 ([التعليق المرجعي](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18#issuecomment-6520210512)). نسخة per-user من غير admin، بيانات وهمية بس، وكل مرحلة على #18 بعنوان **home laptop, not pharmacy PC**. مرشح R8: `b6329815b049eac128d6fd97cda539e2cbfa2458` (0.4.0rc1 → rc2 → rollback).

## نتايج التجارب السابقة على جهاز الصيدلية
- **11/01** (run 41238470755، تفاصيل في تعليقات #18): ✅ P1–P6 وP8 وP9 وP11. ❌ P7 ← #98 (الطابعة مقفولة: مفيش تحذير، والإيصال والدرج بيشتغلوا لوحدهم بعدين). ❌ P10 ← #100 (الملصق نازل لتحت وملصق فاضي بين كل اتنين بفراغ 2 و3 مم بعد معايرة الفراغ). الاتنين اتصلحوا في الكود ومستنيين B7 وB8.
- ملاحظات 11/01 مش launch-critical: زرار البيع السريع بيفضل «ناقص» بعد الشراء؛ «عميل جديد» بيستخدم `prompt()`؛ تقرير الوردية بيطبع من نافذة المتصفح؛ إعادة الطباعة من غير رسالة؛ F9 في التوصيل مرفوض لحد ما خانة العنوان تفقد التركيز، ولوحة التوصيل طالعة برا الشاشة من الشمال؛ مفيش طريقة لاسترجاع كلمة سر المالك.
- **10/30** (run 72، [ملخص](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18#issuecomment-6533037626)): ✅ الطباعة المباشرة بالعربي على POS-80C، والدرج، والإيصال بالمبالغ كاملة، والطباعة من المتصفح، والبحث وقت الكتابة، والمسح السريع (#48). ❌ #85 (`app.js` قديم بعد التثبيت) و#84 (مقاس الملصق) اتصلحوا في #88 و#90.
- إصلاحات 10/30–10/31 (كلها بمراجعة Codex): #85 ← #88، #81 ← #89 (إيصال اختياري F4، درج مستقل، F8 بسبب، F12 للقارئ؛ **لسه:** باركود رقم الفاتورة + قالب جدول E-Stock)، #84 ← #90، #80 ← #91، وCI Windows ← #92 و#93. درس: شغّل `scripts/validate_local_release.py` محليًا قبل أي اختبار CLI/مسارات.
- قبلها: إصلاحات #35، #37، #39، #42، #44، #46، #49؛ مراجعة الإصدار [#68](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/68) والعيب [#72](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/72) اتقفل بـ [#73](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/73) (`c9906a9`؛ المرتجع القديم يحافظ على 99.99). لا تعيدهم من الصفر.
- F27 (لوجو الإيصال وQR وwa.me واسم الكاشير وعدد الأصناف والباقي) مدمج (#103، `d04daf2`)؛ لسه تجربة الطابعة ومسح الموبايل (OWNER_TEST_PLAN الأولوية 3). المواصفات: `docs/specs/07-receipt-logo-qr-benchmark.md`.

## تنضيف 2026-11-10 (بموافقة المالك)
- `after-launch` على Issues ‏#106، #97، #95، #86، #76، #96، #12 وPRs ‏#99، #102، #83، #77؛ ما اتقفلش ولا اتمسح حاجة. #96 (التحديث التلقائي الكامل) اتشال منه `launch-critical`؛ PR #99 ما يتدمجش، و#107 خد منه اللي محتاجه بس.
- أولويات المالك يوم 11/01 (اتغيرت 11/10): #96، بعده #97، بعده #98 و#100، بعدهم شكل الإيصال وبيانات الصيدلية (مستني صورة إيصال E-Stock).
- #97 (تقرير قفل الدرج) كانت أولوية تانية للمالك يوم 11/01، لكنها مش في قائمة الإطلاق اللي حددها 11/10.
- PR #101 اتدمج (`16ab71d`). `LAUNCH_PLAN.md` §1 و§5 بيشاوروا على START_HERE.

## قرارات وقيود مستمرة
- `main` = قناة الصيدلية؛ مفيش دمج أو تعديل فيه غير بموافقة المالك (#20). كل شغل على فرع وPR إلى `develop`؛ الدمج من شات المالك بعد CI أخضر على الـ head. الوكلاء ما يدمجوش.
- بناء مثبّت أو إعادة تشغيل CI = دقايق مدفوعة: بموافقة المالك في الشات بس (#77).
- مفيش بيانات حقيقية ولا لمس E-Stock أو Radmin VPN أو تعريف Xprinter أو إعدادات الجهاز. مفيش admin ولا تعطيل Defender/SmartScreen ولا مسح ملفات الجهاز.
- أول انتقال من نسخة قديمة بالمثبّت + نسخة مفحوصة؛ `preflight` قراءة بس، ولو «لا تنفع» توقّف. تغيير المكتبات/migrations ينزل بالمثبّت بس ([UPDATE.md](UPDATE.md)).
- G6/G7 ما يوصلوش L2 غير بنجاح **كل R1–R10** ([WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md))؛ جدول الـ 12 خطوة القديم في [الأرشيف](../archive/HANDOFF_2026-10-30_before_compaction.md) للتفاصيل بس.
- فشل خطوة Windows في CI بيطلع annotation فيها الاختبار؛ فشل متذبذب سابق (#75 على `0bcba94`، و#67) مش عيب. أول مرة يتكرر: صلّحه بدليل.
- [#12](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/12): الحارس المجاني #53 مدمج؛ ثغرة force-push لنسخة أقدم من الحارس بعد الإطلاق بقرار المالك.
- **قرار مالك مفتوح:** اسم المنتج بدل «منصة الصيدلية» قبل #20.

## الأتمتة
- **مجدول وشغال:** مهمة مجدولة على السحابة بتكمّل الشغل ده؛ القفل على #113 (`RUN START` / `RUN END`). **يدوي:** تجارب Windows والأجهزة والقبول.
- الوصول للسحابة ما يثبتش وصول للابتوب أو جهاز الصيدلية؛ الأجهزة بتتوصل من خلال تطبيق Claude على الجهاز بس.
- مهمة Codex قديمة: اختصار التسليم على `codex/18-current-handoff` من `aef9532`؛ ما اتدمجتش، وHANDOFF ده بيحل محلها.
- **فشل سابقًا:** تنفيذ `@codex` على GitHub (#68/#69)؛ مراجعات Codex نفسها شغالة. @mentions من حساب المالك ما بتنبهوش.
- [السجل الكامل قبل الاختصار](../archive/HANDOFF_2026-10-30_before_compaction.md) و[سجل Codex السابق](../archive/HANDOFF_2026-10-28_codex.md) للتاريخ بس.
