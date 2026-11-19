# Installer candidate for launch night (Friday 20/11/2026)

The cashier PC runs develop `099ae31`, which is schema **0036**. The in-app update refuses migrations, so everything below reaches the PC through **one installer** built from the candidate. Synthetic data only, everywhere in this file.

## 1. What the candidate is

| Part | PR | Head | Migration | State |
|---|---|---|---|---|
| develop | — | `f5a5d31` (19/11) | up to 0036 | merged |
| Contract tickets + E-Stock tickets, one claim across the move | #340 | `0d3b4a3` | **0037** `sale_ticket` | open, installer only |
| Money accounts, supplier early-payment discount, reconciliation (services) | #342 | `b2d4a75` | **0038** `money_accounts` | open, installer only |
| Money accounts phase 2: shift close into an account, payouts, screens | #348 | `a17f1c8` | (0038, same file) | open; **conflicts with develop** in `app/api/app.py`, `app/sales/cash.py`, `app/web/app.js`, `app/web/i18n-en.js` (money session to resolve, #113) |
| Impossible E-Stock history rows quarantined | #380 | `da19e69` | none | open; merges into develop under the rule |
| E-Stock move on the candidate: money places as accounts, store 2 as hospital-owned batches, sale guard | #381 | `c9e46ba` | **0039** `batch_owner` | open, installer only |

**Migration order:** 0036 (develop) → 0037 (#340) → 0038 (#342) → 0039 (#381). No number is shared. Each runs once, with a checksum. 0038 needs foreign keys off (its header says so). The installer's start takes a verified backup tagged `pre-migrate` before any migration (`manage.py _db`).

**Build from:** develop + #340 + #342 + #381 (+ #348 once it merges clean with develop). The local integration branch `claude/launch-candidate` is develop + #340 + #342, and #381 sits on top of it.

## 2. Checks done (19/11, development machine)

| Check | Result |
|---|---|
| Merge develop + #340 + #342 | clean, no conflicts |
| Merge #348 into develop | **4 conflicts** (above), reported on #113 |
| Full suite, develop + #340 + #342 | 2165 tests OK (skipped 10) |
| Full suite, candidate + #381 | 2178 tests OK (skipped 10) |
| Full suite, #380 | 2093 tests OK (skipped 10) |
| New DB migrates in order | 0001 … 0039, none unknown |
| Upgrade test (`tests/test_upgrade_0036_to_candidate.py`) | a DB built with only migrations up to 0036, after a whole E-Stock move, upgrades with ledgers, stock, the stock check, the audit chain and the archive unchanged, then sells |
| **Full-size manual upgrade** | develop's own code built a 0036 DB: 30,000 items, the whole synthetic E-Stock move (RECONCILIATION OK), 40 sales, a return, a closed shift. Then the candidate's `manage.py verify` took a `pre-migrate` backup, applied 0037/0038/0039 and ended **OK in 16 s**. Before/after snapshot **identical**: customer and supplier ledgers, 13,638 stock rows, the ledger-vs-balance check, cash, sales, counts and the archive sha |
| Full-size bench on the candidate (`python -m app.dataimport.estock_synthetic --bench`) | dry run 22 s, apply 21 s, peak 355 MB, RECONCILIATION OK; **9 money accounts with openings, 362 hospital-owned batches** |
| Corrupt-looking read values (cashier PC 15:34: `sales_id -7.4e18`, `amount -1.7e212`, `exp_date 8009`, …) | quarantined in the archive (`q_<table>`), never money; one owner-report line per table (#380) |

**Known, not blocking, owners told on #113:**
- Store 2's hospital-owned batches are kept out of every normal sale. But 15 other stock readers do not filter by owner yet: item card, reorder, counts, stock-value reports and others. Until they do, hospital units can show in those screens' quantities.
- #348 (shift close into an account) is not in the candidate until it merges clean.

## 3. Heikal PC test checklist (before the pharmacy PC)

Use the home laptop with **fake data only**. Write PASS or FAIL with the time on #18 for each line.

1. **Install over a 0036 copy with fake data.**
   - On a develop install (the version the cashier PC runs now), make a pharmacy and put the synthetic E-Stock read in. From `%LOCALAPPDATA%\Programs\PharmacyPlatform\program`, run `..\python\python.exe -m app.dataimport.estock_synthetic --write %LOCALAPPDATA%\PharmacyPlatform\estock-db-read\20261119-000000`.
   - Run `estock-db-import.bat` and type YES. Sell 3 items, return 1, close the shift.
   - Run the candidate installer over it.
   - Expect: a `pre-migrate` backup in the backups folder, migrations 0037/0038/0039 applied, `manage.py verify` OK, the same item count, and the same customer and supplier balances (3 checked by hand), the same stock for 3 items.
2. **The update button.** In the program, «تحديث»: it must say the update needs the installer (migrations) and change nothing.
3. **Import rehearsal.**
   - On the upgraded laptop, which holds the trial data, double-click «أدوات الصيدلية» ← `estock-db-import.bat`.
   - Expect: REHEARSAL on a new empty database (exit 4 path), `owner-report.txt` opens, no YES asked, the program starts again, the real database unchanged.
4. **The real import on an empty database.** `launch-fresh-start.bat`, then `estock-db-import.bat`, then YES. Expect:
   - RECONCILIATION OK;
   - «الحسابات» shows the E-Stock money places with their openings (treasury, bank, wallets; the stopped one stopped);
   - store 2's items are hospital-owned: a normal sale of a Testaclav-like item never takes them;
   - the owner report says the money accounts moved.
5. **Shift close into an account (needs #348).** Open a shift, sell for cash, close with the counted cash into the treasury. Expect: the treasury balance rises by the counted cash; the closed shift appears once.
6. **Backup, restore and rollback.**
   - «اعمل نسخة الآن», then restore it into a new folder (`heikal-tests.bat` restore drill). Expect: verify OK, the archive restored next to the database.
   - Rollback: reinstall the previous installer over a copy. It must refuse the newer schema (`newer schema refused`), not run on it.

## 4. Launch night order

1. Close E-Stock's shift.
2. Close E-Stock.
3. Run the final `estock-db-read.bat` (E-Stock closed).
4. Install the candidate.
5. Run `launch-fresh-start.bat`.
6. Run `estock-db-import.bat`, read `owner-report.txt`, type YES.
7. Take an off-device backup.

Details: `docs/RELEASE_RUNBOOK.md` §10.
