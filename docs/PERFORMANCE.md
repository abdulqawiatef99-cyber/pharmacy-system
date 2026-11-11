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

## Open findings (not fixed yet)
- **Database growth:** 89 MB after 30 days of synthetic data; the largest tables are `safety_events`
  (40,731 rows, 16.6 MB) and `audit_log` (16.1 MB). Extrapolated linearly (not measured), a year is about
  1 GB and the hourly backup (create + verify) about 100 s. 38,472 of the safety rows are
  `insufficient_data` / `shown` (the item has no clinical data), written for every line each time the
  basket is checked, about 6 rows a sale. The synthetic items have no clinical data at all; on the pharmacy
  PC most items will not either until the reference catalogue is linked, so the ratio is likely real.
  Tracked in its own issue (whether a "no data" notice must be logged on every check is a safety-log decision).
- **Safety check** max ~250 ms on the first call of a basket; fine for one cashier, worth a look if it
  grows with the history.
- 1-year and 3-year snapshots: not measured yet (a year takes about an hour to generate here).
