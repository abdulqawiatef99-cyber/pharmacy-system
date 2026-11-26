# HANDOFF — current state and queue

Rules: `docs/OWNER_RULES.md` (wins), `AGENTS.md`. Gates and percentage: `docs/STATUS.md` (73.25%, no new
acceptance evidence). Log up to 23/11: `docs/archive/HANDOFF-2026-11.md`. Only the session that claimed this
file on #113 edits it; other sessions log on #113.

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

## Current state (26/11)
- The program is the pharmacy's live cashier since 21/11. `main` @ `e555df7` = `develop` @ `c893c18`
  (release #459, 24/11, after update 4b #455). Updates reach the PC through the signed «تحديث» only after
  the owner says «نزّله».
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
8. [ ] One combined release PR `develop` → `main` with everything merged tonight — CC-1.
9. [~] `@codex review` on the #451 merge resolution asked 26/11; act on any P0/P1 it finds.
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
- «نزّله» للتحديث الجاي بعد ما الإصدار المجمّع يتدمج.
- ورقة فارما أوفرسيز التانية لاختبار هامش الموزع.
- مقارنة صنف واحد في الطلب الذكي مع تقرير مبيعات إي ستوك.
