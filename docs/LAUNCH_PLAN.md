# LAUNCH PLAN: who does what until the first release

Owner decision, 2026-10-30. Every agent (Claude, Codex, ChatGPT, any other) reads this file right after
`AGENTS.md` §1. It overrides any older plan or chat instruction about priorities or roles. Changing it
requires the owner's approval.

## 1. The single goal

**The program runs in the pharmacy as soon as possible.** All other work happens after launch.
The code is done: every gate is at L1 in `docs/STATUS.md`, and no code Issue labelled `launch-critical`
is open. What remains is **verification on the real pharmacy PC and the owner's sign-off**, not more code.

**Update, owner decision 2026-11-10:** the 2026-11-01 retest found small blockers, so a few code Issues are
open again. The current launch order and who does what is in `docs/START_HERE.md`:
#100 → #98 → #104 → #107 → #18 → #19 → #20. Everything else is labelled `after-launch`.

## 2. Critical path (the only work that moves the release)

| Step | Issue | Where | Who | Done when |
|---|---|---|---|---|
| 0 | #18 part A of `DEVICE_RUNBOOK.md` (G2–G6, no hardware) | Home laptop, any time | Local Claude session on the laptop | Results on #18. Takes load and risk (R8 rollback) off the pharmacy night |
| 1 | #18 device retest: 12 steps + R1–R10 | Pharmacy PC `DESKTOP-5JVH3QD`, after closing hours, backup first | Claude executes; owner does the physical steps (scan, look at the receipt, check the drawer, read the label) | Every line is marked pass or fail with evidence on #18. Each failure is its own `launch-critical` Issue |
| 2 | Fixes from step 1 (if any) | Cloud | Claude implements, Codex reviews | Merged into `develop`, CI green, new installer run, short retest of the failed lines only |
| 3 | #19 operating day (synthetic data) | Pharmacy PC | Owner follows `OWNER_TEST_PLAN.md` P1–P2; Claude guides and reconciles the numbers | Cash, stock, invoices and reports agree; owner signs off |
| 4 | #20 first release | Pharmacy PC, outside working hours | Owner approves; Claude runs it | `develop` SHA → `main`, tag, installer, verified USB backup. **E-Stock keeps running alongside (recommended: 1 week)** |

Estimate (not a promise): about 3 evenings at the pharmacy if step 1 finds no blocker.
Each failure found adds about 1 day.

## 3. Roles

| Role | Does | Never does |
|---|---|---|
| **Owner** | Physical steps at the pharmacy; approvals (`AGENTS.md` §5); final sign-off | Copies prompts between agents, debugs, runs commands an agent can run |
| **Claude Code: lead and executor** | Owns #18/#19/#20; is the only agent that runs commands on the pharmacy PC; implements fixes; keeps `docs/HANDOFF.md` and `docs/STATUS.md` current; reports to the owner (`AGENTS.md` §9) | Raises the percentage without evidence; merges to `main` without the owner's written go |
| **Codex: reviewer** | Reviews every PR (`@codex review`); takes a whole isolated code Issue only when Claude assigns it ("Owner: Codex") | Edits `AGENTS.md`, `docs/HANDOFF.md`, `docs/STATUS.md` or this file; starts its own process or automation work; works on the same files as an open PR |
| **ChatGPT: advisor, read-only** | Answers the owner's questions; may comment on Issues/PRs | Opens branches or PRs or edits repository files, unless the owner assigns a specific Issue in writing |
| **Home laptop `DESKTOP-0DCSIC2`** | A real Windows test device. Under the `STATUS.md` L2 definition it can supply L2 evidence for the gates with no hardware column (G2–G6), **once the owner confirms that it counts**. Never for G7/G8. Runs part A of `DEVICE_RUNBOOK.md` | Running unattended; touching E-Stock or Radmin VPN |

One accountable agent per Issue (`AGENTS.md` §10). If two agents could touch the same file, the lead decides.

## 4. Freeze until #20 is done

Allowed PRs: (a) ones that close or unblock #18, #19 or #20, including fixes for failures they find;
(b) fixes for red CI on `develop`; (c) updates to `HANDOFF.md`/`STATUS.md` that record evidence;
(d) **owner exception, 2026-10-30:** the written assessment of the independent-pharmacy federation (F20 in
`docs/FUTURE_REQUIREMENTS.md`) as `docs/FEDERATION_ASSESSMENT.md`. Docs only: no code, no migrations, no
change to app files. It must not take device-test time or touch files of an open #18/#19/#20 PR.
(e) **owner exception, 2026-11-03:** F27 receipt logo + QR codes + change due (#103, merged into `develop`
`d04daf2`), asked for directly by the owner ("ضيف كل ده"). No migration, no `requirements.txt` change. It
reaches the pharmacy only with #20; its device checks are in `docs/OWNER_TEST_PLAN.md` (priority 3, F27 table).
**Everything else waits** and goes to `docs/FUTURE_REQUIREMENTS.md` class C (`AGENTS.md` §6). That includes
#76 (agent auto-continuation), new process docs, refactors, new features and tooling.

## 5. Standing decisions

- Every agent uses the installer from `docs/HANDOFF.md` (the build installed on the pharmacy PC is listed there). A new installer is built
  only after a fix is merged and `develop` CI is green on both linux and windows.
- Red CI on `develop` is fixed before anything else. A rerun that bills Actions minutes needs the owner's approval
  (cost gate, #77).
- Every agent saves progress to `docs/HANDOFF.md` and the Issue after each meaningful step (`AGENTS.md` §7).
  Usage limits are real.

## 6. After launch (in this order)

1. One week running alongside E-Stock, then the owner decides when to switch.
2. #12 branch protection; #76 automation (only if it saves owner time and has a bounded cost).
3. `ROADMAP.md` phases 2–5 and `FUTURE_REQUIREMENTS.md`, prioritised by the owner.
