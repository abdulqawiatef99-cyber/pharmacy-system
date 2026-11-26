# HANDOFF — current state and queue

Rules: `docs/OWNER_RULES.md` (wins), `AGENTS.md`. Gates and percentage: `docs/STATUS.md` (73.25%, no new
acceptance evidence). Log up to 23/11: `docs/archive/HANDOFF-2026-11.md`. Only the session that claimed this
file on #113 edits it; other sessions log on #113.

## Night 26/11 — CC-1 progress (newest line last)
- Claim posted on #113: PRs #458 #452 #418 #421 #422 #377 + rules/handoff docs; CC-2 owns the sale screen
  and #419, Codex the purchase-invoice screen.
- Rules consolidated (this PR): `OWNER_RULES.md` 206 → 120 lines, `AGENTS.md`/`CLAUDE.md` aligned; the
  refusal case list moved to `DECISIONS.md` ADR-018; `LAUNCH_PLAN.md`, `START_HERE.md`, `QUEUE.md` and the
  old HANDOFF log archived.

## Current state (26/11)
- The program is the pharmacy's live cashier since 21/11. `main` @ `e555df7` = `develop` @ `c893c18`
  (release #459, 24/11, after update 4b #455). Updates reach the PC through the signed «تحديث» only after
  the owner says «نزّله».
- Migrations in `develop` run to 0045 (two files share 0045: harmless, the runner keys on the filename).
  **The next migration is 0046.**
- CI: linux on every labelled PR (`ci`), windows on PRs into `main` and Windows-relevant files.

## Queue (in order; tick with the evidence)
1. [ ] PR #458 RCPT-1 thermal receipt templates (Codex draft) — CC-1.
2. [ ] PR #452 U4-3 part 1 E-Stock move button — CC-1.
3. [ ] PR #418 U3-5 stocktake while selling — CC-1.
4. [ ] PR #421 U3-7 dhikr strip, work-hours reminder — CC-1.
5. [ ] PR #422 U3-8 owner shift summary — CC-1.
6. [ ] PR #377 test template database — CC-1.
7. [ ] PR #419 U3-6 customer medicines — CC-2 (sale screen).
8. [ ] One combined release PR `develop` → `main` with everything merged tonight — CC-1.
9. [ ] Ask `@codex review` on the #451 merge resolution (never reviewed: Codex was at its limit 23/11).
10. [ ] Then: U4-3 part 2 (the real move with the owner's password), U4-4 purchase entry additions,
    U4-19..U4-24 (details in the archived log, «Queue additions (owner 18:20)»).
After launch (label `after-launch`): #132, #102, #99, #83, #77.

## مستني المالك (does not stop the chain)
- «نزّله» for each release before the cashier PC presses «تحديث».
- The second Pharma Overseas paper (its lines are not in the repo) for the B3 test.
- On the PC: E-Stock `amount` on sale lines = packs? compare one item-month of the smart order (#431).
- Label print after the device-settings restore (B4): photo if one label still spans two.
- Odd piastres at shift close (#299): tolerance under 0.25 or leave as is.

## التذكيرات المفتوحة
- «نزّله» للتحديث الجاي بعد ما الإصدار المجمّع يتدمج.
- ورقة فارما أوفرسيز التانية لاختبار هامش الموزع.
- مقارنة صنف واحد في الطلب الذكي مع تقرير مبيعات إي ستوك.
