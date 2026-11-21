# Operating cycle HTTP acceptance — 2026-10-28

Branch: codex/operating-cycle-acceptance. Baseline main: 69e3c4a6c90b76c6f6c239bd8cfe98bfc2b884d0.
Owner: Codex. Only new tests and documentation; no business logic or database migration changed.

Existing test_day_consistency and test_cashier_acceptance already cover accounting scenarios. This new scenario fills the cross-module HTTP gap from imported purchase approval through shift close and reopening SQLite.

## Independent expected amounts (integer piastres)

- Synthetic purchase: 10 paid packs + 2 bonus packs, 2 units/pack; supplier debt 32000, stock 24. Both stay zero before approval.
- Float 10000. Cash sale: 3 units at 2000, 10% discount = 5400. Return one unit = 1800.
- Credit sale: 2 units = 4000. Collect 1500; customer debt 2500.
- Drawer: 10000 + 5400 - 1800 + 1500 = 15100; closing difference zero.
- Stock: 24 - 3 + 1 - 2 = 20, matching the movement ledger.
- Sales report: 2 completed sales, total 9400, returns 1800, discount 600.
- Close original client/DB, reopen a fresh connection/app: stock, reports, closed shift, SQLite integrity and audit chain remain valid.

The first authoring run was blocked by existing split-pack safety acknowledgment. The test was corrected to obtain findings and submit synthetic acknowledgments through the existing workflow; no medical rule was weakened. This does not validate licensed clinical content or approve real dispensing.

Targeted scenario: passed. Full baseline-plus-cycle suite: 375 passed, zero failures/skips, 37.166 seconds.
Log SHA256: 72211b805959af56682ae4fb116f345482eba09025e4012254b0da9456c9ea3c

## Limits and next step

No real invoice, E-Stock, patient, Windows installation, browser automation, printer or scanner/drawer hardware was used. HTTP TestClient exercises server APIs, not a human-operated GUI. Passing does not approve production or imply every financial route is retry-safe. Main lacks fixes in PR #2/#3/#4/#5; owner approval is required for merging.

Next: test this scenario against the isolated composition of saved fixes and refresh the shared handoff. Windows/hardware acceptance and owner GO/NO-GO remain external gates. Overall project completion remains unmeasured; no approved requirement denominator exists.

## Combined candidate verification
Local composition of PR #2, #3, #4, #5, four independent access tests and this cycle: 431 passed, zero failures/skips, 40.317 seconds. No Git merge or production operation.
Log SHA256: 1682cbb0c3f5040d2638ede83daf6d77650a3118b13e2519d80d9fe7593787f9
