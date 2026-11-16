# QUEUE.md — the work chain's queue (OWNER_RULES §10)

Take the first open item of your kind, finish it, tick it with the evidence (PR / issue comment), then start
or schedule the next session. Only the manager chat edits this file (workers write their log in the project).

## Open, in order
1. [ ] Night A (cloud worker, 17/11 02:30 Cairo): #253 scale test for large and huge pharmacies; results and
       any patch in the project (`claude/night-a-*`). Manager wake-up 04:30 posts the table on #253.
2. [ ] Night B (cloud worker, 17/11 07:00 Cairo): refresh the #19 trial-day script against today's `develop`
       (patch in the project, `claude/night-b-*`). Manager wake-up 08:30 applies it as a PR and merges it.
3. [ ] Manager wake-up 12:30: morning report for the owner at the top of `docs/HANDOFF.md` (what the chain
       merged, what failed, what waits for him).
4. [ ] Day chat 17/11 (owner at the pharmacy, 13:00): review the chain's merges; «تحديث» on the cashier PC +
       stock check; Heikal PC: install, #253 small/medium run, start #19.
5. [ ] #160 (indexes, migration 0036): 5 days stale, 14 files, overlaps `scripts/perf/`. Bring up to date with
       `develop` after Night A's result is merged, strongest model, then the bundled installer (owner OK).
6. [ ] #252 security review leftovers; #132 #99 after launch.

## مستني المالك (does not stop the chain)
- OK to build the bundled installer (with #160).
- Product name; USB flash drive (before 18/11).
- Parallel-week method (WORK_PLAN §6 risk 1).

## Done
- [x] 16/11 22:47 cashier PC «تحديث» to `c627f32`, stock check all matching (#18).
- [x] 16/11 23:07 #18 leftovers: driver labels + scanner, reprint and F8 with the printer off (#18).
- [x] 16/11 #286 → #287, #274 → #288 merged; cashier PC on `69d87f8`.
- [x] 16/11 review R8-B: fixes as #289 #290, after-launch issues #291 #292 #293.
