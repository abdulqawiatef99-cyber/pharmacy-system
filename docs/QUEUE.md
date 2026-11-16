# QUEUE.md — the work chain's queue (OWNER_RULES §10)

Take the first open item of your kind, finish it, tick it with the evidence (PR / issue comment), then start
or schedule the next session. Only the manager chat edits this file (workers write their log in the project).

## Open, in order
1. [ ] Night A (cloud worker, 17/11 02:30 Cairo): #253 scale test for large and huge pharmacies; results and
       any patch in the project (`claude/night-a-*`). Manager wake-up 04:30 posts the table on #253.
2. [x] Night B: #19 trial-day script refresh → #295 merged 17/11 02:00 (+ `docs/DAY19_OWNER_HANDS.md`).
3. [ ] Manager wake-up 12:30: morning report for the owner at the top of `docs/HANDOFF.md` (what the chain
       merged, what failed, what waits for him).
4. [ ] Day chat 17/11 (owner at the pharmacy, 13:00): review the chain's merges; «تحديث» on the cashier PC +
       stock check; Heikal PC: install, #253 small/medium run, start #19.
5. [ ] #160 (indexes, migration 0036): up to date with `develop` 17/11 (diff = the migration + 2 tests, CI green,
       Codex clean). Merge only with the bundled installer (owner OK).
6. [ ] #252: code side done (see its 17/11 comment); owner/device side with the owner in the day chat.
7. [ ] #20 preparation (after #19 passes): release PR by `docs/RELEASE_RUNBOOK.md` (merge -s ours origin/main).

## مستني المالك (does not stop the chain)
- OK to build the bundled installer (with #160).
- Product name; USB flash drive (before 18/11).
- Odd piastres at shift close (#299): tolerance under 0.25 or leave as is.
- Parallel-week method (WORK_PLAN §6 risk 1).

## Done
- [x] 17/11 #297 (look-alike search memo, Night A finding), #298 (search: exact name first, Night B O1); #274 closed; #299 opened (odd piastres at close, owner decision).
- [x] 16/11 22:47 cashier PC «تحديث» to `c627f32`, stock check all matching (#18).
- [x] 16/11 23:07 #18 leftovers: driver labels + scanner, reprint and F8 with the printer off (#18).
- [x] 16/11 #286 → #287, #274 → #288 merged; cashier PC on `69d87f8`.
- [x] 16/11 review R8-B: fixes as #289 #290, after-launch issues #291 #292 #293.
