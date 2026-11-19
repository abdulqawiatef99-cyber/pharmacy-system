# 09: Backup watch, database health and speed with years of data (19/11/2026)

> ملخص للمالك (بالفصحى): هذه الوثيقة تسجّل ما أُضيف ليلة 19/11 حتى لا يتوقف النسخ الاحتياطي دون علمك، وحتى يبقى
> البرنامج سريعًا وخفيفًا بعد سنوات من البيانات: مراقب مستقل للنسخ ينبّهك مرة يوميًا إذا توقّف، قياس للمساحة
> الفارغة قبل كل نسخة، تقارير كانت تأخذ دقائق فأصبحت أقل من ثانية، ونسخ احتياطي لا يعطّل الكاشير. لا شيء من
> دفاتر الصيدلية (الفواتير، حركات المخزون، سجل النشاط) يُحذف أبدًا.

Everything below was measured with `scripts/perf/scale.py` on synthetic data only, on one cloud machine
(4 cores, Linux). The pharmacy PCs are Windows: the figures are for comparison before and after, not promises.

## 1. Backup watchdog (`app/core/autobackup.py`)

Goal: the automatic backup can never stop silently.

| Piece | What it does |
|---|---|
| `health()` | One summary, cheap to poll (two small JSON files, no database query), safe for every signed-in user |
| `status(ctx)["health"]` | The same summary plus `last_error`, for `settings.manage` |
| watchdog thread `backup-watchdog` | Every minute: restarts a dead scheduler thread; while stale, one notification a day |
| reading `health()` | Also restarts dead threads: the screen's poll is a third watcher |
| disk check | Before every run; low = under 1 GiB free, or under twice (database + last backup) |
| audit log | `backup.failed`, `backup.recovered`, `backup.stale`, `backup.scheduler_restart`, `backup.low_disk` |

`health` fields (agreed with the screen's owner on #113; existing `state.*` and `settings.*` fields are unchanged):

| Field | Meaning |
|---|---|
| `ok` | false = show the badge |
| `level` | `ok`, `warning` (low disk only), `error` |
| `stale` | no good backup or "nothing changed" check for 2 x `interval_min`, or the scheduler dead or stuck |
| `last_good_at` | last backup file written |
| `last_fresh_at` | latest of `last_good_at` and the last "nothing changed" check after a good backup |
| `last_fail_at`, `last_error` | the last failed run (`last_error` only in `status`) |
| `age_min`, `limit_min` | minutes since `last_fresh_at`; the stale limit |
| `scheduler` | `running`, `dead`, `stuck` (one run longer than 45 minutes), `stopped` |
| `scheduler_restarts` | restarts since the program started |
| `low_disk`, `free_bytes` | from the last run |
| `reasons` | `failed`, `stale`, `never`, `scheduler_dead`, `scheduler_stuck`, `low_disk` |

Rules that must stay true:
- The watchdog never takes the backup lock. A run stuck on a dead USB drive cannot silence the alarm about itself.
- A healthy backup writes nothing to the audit log or the notifications.
- Records written by a run (warnings, audit rows) are not "changes" that force the next backup.
- Known limit: a run that hangs inside the operating system cannot be killed from Python. It is reported, not unstuck.

## 2. The cashier never waits for the backup (`app/core/backup.py`, `audit.py`, `responsive.py`)

Measured cause of the wait (medium, 1 GB database, each backup phase timed):

| Cause | Where | Fix |
|---|---|---|
| The temporary snapshot (a full copy) was flushed to disk in one go; a sale committed then waited about 1 s | `backup.create` → one-step copy | `backup.snapshot`: 8 MB steps, each flushed, under one read snapshot (so sales neither enter the copy nor restart it) |
| The backup checks the activity log's hash chain in pure Python; request threads queued behind it, up to 5 ms per database call | `audit.verify_chain` | `responsive.apply()`: the interpreter hands over after 0.5 ms instead of 5 ms |
| The whole activity log was read into memory for that check (about 100 MB of rows per year of a medium pharmacy) | `audit.verify_chain` | read in blocks of 2,000 rows |

Result, scanning while a backup runs, longest wait: see section 5.

Not done, and why: the stepped copy cannot be used inside a caller's own transaction (it falls back to one step);
and at 4.5 GB a backup takes minutes (section 5). Making the hourly backup incremental is a design decision for later.

## 3. Reports that asked one question per item (`app/reports/owner.py`, `app/procurement/service.py`)

No index was missing. The slow screens did not use the indexes that exist:

| Screen | Before | Now |
|---|---|---|
| «الأصناف اللي ما اتباعتش» | per item: sold in the period? last sold? last received? The last one without `org_id`, so the whole stock ledger was read once per unsold item | built from the period's invoices (short period) or item by item with an early stop (long period); the two dates only for the items listed |
| «مبيعات العملاء» | balance sub-query without `org_id`: the whole customer ledger once per customer | through `ix_customer_ledger (org_id, customer_id)` |
| Order suggestions, stock health | four queries per item | four queries in all, then arithmetic |

Still on the event loop in `app/api/app.py` (not changed here): the two procurement routes. They are short now but should go through `off_loop`.

## 4. Database health (`app/core/dbhealth.py`, `retention.py`, `app/safety/engine.py`)

| Topic | Decision |
|---|---|
| integrity_check | Already run on every backup's snapshot (with foreign keys, stock balances and the audit chain); a damaged database stops the backup and the owner is told. Pinned by a test. Nothing added |
| WAL checkpoint | Once a day: passive (blocks nobody). If the WAL file is over 64 MB (a long report held a snapshot while sales went on): one truncating checkpoint that waits at most 1 s for readers |
| VACUUM | On demand only: `python -m app.core.dbhealth vacuum`, program stopped. Refused without room for a second copy. The books are append-only, so free pages come only from cleaned notifications and sessions |
| ANALYZE | Not run. See section 6 |
| server log | `logs/server.log` and `logs/restore.log` trimmed in place to their newest 5 MB when over 20 MB (the launcher keeps them open for append and nothing shortened them before) |
| notifications | Deleted after 180 days (unchanged) |
| sessions | Expired or closed ones deleted after 30 days (unchanged) |
| audit log, safety log, stock movements, invoices | Never deleted. The first three also refuse DELETE by trigger. How long the law requires is the owner's question (#113) |
| safety log volume | The notice "no verified ingredient data for item X" was 95% of `safety_events` (440,696 of 464,792 rows in a year, medium) and a quarter of the database with its indexes. Now recorded the first time it is shown for an item in a month; still shown on every sale. Real warnings and every decision are recorded every time |

What takes the space after one year (medium, 81,812 sales, 936 MB of pages), tables with their indexes:

| Table | MB | Share |
|---|---|---|
| `safety_events` | 252 | 27% |
| `stock_movements` | 214 | 23% |
| `audit_log` | 137 | 15% |
| `sale_lines` | 82 | 9% |
| `sales` | 70 | 7% |
| `sale_allocations` | 55 | 6% |

## 5. Measurements

Before = `develop` @ `830a0ed`. After = the three branches of 19/11 merged locally (`claude/speed-reports`,
`claude/backup-smooth-copy`, `claude/sqlite-health`). Same snapshot, same machine. Limits: cashier under 300 ms,
report under 5 s.

Medium = 4,000 items, 81,812 sales in a year, 1.0 GB database. Large = 15,000 items, 364,664 sales, 4.5 GB.
Medians of the request time in ms, unless marked.

| Measure | Medium before | Medium after | Large before | Large after |
|---|---|---|---|---|
| «الأصناف اللي ما اتباعتش» (month) | 62,250 | 419 | no answer in 180 s | 1,904 |
| «الأصناف اللي ما اتباعتش» (all years) | 58 | 61 | 260 | 273 |
| Order suggestions | 787 | 295 | 3,770 | 1,439 |
| Stock health | 761 | 321 | 3,653 | 1,562 |
| «مبيعات العملاء» (month) | 19 | 8 | 285 | 29 |
| Scan while a backup runs, longest wait | 714 | 17 | 70 | 83 |
| Scan while an all-years report runs, longest wait | 180 | 55 | 634 | 54 |
| Scan while the year sales report runs, longest wait | 203 | 68 | 417 | 179 |
| Scan while order suggestions run, longest wait | not measured | not measured | 4,235 | not measured (see note) |
| Verdict of the run | 3 SLOW | 1 SLOW | INCOMPLETE (1 no answer, 10 slow) | 5 SLOW |

Notes, so nothing here reads better than it is:
- Large, "scan while a backup runs": 70 and 83 ms come from 98 and 131 scans (about 25-35 s), while a backup of that
  size takes minutes; the probe did not cover the whole backup. The medium figures (138 and 165 scans over the whole
  backup) are the reliable ones for this row.
- "Scan while order suggestions run" is probed only for the three slowest requests. Before, order suggestions were
  among them at large (the scan waited 4.2 s, the whole request, because the route runs on the event loop). After,
  they are no longer among the three slowest, so the probe did not run; the route is still on the event loop and a
  scan would still wait for its 1.4 s.
- Still over a limit after the change. Medium: the first safety check after a start or an item change (381 ms: the
  name index is rebuilt). Large: the same (1.6 s), the year and all-years sales report (7.7 s, 7.8 s), the transfers
  report over all years (8.8 s, `app/sales/attachments.py`), and the stock check button (25 s).
- A backup of the large database takes 220-270 s to create and 920 MB on disk; restore 133 s.
- Windows was not measured tonight. The same script on the test PC (`scripts/windows/heikal-tests.bat`) is the check.

## 6. ANALYZE: measured, not adopted

`retention.clean` has said since #249: no `PRAGMA optimize` / `ANALYZE`, because statistics gathered on a small
database went stale after a bulk load and made the planner read whole tables. Checked again on the medium
snapshot with the code of 19/11: every request was timed, then `ANALYZE` was run and every request was timed again
(`scale.py --indexes-sql <no index> --analyze`).

| | Without ANALYZE | With ANALYZE |
|---|---|---|
| Sum of the medians of all 58 requests | 13,226 ms | 13,465 ms |
| Requests more than 1.5 x faster (and by more than 20 ms) | 0 | |
| Requests more than 1.5 x slower (and by more than 20 ms) | 0 | |

No request gained from statistics, and the #249 risk stays (a pharmacy that imports years of history in one night
would run on the statistics of the day before). So: no ANALYZE, on a schedule or otherwise. The queries that need a
particular index say so in the SQL (`+branch_id`, `CROSS JOIN`, a materialized sub-query), with a test of the plan.
