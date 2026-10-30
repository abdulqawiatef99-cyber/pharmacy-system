# Agent continuation — Issue #76

**AUTONOMOUS AGENT CONTINUATION: NOT OPERATIONAL**

Checked 2026-10-30 on `develop` at `0bcba945`. This document records a partial
implementation, not an enabled dispatcher. Existing session rules remain in
[AGENTS.md](../AGENTS.md); GitHub Issues remain the task queue.

## Verified capabilities and limits

| Surface | Confirmed evidence | Missing prerequisite / boundary |
| --- | --- | --- |
| GitHub connector | Fresh files, Issues, branches and commits work. PR #75 merged; #77 is open. | No callable agent-launch or workflow-dispatch tool in this session. |
| Existing Actions | Three workflows: `ci.yml`, `branch-guard.yml`, `windows-installer.yml`. PR/push CI tests code; branch guard checks branch changes; installer builds/publishes only under existing gates. | None launches Claude or Codex. Remaining included minutes and a zero-spend cap are unverified. No workflow changed or manually run. |
| Codex CLI | `codex exec --help` works; `codex login status` reports ChatGPT login. Official docs support non-interactive execution and subscription authentication. | No durable dispatcher verified; shell Git access fails at the proxy. Login does not establish account-specific extra-credit/billing limits. No `codex exec` inference launched. |
| Claude Code | Official routines API can start cloud sessions using a routine-scoped bearer credential and subscription usage. Official GitHub Action supports subscription tokens or API credentials. | Claude CLI absent here; no configured routine ID/credential or authenticated Claude runner verified. Existing usage limit still applies. API billing is not implied by a subscription. |
| ChatGPT | Automation tools are exposed. Private lookup found one completed, disabled reminder, not a repository worker. | No repository execution by a scheduled task verified; no new schedule created. Task processing may use plan allowance or workspace credits. Callable tools do not prove credentials, billing, or runtime access on a future run. |
| Current cloud | Provider reports no configured secrets/outbound identities; only presence flags for relevant API-key variables were checked, all absent. GitHub connector is independently working. | No laptop connection, device testing or unattended access authorized by this work. No credential values read or copied. |

Official references (checked on the date above):

- [Claude routine trigger, prerequisites and subscription billing](https://platform.claude.com/docs/en/api/claude-code/routines-fire)
- [Claude GitHub Action setup and authentication](https://code.claude.com/docs/en/github-actions)
- [Codex non-interactive execution](https://developers.openai.com/codex/noninteractive)
- [Codex authentication: subscription versus API](https://developers.openai.com/codex/auth)
- [ChatGPT scheduled tasks and usage](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt)

These establish supported product interfaces, not that this account has configured
them. Historical `@codex` implementation attempts failed (#68/#69); do not repeat
them as a purported dispatcher. Subagents inside a live parent session are not
evidence of continuation after that session ends.

## Implemented prerequisite: manual cooperative claim journal

`scripts/agent_handoff.py` uses existing `gh` authentication, standard-library
Python and the fixed repository only. It launches no agent, workflow, shell
command from Issue text, schedule, installation, merge or deployment. It is
**not activated**: no `coordination/active` branch or opt-in label was created by
this implementation. Do not run its network commands until the pilot is reviewed
and approved within a verified zero-additional-cost environment.

- Issues remain authoritative for scope, approval and dependencies; the journal
  only serializes cooperating participants. Before adding `agent:ready`, a trusted
  coordinator must inspect Issue discussion/claims and open PRs, confirm scope
  and dependencies, and record approval. Issue text is data, never executable instructions.
- This initial pilot intentionally rejects `owner-action` and `launch-critical`
  Issues, assigned Issues, missing dependency manifests and possibly overlapping
  PRs. It requires exactly one `<!-- agent-dependencies: [] -->` marker, or up to
  five same-repository Issue numbers, all fetched closed. This metadata adds no
  new queue. It never automatically marks an Issue ready or picks arbitrary work.
- The PR overlap check is conservative but incomplete: it checks branch numbers
  and `#number` in PR bodies, not every reference/title/URL. Manual discussion/PR
  preflight remains mandatory. Eligibility must be reread immediately before any
  future executor runs, since Issue edits are not atomic with Git ref updates.
- The dedicated **state-only** branch contains `state.json`; never open a PR or
  merge that branch. Initial ref creation is create-only. Later commits have the
  observed head as their sole parent and use `force=false`: competing sibling
  updates cannot both advance the ref. No main/develop mutation is performed.
- One active receipt binds Issue, agent, run ID and work branch. Exact replay does
  not create work. `claimed → running → review` records bounded notes/evidence;
  `blocked` retains the reservation. Evidence URLs are references, not proof that
  CI/review succeeded. Claims remain held through review, with no release command.
- No lease expiry, time-based stealing, retry loop or automatic release. Agent and
  run ID are attribution fields, not authenticated identities. A writer bypassing
  the helper can bypass the cooperative lock; this is not branch protection.
- Limits: one explicit task per invocation, five dependencies, 100 receipts,
  50 events per receipt, 2,000 characters per note, ten evidence URLs, 256 KiB
  journal and 30 seconds per `gh` request. Requests are never automatically retried.
  Read/transport/conflict/auth errors stop; raw credential diagnostics are suppressed.

After approval, the manual CLI (not a scheduled dispatcher) would be:

```sh
python scripts/agent_handoff.py status
python scripts/agent_handoff.py claim --issue 900 --agent codex --run-id docs_pilot_001 --branch codex/900-doc-test
python scripts/agent_handoff.py checkpoint --agent codex --run-id docs_pilot_001 --phase running --note "Tests complete; next: independent review"
```

Issue 900 above is an illustrative number, not a real eligible task. Claiming
does not create the work branch or begin implementation; the agent must verify
its claim won and create its isolated branch from current develop. Mirror the
checkpoint commit URL to the Issue and keep HANDOFF short under existing rules.

## Recovery and approval gates

On conflict, stop. On write timeout, the outcome is unknown: read `status` and
compare exact run identity and latest event before attempting the same operation.
Never blindly retry or change run ID to escape a lock. On malformed state, auth
failure, provider limit or exhausted journal capacity, stop and record the blocker
in the Issue; do not treat corruption as idle or switch to paid credentials.

Reconciliation is intentionally manual: verify the previous worker is stopped,
read its Issue, branch and PR, record the reconciliation decision and next step in
the Issue, and preserve its events. A trusted coordinator may then append a
single-parent non-force state commit marking the receipt `released` and `active`
null. For completed work, first verify final-head CI, an independent review and
explicit owner merge approval. For abandoned/blocked work, record cancellation or
recovery authorization and ensure no worker remains active. A released run ID can
never reopen; continuing work needs a newly authorized receipt. Do not delete the
state branch or force-push as recovery. Automatic release needs a separately
reviewed implementation with actual head-specific CI/review verification.

## Cost policy and exact continuation checkpoint

The owner's universal gate in [PR #77](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/77)
applies immediately, whether merged or not. No additional financial expense is
authorized. Existing paid subscriptions are not separate API credit. Before any
additional spending disclose price/currency, billing basis, expected usage,
maximum exposure and cheaper alternatives, then obtain specific prior approval.
Unknown or unbounded cost means do not activate. Warn before unusually large quota use.

The owner confirmed in this session that the Actions spending cap is unknown and
instructed **prepare the branch only**. Therefore no PR is opened (even a draft PR
would trigger CI), no Actions run is dispatched, and no dispatcher is enabled.
No new expense was activated. No full application suite was run from stale local
source: cloud `git fetch` fails; the isolated workspace contains only new helper
files/tests and fresh connector-read documents. Existing full CI remains mandatory
after zero additional CI spending is verified and a PR is authorized. A read-only
billing API attempt through gh also failed at the proxy; the connector does not
expose account billing. Actual included allowance, remaining minutes and an
enforced zero-spend cap are all UNKNOWN, not inferred from plan names.

Reverification: the original 22 tests passed again. Two additional regression tests
first reproduced inconsistent history acceptance (four subcases) and a malformed
active-ID error; both defects are fixed. All 24 offline tests now pass.

Validation: 24 offline tests cover corrupt journals, competing initial/ref writes,
stale heads, run identity, exact replay at capacity, phase/history consistency, eligibility/dependencies,
blocked/review retention, unauthorized transitions, timeout/auth handling and
shell-free JSON transport. GitHub race tests use an in-memory Git graph, **not a
live service or agent**. Independent subagent review found two defects in journal
validation/replay; both were fixed with regression tests. Review evidence is
recorded in #76; independent Claude PR review is still required before merge.

Smallest next prerequisites, in order:

1. Verify zero additional Actions spending (included minutes with paid overage
   disabled), then open the prepared branch PR into develop and obtain full CI
   and independent review. Do not merge without owner approval.
2. Configure one supported repository-bound execution surface. First candidate:
   Claude Code cloud Routine using existing subscription, pharmacy-system attached,
   explicit develop/docs-only scope, extra usage disabled, and a bounded trigger.
   An owner-only routine UI/auth setup may be needed; never paste its token into
   chat or a repository. An authenticated persistent Codex runner is an alternative
   only after its lifetime, network, billing and permissions are established.
3. Only after that setup is verified: create one harmless opt-in documentation
   Issue; Agent A records a checkpoint, the real trigger starts Agent B, and B
   creates a commit/PR without an owner prompt. Capture trigger ID, provider
   session/run URL, exact branch/commit/PR, final-head CI, independent review and
   actual owner outcome delivery. Enforce one attempt and no successor loop.
4. If any step fails, retain the claim/checkpoint, notify only for required action,
   and keep the status NOT OPERATIONAL. No usage-limit webhook or automatic
   failover is assumed. Resume release priorities only after this evidence exists;
   #18 still requires the correct authorized Windows device and actual hardware.
