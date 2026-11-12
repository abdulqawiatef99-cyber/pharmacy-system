# Performance with years of data (#113)

Synthetic data only. `scripts/perf/loadgen.py` builds a pharmacy through the program's own services
(purchases, sales, returns, voids, shifts, ledgers, expenses, transfer screenshots) on a simulated clock;
`scripts/perf/bench.py` copies a snapshot and measures backup, restore, start-up, memory, the cashier's hot
paths and the owner's reports over HTTP. Neither runs in CI (only a tiny generator run in
`tests/test_perf_loadgen.py`); the query plans are guarded by `tests/test_perf_queries.py`.

```
python3 scripts/perf/loadgen.py --out /tmp/perf --days 30 --quiet          # or --years 1 --snapshots 1
python3 scripts/perf/bench.py --data /tmp/perf/final/data --label d30 --json d30.json --repeat 3
```

Default shape: 4,000 items, 12 suppliers, 300 customers, 150–300 sales a day, 2% transfers with a ~60 KB
screenshot. Measured on a 2-core Linux cloud machine (Python 3.13), not the pharmacy PC: read the ratios,
not the absolute numbers.

## 30 days (2026-11-12): 6,758 sales, 19,236 lines, 89 MB database

Same snapshot, three versions of the code. `develop` = `22bda14`; "queries" = PR #159 (no migration);
"indexes" = PR #160 (#159 + `0036_perf_indexes.sql`). Median of 3, milliseconds unless marked.

| Request | develop | queries | indexes |
|---|---:|---:|---:|
| Invoices report (month) | 8,459 | 166 | 119 |
| Returns report (month) | 299 | 6.0 | 4.5 |
| Day report | 16.3 | 16.7 | 2.2 |
| Sales report (day) | 20.5 | 20.5 | 2.9 |
| Sales report (month) | 32.4 | 32.2 | 18.6 |
| Profit (month) | 22.6 | 23.2 | 12.9 |
| Shift report | 6.5 | 6.5 | 2.8 |
| Home screen | 13.5 | 11.2 | 8.8 |
| Receipt page | 6.0 | 5.9 | 1.6 |
| Return one unit | 4.0 | 4.0 | 1.9 |
| Complete sale | 31.7 | 31.9 | 26.6 |
| Add line (while the automatic backup runs), max | 22.9 | 19.0 | 13.4 |

The rows that read a whole table (`SCAN sale_allocations`, `SCAN sales`, the audit log) grow linearly with
the history, so on "develop" they keep growing with every month of sales; the invoices report
was one query per invoice and per line (~14,000 for a month), now a few batched queries.

Unchanged by these PRs (same in all three columns): backup 6.3 s (create) + 2.4 s (verify) for 30 days,
restore 5.0 s, start-up 0.2–0.4 s, server memory peak ~126 MB, the safety check before completing a sale
(median 26 ms, max ~247 ms), POS quick buttons 33 ms.

## 1 year (2026-11-12): 82,387 sales, 222,908 lines, 268,729 stock movements, 811 MB database

Same snapshot, three versions: develop `22bda14`, #159 at `97c9a68`, #160 at `a1031b5` (its six indexes).
Median of 2, milliseconds unless marked.

| Request | develop | queries (#159) | indexes (#160) |
|---|---:|---:|---:|
| Invoices report (month) | 86,999 | 616 | 119 |
| Day report | 182 | 183 | 2.3 |
| Sales report (day) | 218 | 219 | 3.0 |
| Sales report (month) | 235 | 230 | 19.8 |
| Profit (month) | 181 | 176 | 13.5 |
| Invoice list (day) | 22.0 | 25.4 | 2.9 |
| Shift report | 42.4 | 52.1 | 1.5 |
| Customer statement | 28.8 | 34.5 | 2.3 |
| Item card (top seller) | 130 | 124 | 54 |
| Home screen | 145 | 153 | 99 |
| POS quick buttons | 47.3 | 47.6 | 32.1 |
| Receipt page | 35.4 | 41.8 | 2.1 |
| Return one unit | 14.1 | 16.7 | 2.7 |
| Complete sale | 62.8 | 70.5 | 30.1 |
| Returns report (month) | 17.5 | 20.4 | 4.7 |

Backup and restore, the same in all three (they do not depend on these queries): backup create 137–147 s,
verify 112–115 s, restore 231–233 s, backup file 225 MB. **103 s of the verify is the stock check**
(`verify_balances`, a join of every pair): PR #166 makes it 0.5 s on the same file (measured 101.1 s → 0.5 s),
which removes most of that time from every hourly backup and every restore. Start-up 0.2–0.7 s, server
memory peak 314–332 MB. While the automatic backup ran, adding a line stayed at p95 6–15 ms (max 136–298 ms).

## Open findings (not fixed yet)
- **Database growth:** 89 MB after 30 days of synthetic data; the largest tables are `safety_events`
  (40,731 rows, 16.6 MB) and `audit_log` (16.1 MB). Extrapolated linearly (not measured), a year is about
  1 GB and the hourly backup (create + verify) about 100 s. 38,472 of the safety rows are
  `insufficient_data` / `shown` (the item has no clinical data), written for every line each time the
  basket is checked, about 6 rows a sale. The synthetic items have no clinical data at all; on the pharmacy
  PC most items will not either until the reference catalogue is linked, so the ratio is likely real.
  Tracked in its own issue (whether a "no data" notice must be logged on every check is a safety-log decision).
- **Safety check** grows with the history: median 26 ms at 30 days, 133–136 ms at one year (all three
  versions), checked twice per sale. Not looked into yet; next candidate after #166.
- **Year sales report** 616–622 ms, **stock report** 69–93 ms, **item movements for a year** 145–161 ms:
  acceptable, not changed by these PRs.
- 3-year snapshot: not measured yet (generating one year took about an hour here).
