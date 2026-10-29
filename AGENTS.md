# AGENTS.md — shared rules for every coding agent (Claude Code accounts, Codex, others)

This file is the permanent memory of the project's working rules. Chat history is not.
If something here conflicts with an older document, this file wins.

## 1. Start of every session (in this order, nothing else first)
1. `git fetch origin` and read `docs/STATUS.md` (release gates, progress) and `docs/HANDOFF.md` (current work).
2. List open PRs and open Issues labelled `launch-critical`. Do not trust counts or SHAs in old reports.
3. Pick the highest-priority open Issue whose dependencies are done and that nobody is assigned to.
   Claim it (assign yourself or comment `claimed by <agent>`), then start. One task at a time.

## 2. Branches and merging
- `main` = the pharmacy's update channel. The in-app updater installs whatever is on `main`, and a
  push to `main` publishes `installer-latest`. **Nobody merges into `main` without the owner's explicit approval.**
- `develop` = integration branch. All work goes to `develop` through PRs.
- Work branches: `claude/<issue>-<slug>` or `codex/<issue>-<slug>` from the latest `develop`
  (a session may be given a fixed branch name; use it). Never push to another agent's branch.
- Merge into `develop` only when: CI is green, the diff was re-read, the Issue's acceptance criteria
  are met, and there are no conflicts. Merge commits, no force-push on shared branches.
- Never close or overwrite another agent's open PR without checking its content is preserved elsewhere.

## 2a. Review loop (Claude Code implements, Codex reviews)
1. The implementing agent opens a PR into `develop` (template: `.github/pull_request_template.md`)
   and comments `@codex review` on it.
2. Codex reviews on GitHub. Its findings are the record; nothing is passed through chat.
3. The implementing agent fixes every P0/P1 finding, or replies on the thread with the reason it does
   not apply, then pushes. CI must be green on the final head.
4. Merge into `develop` (rules in §2), close the Issue with the evidence, update `docs/HANDOFF.md`,
   take the next Issue (§1).
If Codex does not answer within a working day, merge on green CI plus your own re-read, and note
"no Codex review" in the PR. Codex taking an implementation task works the same way with the
roles swapped: Claude reviews.

## Code Review Rules
(Read by Codex code review. Flag these as P0/P1; ignore style.)
- Money must stay integer piastres and quantities integer base units; any float, truncation or
  rounding other than `discount_of` (half up) is a defect.
- Stock ledger and audit log are append-only; writes happen inside `db.transaction()` together with
  their audit record.
- Every query touching pharmacy data filters by `org_id`; every service checks `ctx.require(...)`.
- Money-moving POSTs must not post twice on a retry (Idempotency-Key path in `app/api/app.py`).
- No change to `app/migrations/*.sql` files that are already applied; no edit to `requirements.txt`
  without saying it ships only through the installer.
- No secrets, real pharmacy, patient or customer data in code, tests or fixtures.
- A behaviour change without a test that fails before it is a defect.

## 3. Definition of done (per task)
Code + targeted tests + full suite (`python -m unittest discover -s tests -t .`) green + docs touched by
the change + PR merged into `develop` + `docs/HANDOFF.md` updated + Issue closed with the evidence
(commit SHA, test counts). "Implemented" is not "verified on Windows" or "accepted by the owner";
record each level separately in `docs/STATUS.md`.

## 4. Hard rules
- Money is integer piastres, stock is integer units, the stock ledger and audit log are append-only,
  every query is filtered by `org_id`. AI reads and suggests; it never decides money, stock or doses.
  (Details: `docs/PROJECT.md` §2, `docs/DECISIONS.md`.)
- Never edit an applied migration; add a new one. Note: the protocol-1 in-app updater refuses releases
  that change `requirements.txt` or migrations; those ship through the installer (`docs/UPDATE.md`).
- No real pharmacy, patient or customer data and no secrets in code, tests, commits, logs or chat.
  CI runs `scripts/check_secrets.py` on every PR.
- Never skip, disable or weaken a test to get green. Never invent test results or percentages.

## 5. Needs the owner's explicit approval
Merge to `main` / any release or deployment; touching real data or migrating it; new recurring costs or
paid APIs; irreversible operations; changes to agreed business rules (pricing, discounts, returns,
cost, dosing). Everything else: decide, document the decision (`docs/DECISIONS.md` if architectural), continue.

## 6. New ideas
Do not interrupt the active task. Add or update an entry in `docs/FUTURE_REQUIREMENTS.md`
(description, value, modules, design impact). Future features are outside the first-release percentage.

## 7. End of every task or session (also before hitting a usage limit)
Save as you go (owner's rule): the owner's usage limit can end a session without warning, so after every
meaningful step (a finding, a decision, a test result, a device fact) commit and push it to `docs/HANDOFF.md`
on your branch right away; do not wait for the end of the task or for the owner to read your messages.
Commit verified work → push → update `docs/HANDOFF.md` (task, branch, last SHA, tests run and results,
what is unfinished, exact next step) → update the Issue. Never claim work will resume automatically
unless a real scheduler was configured.

## 8. Efficiency
- Read targeted files; do not re-scan the whole repository or re-run passing suites without a change.
  Targeted tests while implementing; the full suite once before each push that goes to a PR.
- Small, independently verifiable tasks; one Issue/branch/PR each. A fresh session for an independent task
  when practical; save `HANDOFF.md` before context is cleared. Never depend on chat history.
- Subagents, new tools, libraries or services only when they clearly cut real work; assess architecture fit,
  security, licence, cost and test effort first. Paid services or new orchestration need the owner (§5).
  Non-essential tooling waits until after launch.
- Efficiency claims need evidence (rework avoided, CI failures, tasks verified). Never invent token or time savings.
- Scheduling/queued continuation only where actually configured; never claim it otherwise (§7).

## 9. Reports to the owner
Short Egyptian Arabic, facts separated from assumptions, details stay in GitHub. After each milestone:
- **الإنجاز** — what was actually completed. **الدليل** — PR/commit, test counts.
- **نسبة الإطلاق** — X% by the `STATUS.md` formula, change since the last milestone (0 if no evidence level
  changed), remaining 100−X%; code-complete gates kept separate from Windows, hardware and owner acceptance.
  Never raise the percentage because more code was written; weights, levels and criteria change only with the owner.
- **الكفاءة** — only a verified improvement. **العوائق** — important blockers, ranked. **التالي** — next task.
