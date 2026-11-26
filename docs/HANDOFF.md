# HANDOFF — current state and queue

Rules: `docs/OWNER_RULES.md` (wins), `AGENTS.md`. Gates and percentage: `docs/STATUS.md` (73.25%, no new
acceptance evidence). Log up to 23/11: `docs/archive/HANDOFF-2026-11.md`. Only the session that claimed this
file on #113 edits it; other sessions log on #113.

## Afternoon and evening 26/11 — FX-D, UX-26, FX-E (newest line last)
Merged into `develop` after release 6 (`5500cd8`), each with a failing test first, the full local suite, a
fresh-context agent review, linux green (windows where it runs) and Codex clean on the merged code:
- #477 `7e53d40`: every import-run entry point (analyze, read, decide, apply, cancel) needs the kind's
  permission (#461 P2, #324 S7).
- #476 `a73d780`: restore swaps the database and its E-Stock archive as one set; a cut restore is settled
  before the database is next opened (#461 P1).
- #481 `6dced6e`: the items import asks for the pack layout of a counted item (`pack:N` / whole box / link /
  skip) instead of guessing whole box; only the E-Stock move may defer it (#461 QA-007).
- #479 `53c7846`: invoices report totals of every match, cursor pages, Excel with every invoice (#461 P2).
- #480 `421d094`: update space check per disk (backups, TEMP, data, database folder) when scheduling,
  before the stop and in apply (#461 P2).
- UX-26: #478 `d45d783` (sale words, scan with no full box adds a unit, English names left to right),
  #482 `19b6a39` (English names in capitals, keyboard follows the field), #484 `9cf20c6` (receipt options:
  item / invoice discount, rounding line, bigger customer details).
- FX-E: #483 `5e9cb56` (item search at catalog scale, QA-012), #485 `cb2bdff`
  (`POST /api/system/keyboard-layout`; **needs one real Windows check on the Heikal PC**). #486 (name typed
  on the other keyboard language) open at the time of writing.
- **Release 7** = everything above (+ #486 if merged first): one PR `develop` → `main`; see #113 for its
  number and SHA. No migration, no requirements change. Not installed: waits for «نزّله».
- **Not done:** #461 P1 mixed-tender refund / electronic exchange: design first, #487 (it touches the shift
  and Hikma readers of `sale_returns`). `codex/fix-b-461` and `codex/fix-c-461` still not opened. #461 P2
  QA-010 (odd-piastre residual at shift close) and #252 (LAN over HTTP) unowned.
- Open P2/P3 left on purpose: #476 `smart_update.py` / `dbhealth.py` do not settle a cut restore before
  opening the database, no fsync of the staged database; #479 the two print buttons print the listed page;
  #480 two cloned disks with one volume serial count as one; #483 clock set backwards in the name-index label.
- **Next:** #487 (strongest model, fresh session); device checks after «نزّله» (list in the release PR).

## Day 26/11 — CC-1 (newest line last)
- **Done:** #463 DEV-PUR (built by Codex) reviewed, review P1 fixed test-first (warehouse invoice with a
  stored bonus refused at approval), merged `05e52c6`. #471 six owner decisions in OWNER_RULES, merged
  `0a08c88`. #472 #461 fix A (stocktake expired/recalled surplus P0, stale stocktake approval P1, supplier lot
  credit P1; built by Codex) reviewed, review P1 fixed test-first (same P0 through the manual adjust screen),
  merged `768e183`.
- **Release 6** (develop → main): #469 #419 #471 #463 #462 #468 #473 #472; no migration, no requirements.
  Not installed: waits for «نزّله».
- **Not done:** `codex/fix-b-461` (drawer negative refund, shift-close denominations, B3 legacy VAT) and
  `codex/fix-c-461` (Hikma reissue, refund day #146, update-rollback writes) were not opened by 11:30 Cairo;
  they go in the next release. #461 P1s not assigned to A/B/C: failed restore + archive pairing, mixed-tender
  guest refund. Owner decision: free-goods purchase invoice (#463 P2).
- **Next:** review + merge fix-b/fix-c when they open (next migration 0046; check numbering across both).

## Night 26/11 — CC-1 progress (newest line last)
- Claim posted on #113: PRs #458 #452 #418 #421 #422 #377 + rules/handoff docs; CC-2 owns the sale screen
  and #419, Codex the purchase-invoice screen.
- #460 merged `920d276`: one short rule set (`OWNER_RULES.md` 206 → 126 lines; old numbers §4 → §5,
  §12 → §7, §13 → §8), AGENTS/CLAUDE aligned, refusal cases → `DECISIONS.md` ADR-018, LAUNCH_PLAN /
  START_HERE / QUEUE / old HANDOFF log archived. Agent review 2 P1 fixed; Codex clean.
- #418 merged `faf3d9e` (U3-5 daily stocktake). Merge fix: English key for «هيتجرد».
- #377 merged `ebf98c7` (template DB per test): local suite ~935 s → ~513 s, linux CI ~16 → ~9 min.
- #458 merged `48d9150` (RCPT-1 receipt templates). Agent review no P0/P1; fixed: broad logo/QR fallback
  (P2, test-first), 11 missing English keys, old test → name printed above the intro line.
- #422 merged `aa96180` (U3-8 owner shift summary).
- #421 merged `44175b4` (U3-7 dhikr strip, hours reminder). Agent review caught a P1 in the develop merge
  (rounding route lost `@handler` → 500), fixed test-first; Codex P2 (fetch settles after navigation) fixed.
- #452 merged `89d66a7` (U4-3 part 1 E-Stock move: read + rehearsal + report). Codex P2 fixed test-first:
  a corrupt `state.json` is set aside instead of blocking the screen.
- `@codex review` asked on the #451 merge resolution (queue item 9).
- Every PR: develop merged in, full local suite (only the known local `httpx2` error), fresh-context agent
  review, Codex clean on the merged head, linux green. Windows runs on the release PR.
- Codex on #451 found a live P1 (untracked receipt without a receipt price sold at an older price); the
  review of the fix found a second path (net-price-only line with authority). Both fixed test-first: #466
  merged `dfcba76`. Windows red on the release from my #458 test (needs raqm) → #467 merged `c111afe`.
- **Release #465 (update 5) merged into main `4db40d4`**, linux + windows green on `c111afe`. No migration,
  no requirements, no installer. Not installed: waits for the owner's «نزّله». #419 (CC-2) not in it.
- Known intermittent test (not tonight's): `test_ux1_invoice_item_card` 5 s browser waits fail ~1 in 3
  locally on plain develop; for Codex (UX-1 owner).

## Current state (26/11)
- The program is the pharmacy's live cashier since 21/11. Release 6 (26/11, after #465 update 5) carries
  everything in `develop` up to #472; release 7 (26/11 evening) everything up to #485 (#486 if merged first).
  Updates reach the PC through the signed «تحديث» only after the owner says «نزّله».
- Migrations in `develop` run to 0045 (two files share 0045: harmless, the runner keys on the filename).
  **The next migration is 0046.**
- CI: linux on every labelled PR (`ci`), windows on PRs into `main` and Windows-relevant files.

## Queue (in order; tick with the evidence)
1. [x] PR #458 RCPT-1 thermal receipt templates → `48d9150`.
2. [x] PR #452 U4-3 part 1 E-Stock move button → `89d66a7`.
3. [x] PR #418 U3-5 stocktake while selling → `faf3d9e`.
4. [x] PR #421 U3-7 dhikr strip, work-hours reminder → `44175b4`.
5. [x] PR #422 U3-8 owner shift summary → `aa96180`.
6. [x] PR #377 test template database → `ebf98c7`.
7. [ ] PR #419 U3-6 customer medicines — CC-2 (sale screen).
8. [x] Release #465 (update 5) → main `4db40d4` (+ #466 money fix, #467 test fix).
9. [x] #451 merge resolution reviewed by Codex 26/11: one P1 → fixed in #466.
10. [ ] P2s left from tonight's reviews (small, after the release): #418 daily pick ranks by cost without
    `costs.read`, double daily session on a double-click; #421 staff timers keep running after a 401;
    #422 another branch's shift summary by id, «نقدي» line = cash sales only; #452 reader process survives a
    server restart (no kill/timeout), a new rehearsal deletes the previous report first; #458 «م» numbers
    price rows, not lines.
11. [ ] Then: U4-3 part 2 (the real move with the owner's password), U4-4 purchase entry additions,
    U4-19..U4-24 (details in the archived log, «Queue additions (owner 18:20)»).
After launch (label `after-launch`): #132, #102, #99, #83, #77.

## مستني المالك (does not stop the chain)
- «نزّله» for each release before the cashier PC presses «تحديث».
- The second Pharma Overseas paper (its lines are not in the repo) for the B3 test.
- On the PC: E-Stock `amount` on sale lines = packs? compare one item-month of the smart order (#431).
- Label print after the device-settings restore (B4): photo if one label still spans two.
- Odd piastres at shift close (#299): tolerance under 0.25 or leave as is.
- RCPT-1 receipt changes to confirm on the first printed receipt: the pharmacy name now prints above the
  intro line; on credit/delivery the customer's name, phone and address print even with "show customer" off;
  the phone prints on every sale with a customer.

## التذكيرات المفتوحة
- «نزّله» للتحديث 7 (فيه التحديث 5 و6 كمان): مستني كلمتك بعد ما يتدمج في النسخة الرسمية.
- بعد التحديث: تجربة تبديل لغة الكيبورد على جهاز هيكل، والبحث باسم مكتوب بلغة الكيبورد الغلط.
- قرار طريقة رد فاتورة مدفوعة بطريقتين (نقدي + فيزا): الاقتراح مكتوب في #487.
- قرار فاتورة البضاعة المجانية (مرفوضة دلوقتي).
- ورقة فارما أوفرسيز التانية لاختبار هامش الموزع.
- مقارنة صنف واحد في الطلب الذكي مع تقرير مبيعات إي ستوك.
- أول إيصال بعد التحديث: اسم الصيدلية بقى فوق سطر المقدمة، وبيانات العميل بتظهر في الآجل والتوصيل.
