# Performance with years of data (#113)

Synthetic data only. `scripts/perf/loadgen.py` builds a pharmacy through the program's own services
(purchases, sales, returns, voids, shifts, ledgers, expenses, transfer screenshots) on a simulated clock;
`scripts/perf/scale.py` does the same for one pharmacy size and measures every snapshot (#253, below);
`scripts/perf/bench.py` copies a snapshot and measures backup, restore, start-up, memory, the cashier's hot
paths and the owner's reports over HTTP. Neither runs in CI (only tiny runs in
`tests/test_perf_loadgen.py` and `tests/test_perf_scale.py`); the query plans are guarded by `tests/test_perf_queries.py`.

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
which removes most of that time from every hourly backup and every restore. Measured with #166 on the same
file: backup create 142 → 50 s (38 s of it is the xz compression), verify 115 → 9.9 s, restore 231 → 21 s.
During that shorter backup, adding a line had p95 105 ms (max 341 ms): the compression keeps one core busy. Start-up 0.2–0.7 s, server
memory peak 314–332 MB. While the automatic backup ran, adding a line stayed at p95 6–15 ms (max 136–298 ms).

## Large and huge pharmacies (#253)

`scripts/perf/scale.py` builds one size from an empty database (sizes in `real_years.PROFILES`: large = 800-1200
sales a day and 15,000 items, huge = 2500-3500 a day and 30,000 items), runs the owner's stock check on every
snapshot, then measures what `real_years.py` measures against the owner's limits (cashier < 0.3 s, report < 5 s,
nothing blocks the cashier). Several sizes can run at once, one process a core; with `--turns` only one measures
at a time and the others wait.

```
python3 scripts/perf/scale.py --profile large --years 1,3 --out-txt /tmp/scale-large.txt --turns /tmp/turns
python3 scripts/perf/scale.py --profile huge  --years 1   --out-txt /tmp/scale-huge.txt  --turns /tmp/turns
```

Generator speed: with 15,000 items the service-level generator made 16.5 sales a second. 90% of its time was the
catalog's fuzzy name matching (`matching.fuzzy_candidates`): the look-alike notice of the safety check, for every
basket line, twice a sale, plus every purchase line; each call scores the name against every item that shares a
3-letter prefix and rebuilds the name index when any item row changed. `loadgen.install_fast_matching` remembers the
program's own answer per name while generating (the catalog is fixed then); `tests/test_perf_loadgen.py` compares
the data with and without it. The program is unchanged, so the measured server still pays that cost: see the
safety-check rows of the results. Disk: about 10 KB a sale; a backup needs about 1.7 times the database in free
disk on top of it (copy, compressed copy, check), and `scale.py` skips the backup timings when that is missing.

### Results 2026-11-17 (`develop` @ `2a47dcc`, 2-core cloud machine, 8 GB RAM)

Full table (58 screens and reports, cashier flow, purchase invoice, blocking probes) and the suggested fix for every
FAIL: the Night A comment on issue #253. Stock check after generation: all balances match at every size.
"+ indexes" = the same database with #160's `0036_perf_indexes.sql`. Max of 3, milliseconds unless marked.

| | large 3 years | + indexes | huge 1 year | + indexes |
|---|---:|---:|---:|---:|
| sales / stock movements | 1.09 M / 3.5 M | | 1.10 M / 3.4 M | |
| database | 11.8 GB | | 11.4 GB | |
| Add line | 8 | 26 | 6 | 8 |
| Safety check (median / max) | 48 / 980 | 52 / 998 | 177 / 1,951 | 174 / 1,931 |
| Complete sale | 825 | 56 | 807 | 188 |
| Receipt page | 582 | 3 | 597 | 2 |
| Search by name / barcode | 9 / 2 | 28 / 2 | 27 / 2 | 16 / 3 |
| POS quick buttons | 1,397 | 297 | 5,791 | 1,106 |
| Day report | 11.1 s | 2 | 3.9 s | 2 |
| Sales report (month) | 7.8 s | 279 | 11.6 s | 1.1 s |
| Sales report (year) | 11.0 s | 13.4 s | 26.4 s | 39.9 s |
| Invoices report (month) | 11.1 s | 177 | 9.7 s | 193 |
| Stock-alert banner (asked once a minute) | 4.7 s | 972 | 13.3 s | 13.4 s |
| Purchase invoice, approve 20 lines | 6.5 s | 6.2 s | 7.3 s | 6.7 s |
| Cashier's scan while it is approved | 6.5 s | 6.1 s | 7.3 s | 6.6 s |
| Cashier's scan while a long report runs (median / max) | 7 / 11 | 3 / 51 | 6 / 14 | 8 / 14 |
| Unsold items (month) | no answer in 180 s | same | same | same |

Large 1 year (364,000 sales, 3.99 GB, fits in memory): complete sale 266 (92 with the indexes), receipt page 183 (2),
day report 908 (8), purchase approval 2.0 s with the cashier's scan waiting 1.9 s. Backup: create 181 s, check after
writing 27 s, verify 48 s, restore 103 s, file 0.91 GB; the cashier's scan while it ran: median 6, max 180.
The 11-12 GB databases were not backed up: the machine's 30 GB disk cannot hold it.

What the numbers say:
- Without #160's indexes the cashier's own steps fail the 0.3 s limit at these sizes; with them they pass.
- `ix_sale_lines_item ON sale_lines(item_id)` as #160 has it makes `Sales.add_line` read every line the item ever
  had (156 ms for the best seller of large 3 years); it must be `(item_id, sale_id)`.
- Requests that run on the server's event loop hold every screen: purchase approval (its last-purchase-cost query has
  no `branch_id` and sorts every stock movement of the pharmacy), the stock-alert banner (reads the whole audit log
  when there never was a mismatch), the audit log screen, order suggestions, stock health, the quick buttons.
- Reports that run in a worker thread (#249) keep the cashier's median scan at 4-8 ms. While the year sales report
  runs, single scans waited 0.3-0.6 s (it adds its rows up in Python); other long reports and the backup did not.
- After one year of a large pharmacy the cashier still passes without the indexes, just (266 against 300); after
  three years it does not.

## Open findings (not fixed yet)
- **Database growth:** 89 MB after 30 days of synthetic data; the largest tables are `safety_events`
  (40,731 rows, 16.6 MB) and `audit_log` (16.1 MB). Extrapolated linearly (not measured), a year is about
  1 GB and the hourly backup (create + verify) about 100 s. 38,472 of the safety rows are
  `insufficient_data` / `shown` (the item has no clinical data), written for every line each time the
  basket is checked, about 6 rows a sale. The synthetic items have no clinical data at all; on the pharmacy
  PC most items will not either until the reference catalogue is linked, so the ratio is likely real.
  Tracked in its own issue (whether a "no data" notice must be logged on every check is a safety-log decision).
- **Safety check:** not a history problem. The first check after the program starts builds the catalogue
  match index (~220 ms for 4,000 items on the 1-year file), later checks take ~3 ms (profiled on the same
  file). The bench's median of 2 runs (133–136 ms) includes that first build; at 30 days it ran 3 times.
- **Year sales report** 616–622 ms, **stock report** 69–93 ms, **item movements for a year** 145–161 ms:
  acceptable, not changed by these PRs.
- 3-year snapshot of the default (medium) shape: not measured; large 3 years is above.

## U4-5 checkout (2026-11-23)

Loopback HTTP on Linux with the pinned Python 3.13 dependencies, 30,000 synthetic catalog items,
an adjacent archive with 500,000 synthetic detail rows, and a two-line cash basket. The catalog
deliberately shares a common prefix to stress fuzzy queries. Base is develop at `78c3646`. Timings
are single controlled observations in milliseconds.

| Request | Base | U4-5 |
|---|---:|---:|
| Safety check, cold catalog | 9,072 | 64 |
| Complete after check, device idle | 50 | 37 |
| Check plus complete | 9,122 | 101 |
| Complete with a simulated 1.5 s device operation | 1,542 | 24 |
| First fuzzy search after the index is warm | 5,568 | 1,017 |
| Repeat fuzzy search | 108 | 92 |
| Cold fuzzy search before startup warmup can finish | 11,114 | 6,274 |
| Backup health, stock alert and notifications during a forced 2 s SQLite writer hold | 2,013 each | 24–26 |

Profiling traced the cold check to building the fuzzy catalog index and scoring roughly 30,000 names
to find informational look-alikes. Checkout now compares same-core candidates and still runs clinical
rules and warning resolution. The archive was outside the checkout path in this reproduction.
The server starts warming the fuzzy catalog index in a worker. Item search, checkout and HTTP
idempotency also use workers. The three measured polls returned while the write was still waiting.
The forced lock reproduces the stall mechanism. The trigger for the live 11 s incident is unconfirmed.

The cashier waits for the sale commit and can begin the next sale while the device worker issues the
drawer command first and prints the receipt. Older API callers can keep synchronous device results.
The offline status switch used by tests leaves explicit updater network tests on their mocked path.

Reproduce the timed checkout and worker assertions with
`python -m unittest tests.test_u4_5_checkout`. The cashier's Windows device timing remains to be measured.
