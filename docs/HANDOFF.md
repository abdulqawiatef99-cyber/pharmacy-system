# HANDOFF — current state

Verified 2026-10-30 · Codex · base `develop` @ `0bcba945` (PR #75 merged).
Start/end/efficiency rules: [AGENTS.md](../AGENTS.md). Release gates: [STATUS.md](STATUS.md).
Read task-specific links only when working on that task; archives are historical.

## Current work — #76

- [#76](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/76) claimed by Codex; branch `codex/76-bounded-continuation`. **AUTONOMOUS AGENT CONTINUATION: NOT OPERATIONAL**.
- Code/test checkpoint: `c73fcd2b55651f7922acbd2b37c830557df5e7c5`. Implemented a manual cooperative claim/checkpoint helper and 22 passing offline tests. No agent dispatcher, journal branch, schedule or extra workflow activated. [Capability evidence, limits, recovery and exact next steps](AGENT_CONTINUATION.md).
- Independent architecture/code review found two journal validation/replay defects; fixed with regression tests. This review is a live-session subagent, not proof of autonomous continuation. Independent Claude PR review and full CI remain pending.
- Owner instruction: **prepare the branch only** until zero additional Actions cost is verified. No PR opened; no merge/deployment. Full application suite not run: shell Git fetch fails at proxy; fresh source/doc reads and branch writes use the working GitHub connector. Never test/modify stale local application copies.
- [PR #77](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/77) is open; universal specific prior cost approval applies immediately. No new paid API, subscription, runner, device work or spending authorized; unknown/unbounded costs block activation.
- Next: verify Actions paid overage is disabled, then open this branch's PR into develop, obtain CI and independent review plus owner merge approval. Then configure/verify one supported bounded agent-execution integration and run the real Agent A → trigger → Agent B → commit/PR → CI test. A routine/runner account setup is missing; no unattended continuation is promised.

## Release evidence and unresolved work

- Release blockers remain [#18](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18) → [#19](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/19) → [#20](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/20); no open application-code launch blocker at verification. Do not start unrelated features.
- Device fixes merged: #35, #37, #39, #42, #44, #46, #49; hardware retesting still required.
- Release audit [#68](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/68) already completed. Legacy refund defect [#72](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/72) fixed by [#73](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/73), merged `c9906a9`: recorded 99.99 is preserved; 535 tests without skips recorded, clean Codex review on `de44f90`, [green CI](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/41031438683). Do not redo this audit/fix.
- Candidate: [installer run 72](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/41031738293), `PharmacySetup`, code `c9906a9`, Windows tests/build passed, publication skipped; contains preflight (#66) and #72 fix. Runs 69/71 superseded.
- Installer SHA256: `913ad6027c81d7a32274f4026f82165f28b1c57757adaafbedc7509d722d5dd5`.
- Recorded readiness remains **40%, all gates L1**. Old test/installer figures in STATUS are historical. No new acceptance evidence or percentage change; home-laptop testing does not change it under #18 instructions.

## Windows next steps — #18

- [Home-laptop instructions](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18#issuecomment-6520210512): no results recorded at last check. Local `hostname` must be `DESKTOP-0DCSIC2`; stop on any other device, especially pharmacy PC `DESKTOP-5JVH3QD`. Read-only OS/RAM/disk/process/install-path/port-8000 checks first; existing install or busy port means stop.
- Only after specific owner installation authorization: per-user test install, no admin, synthetic data. Install/launch → sales/refunds (legacy 99.99), discounts/shift/cash → inventory/adjustment → backup/restore → update/rollback → restart/persistence → synthetic preflight. Record each phase in #18 as **home laptop, not pharmacy PC**; unattached hardware = not tested.
- [WINDOWS_ACCEPTANCE.md §0, R1–R10](WINDOWS_ACCEPTANCE.md) has commands/checklist. R8 candidate `b6329815b049eac128d6fd97cda539e2cbfa2458`: rc1 → rc2 → rollback rc1, data unchanged.
- [Preserved 12-step pharmacy retest table](../archive/HANDOFF_2026-10-30_before_compaction.md) is task detail only; its superseded statuses are not new tasks. G6/G7 need evidence for **all R1–R10**, including labels/update/rollback, before L2.
- Known pharmacy devices: receipt `POS-80C` USB002 (drawer attached), labels `Xprinter XP-235B` USB004, scanner `HH360 Linear Imager` USB keyboard. Receipt sticker model optional/unresolved. Actual hardware tests required; Windows CI is not hardware acceptance.
- Each acceptance failure gets its own `launch-critical` Issue with evidence. Then #19 using [OWNER_TEST_PLAN.md](OWNER_TEST_PLAN.md), followed by written owner launch approval for #20.

## Persistent boundaries and deferred decisions

- No main modification/release/deployment. Work branches and PRs target develop; independent review, CI and explicit owner merge approval remain mandatory.
- No real pharmacy/customer/patient data or pharmacy connections; no E-Stock, Radmin VPN, Xprinter driver, existing software/settings changes. No device installation/operation without specific authorization, admin, protection disabling or file deletion/overwriting.
- Cloud access is not laptop access. Current cloud is Linux with no configured laptop connection; no unsupported connection attempts.
- Initial legacy transition uses installer + verified backup, not old updater. Preflight is read-only; stop restore if unsuitable. Requirement/migration changes ship through installer ([UPDATE.md](UPDATE.md)).
- Pharmacy PC previously received an installer and contains test data; do not assume install.bat-only or delete anything automatically. Verify local state and a safe owner-approved transition before real use.
- [#12](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/12): free guard #53 merged; force-push to pre-guard history remains deferred after launch by owner decision. Future-requirements reconciliation (#21 in history) is after launch.

## Automation and history

- Manual: Windows/hardware/owner acceptance. Partial/historical: Claude same-session wakeups/CI follow-up worked, but persistence after usage limits is unverified.
- Failed historically: fresh scheduled session repository access and GitHub `@codex` implementation (#68/#69). Direct Codex work/reviews worked. No inferred usage-limit detector, webhook or background loop.
- Mobile push delivery remains unconfirmed; self-account GitHub mentions are not reliable owner alerts. One completed disabled ChatGPT reminder is not a dispatcher.
- [Full pre-compaction history](../archive/HANDOFF_2026-10-30_before_compaction.md); [earlier Codex history](../archive/HANDOFF_2026-10-28_codex.md). PR #75 is completed; it is not pending work.
