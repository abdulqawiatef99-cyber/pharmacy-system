# AGENTS.md — shared rules for every coding agent (Claude Code accounts, Codex, others)

This file is the permanent memory of the project's working rules. Chat history is not.
If something here conflicts with an older document, this file wins (except `docs/OWNER_RULES.md`, which wins over it).

## 1. Start of every session (in this order, nothing else first)
1. `git fetch origin` and read `docs/OWNER_RULES.md` (the owner's standing rules; they win over this file
   where they overlap), then `docs/STATUS.md` (gates, percentage) and `docs/HANDOFF.md` (current state, queue),
   and the last comments on #113 (file claims).
2. List open PRs and the open Issues of the queue. Do not trust counts or SHAs in old reports.
3. Take the first open queue item (or the task you were given) that nobody has claimed. Claim its files on
   #113 (OWNER_RULES §6), then start. One task at a time.

## 2. Branches and merging
- `main` = the pharmacy's update channel. The in-app updater installs whatever is on `main`. Only a
  release PR `develop` → `main` merges into it, on the gates of OWNER_RULES §5 (owner pre-approved 22/11).
  Installing on the pharmacy PC waits for the owner's «نزّله».
- `develop` = integration branch. All work goes to `develop` through PRs.
- Work branches: `claude/<issue>-<slug>` or `codex/<issue>-<slug>` from the latest `develop`
  (a session may be given a fixed branch name; use it). Never push to another agent's branch.
- Merge into `develop` only on the gates of OWNER_RULES §5 (local full suite on the merge with `develop`,
  independent review, `CI / linux` green on the latest head, no unresolved Codex P0/P1), with the
  acceptance criteria met and no conflicts. Merge commits, no force-push on shared branches.
- Never close or overwrite another agent's open PR without checking its content is preserved elsewhere.
- GitHub Free has no branch protection for this repo, so `.github/workflows/branch-guard.yml`
  stands in for it: `main` and `develop` change only by merging a PR whose `CI / linux` is green, never by
  direct or force push. A violation opens an Issue (force push: old tip kept as `guard/<branch>-<sha>`),
  and an unverified push to `main` does not build or publish `installer-latest`. Limit: a force push back
  to a commit older than the guard runs no guard (GitHub uses the pushed commit's workflows); only real
  branch protection or an outside watcher covers it, which is the owner's decision (#12).

## 2a. Review loop (Claude Code implements, Codex reviews)
0. Before any `@codex review` (owner 21/11, OWNER_RULES §7): an independent review by a separate agent with a
   fresh context (only the diff + the item's spec), focused on what breaks money/stock; fix its P0/P1 test-first.
   Codex stays the final reviewer.
1. The implementing agent opens a PR into `develop` (template: `.github/pull_request_template.md`)
   and comments `@codex review` on it. GitHub CI runs only once the PR has the label `ci`: add it when the
   head is final and the full suite passed locally (OWNER_RULES §5, GitHub minutes).
2. Codex reviews on GitHub. Its findings are the record; nothing is passed through chat.
3. The implementing agent fixes every P0/P1 finding, or replies on the thread with the reason it does
   not apply, then pushes. CI must be green on the final head.
4. Merge into `develop` (rules in §2), close the Issue with the evidence, update `docs/HANDOFF.md`,
   take the next Issue (§1).
Codex silent for 40 minutes: re-request once and work on something else. No answer within a working day
(or Codex at its usage limit): merge on green CI plus the independent review, and note "no Codex review" on the PR. Codex taking an implementation task works the same way with the
roles swapped: Claude reviews.

## Code Review Rules
(Read by Codex code review. Flag these as P0/P1; ignore style.)
- Money must stay integer piastres and quantities integer base units; any float, truncation or
  rounding other than `discount_of` (half up) is a defect.
- Stock ledger and audit log are append-only; writes happen inside `db.transaction()` together with
  their audit record.
- Every query touching pharmacy data filters by `org_id`; every service checks `ctx.require(...)`.
- Money-moving POSTs must not post twice on a retry (Idempotency-Key path in `app/api/app.py`).
- No change to `app/migrations/*.sql` files that are already applied; a new migration takes the next
  free number (no duplicate numbers). A `requirements.txt` change must say it ships in the smart-update
  dependency bundle (`docs/SMART_UPDATE.md`).
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
- Never edit an applied migration; add a new one with the next free number. Migrations and dependency
  changes ship through the signed smart update (`docs/SMART_UPDATE.md`), not an installer.
- No real pharmacy, patient or customer data and no secrets in code, tests, commits, logs or chat.
  CI runs `scripts/check_secrets.py` on every PR.
- Never skip, disable or weaken a test to get green. Never invent test results or percentages.

## 5. Needs the owner's explicit approval
The list in OWNER_RULES §2 (installing on the pharmacy PC, an installer build, real data, costs,
irreversible operations, business rules). Merges into `develop` and release PRs into `main` are
pre-approved on the gates. Everything else: decide, document (`docs/DECISIONS.md` if architectural), continue.

## 6. New ideas
Do not interrupt the active task. Add or update an entry in `docs/FUTURE_REQUIREMENTS.md`
(description, value, modules, design impact). Future features are outside the first-release percentage.
At a checkpoint (after a merge, before a new Issue) spend ~5 minutes on the relevant entries and class them:
**A** small, low-risk, clear benefit, fits current work → a scoped Issue now; **B** a minimal preparation now
saves real rework later → only that preparation; **C** everything else → after launch. Launch blockers come first.

## 7. End of every task or session (also before hitting a usage limit)
Save as you go (owner's rule): the owner's usage limit can end a session without warning, so after every
meaningful step (a finding, a decision, a test result, a device fact) push it right away: to `docs/HANDOFF.md`
if your claim on #113 holds it, otherwise as a comment on #113 (OWNER_RULES §6); do not wait for the end of the task or for the owner to read your messages.
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
- **تم تلقائيًا** — owner-facing actions done and verified. **مطلوب منك:** — only what the owner alone can do,
  with exact steps; never hand the owner a step an agent can do.

## 10. Lead role and coordination
- Claude Code leads and is accountable; it judges proposals (the owner's, Codex's, its own earlier ones) on
  evidence, says when a better option exists and recommends one; it does not argue trivia.
- One accountable agent per Issue (claimed on the Issue); no overlapping branches. Codex reviews every PR
  (§2a) and may take a whole Issue; the owner of the Issue integrates.
- Giving Codex a task: an Issue with the whole task (scope, done-when, rules) marked "Owner: Codex", then an
  `@codex …` comment on it. Say **base branch `develop`** every time: the Codex cloud environment checks out
  the default branch (`main`). Codex works on its own branch and PR into `develop`; Claude reviews it (§2a).
  Don't wait on it: Claude keeps working on other Issues.
- Second opinion (Codex security/code review, or a second agent) is required for money, stock integrity,
  migrations, permissions/isolation, backup/restore and architectural decisions; not for routine low-risk work.
- Disagreement: summarise, compare evidence and risk, decide routine points yourself, escalate consequential
  ones to the owner; record real decisions in `DECISIONS.md` or the Issue. Never claim a review that did not happen.

## 11. Unattended work and alerts (zero extra cost)
- Only mechanisms verified end to end count; `HANDOFF.md` lists each as automated / partial / manual.
- Alerts reach the owner as a Claude push notification. GitHub @mentions do **not** notify the owner: agent
  comments are posted from the owner's own account. Alert only when the owner must act or work stopped: what stopped, cause,
  what was tried, exact action, link.
- Retries only for transient failures, bounded; never retry authentication, usage-limit or destructive failures.
