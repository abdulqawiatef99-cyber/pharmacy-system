# HANDOFF — technical state and queue for coding agents

No rules here: every rule is in `docs/OWNER_RULES.md`. The manager's handoff to the owner is the project
file `claude/handoff.md`. This file is overwritten, never appended as a log (old logs live only in git history).

## State (28/11/2026)
- `main` @ `9565adb` = release 10 (#511: #509 supplier discounts in the order plan, #510 sale screen,
  QA-31 fixes). Installed on the cashier PC 28/11 by smart update (backup taken first).
- `develop` @ `12629e3` = release 10 + #512 (shortages: public price, last purchase discount and
  pharmacist price, plan `timings_ms`). Not yet in a release.
- Last migration: `0052_mixed_refunds.sql`. The next migration takes the next free number.
- The program is the pharmacy's live cashier since 21/11. The Heikal PC becomes the main PC on move day
  (steps: project file `claude/heikal-move-coordination.md`).

## Queue
1. Instinct patches waiting on WhatsApp (cheques 3+1, newest 547b0765; controlled-drugs register 3;
   units 3): download, verify sha256, apply on a PR branch, review, Codex, merge.
2. `test_filters` in `tests/test_invoices_report.py` fails intermittently (3 of 12 runs per Instinct): find
   the cause and fix; never skip it.
3. Next release `develop` → `main` (#512 + the above), then smart update on the cashier PC and measure the
   first open of the order screen.

## Open with the owner (each has a recommendation; work proceeds on it)
- Mixed-tender refund (cash + card): proposal in #487.
- Free-goods purchase invoice: refused today.
- Second Pharma Overseas paper for the distributor-margin test.
- Compare one item-month of the smart order with the E-Stock sales report (#431).
- Odd piastres at shift close (#299): tolerance under 0.25 or leave as is.
