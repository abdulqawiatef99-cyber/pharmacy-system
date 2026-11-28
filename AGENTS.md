# AGENTS.md — technical code rules for every coding agent (Codex, Claude Code, Instinct patches)

The owner's rules (who does what, approvals, merging, talking to the owner) are ONLY in
`docs/OWNER_RULES.md`. This file holds technical code and CI rules only, never owner rules. If anything
here seems to conflict with `docs/OWNER_RULES.md`, that file wins.

## 1. Start of a coding task
- `git fetch origin`, branch from the latest `develop`, read `docs/OWNER_RULES.md` and `docs/HANDOFF.md`.
- One task = one branch = one PR into `develop`. Work branches: `claude/<issue>-<slug>`,
  `codex/<issue>-<slug>` or the name you were given. Never push to another agent's branch.
- Two tasks never change the same file at the same time. Two open PRs on one file: merge one, merge
  `develop` into the other, re-run the suite, CI and review.

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
- An impossible money / stock entry is refused by the server with a clear Arabic message; every money /
  stock entry point has a refusal test (`tests/test_u2_0_refusals.py`, `docs/DECISIONS.md` ADR-018).

## 2. Tests and CI
- Full suite: `python -m unittest discover -s tests -t .` — green on the merge with current `develop`
  before a PR is marked ready. Targeted tests while working.
- Never skip, disable or weaken a test to get green. Never invent test results or percentages.
- GitHub minutes (2,000 free a month, Windows counts double): add the label `ci` only when the head is
  final and the full suite passed locally. Windows runs for Windows-relevant files, PRs into `main`, or
  on demand. A PR labelled `ci` before it was opened can show all jobs skipped: remove and re-add the label.
- CI runs `scripts/check_secrets.py` and `scripts/check_rules.py` (no stale-rule phrases in the repo) on every PR.
- `.github/workflows/branch-guard.yml`: `main` and `develop` change only by merging a PR with green
  `CI / linux`; never a direct or force push.

## 3. Branches
- `develop` = integration; `main` = the pharmacy's update channel (the in-app updater installs `main`).
  Only a release PR `develop` → `main` merges into `main`.
- Migrations and dependency changes ship through the signed smart update (`docs/SMART_UPDATE.md`),
  never an installer.
- Giving Codex a task: always say **base branch `develop`** (its cloud environment checks out `main`).

## 4. Commits and PRs
- English, short one-line commit messages; details in the PR body (`.github/pull_request_template.md`).
- No model names or session identifiers in commits, PRs or code.
- Architectural decisions go in `docs/DECISIONS.md`; new ideas in `docs/FUTURE_REQUIREMENTS.md`.
