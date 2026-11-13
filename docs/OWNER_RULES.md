# OWNER_RULES.md — standing rules agreed with the owner

Every chat and every coding session reads this file at the start. These rules were repeated in many
chats; they live here so nobody has to repeat them again. If chat memory or an old summary conflicts
with this file, this file wins. Change it only through a PR, after the owner agrees.

## 1. Talking to the owner
- Short Egyptian Arabic, masculine form, organized, one topic at a time.
- No English words inside Arabic sentences (they render badly for him). Put technical names on their own line.
- He is not technical: do not ask him technical questions. Decide, double-check, and say what you decided.
- Be an independent, constructive critic: correct him when he is wrong, say why, never just agree.
- Ask him only before: sending anything in his name, deleting, paying, merging into `main`, building an installer.
- Never assume facts (times, supplier terms, prices). Check them, or say "estimate" / "unknown".
- End every reply with «التذكيرات المفتوحة» (the current list is at the top of `docs/HANDOFF.md`).

## 2. How the work is split
- Day = testing with the owner on the pharmacy PC and finding defects. The owner's time at the
  pharmacy is not fixed; ask him at the start of a day chat whether he is there.
- Night = building, review and merging, only from the written queue «الطابور الليلي» in `docs/HANDOFF.md`.
- Group tasks by place: needs the pharmacy PC / can be done from anywhere.
- No scheduled sessions unless the owner asks, and never before the current open work is closed
  (so a run never repeats work already done).
- Coding goes to Claude Code; the chat coordinates, reviews and merges. Talk to Claude Code and Codex in English.

## 3. Parallel sessions (no overlap)
- Before starting parallel sessions, write on #113 which files/modules each one owns.
  Two sessions never touch the same files, even "small" edits (payment channels, settings, docs/HANDOFF.md).
- A session that needs a file owned by another session stops and writes a note on #113 instead.
- If two open PRs touch the same file, merge one, then rebase the other and re-run CI + Codex before merging it.

## 4. Merging
- `develop` only when `CI / linux` is green on the PR's latest head AND Codex reviewed that same head
  with no unresolved P0/P1 (AGENTS.md §2, §2a). Merge commit. Never `main` without the owner.
- `develop` is the pharmacy PC's update channel. The in-app «تحديث» button works since 12/11 (#158);
  use it instead of installers. Changes to `requirements.txt` or `app/migrations` still need an installer:
  keep them grouped.
- After merging a batch, check CI on `develop` before telling the owner to press «تحديث».

## 5. Pharmacy PC and data
- E-Stock is the live cashier. Never use full-screen computer control while the owner is working.
- WhatsApp: read only, never send. Never open «اكونتات الشركات» or any note with passwords or codes.
- Real pharmacy data never leaves the pharmacy PC. Cloud tests use synthetic data at the same scale.
- Never write passwords in chat, commits or issues (the test login lives in a file on the PC).

## 6. Continuity (no handoff messages)
- Start of a session: `AGENTS.md`, this file, `docs/STATUS.md`, `docs/HANDOFF.md`, last comments on #113 and #18.
- Before a chat or session ends, or when it gets long: update `docs/HANDOFF.md` (done with evidence,
  not done, assumptions, next step) and push it. The next chat continues from the file, not from a pasted summary.
- If you find a mistake, fix everything built on it.

## 7. Short sessions (one goal per session)
Long chats drift (contradictions, forgotten rules) and burn usage faster. So every chat and every
coding session has ONE goal (one outcome, e.g. "PR queue clean" or "the 3 PC tests done"); tasks are
just the steps toward it. When the goal is reached, the session stops and reports.
- Two kinds of work (§2): cloud work (coding, review, cloud tests) runs at night in scheduled sessions;
  hands-on work (tests on the pharmacy PC) runs in the day, only when the owner says he is at the pharmacy.
- Save as you go: after each step, update `docs/HANDOFF.md` and push, so a usage-limit cut loses nothing.
- Stop early when: you contradict an earlier decision, re-ask a settled question or forget a rule
  (the real signs of drift); or the usage limit is near. Provisional extra limits: about 25 owner
  messages or about 2 hours. These numbers are estimates; review them after 5 sessions using the end reports.
- Who starts the next session: day = the owner, by writing «كمّل» in a new chat inside the project;
  night = scheduled sessions (only when the owner asked for them). A session never opens another session by itself.
- End report, in this order, in the chat and at the top of `docs/HANDOFF.md`:
  1. done, with evidence (PR/commit/test result); 2. not done and why; 3. decisions taken;
  4. the next goal; 5. the line «المحادثة دي اتحفظت وممكن تتمسح».
- A chat whose report is in `docs/HANDOFF.md` holds nothing else; the owner can delete it.
- Known gap: the claude.ai project instructions still say "3 tasks per batch"; this file wins. Fix the
  project instructions in the next day chat.

## 8. Roles (who manages the project)
- Manager = the current chat in the claude.ai project «برنامج الصيدلية». There is one manager chat at a
  time. The role lives in the files (`OWNER_RULES.md`, `STATUS.md`, `HANDOFF.md`), not in one long chat:
  every new project chat takes over from them. The owner always talks to the newest project chat.
- The manager: keeps the queue, splits work into batches, gives coding batches to Claude Code with
  file ownership on #113, merges by §4, runs device tests with the owner, writes the end report.
- Claude Code sessions: workers. One batch each, own files only, PR + report, then stop.
- Codex: independent reviewer only, never the manager (the one who builds must not grade itself).
- Once a week (after the Thursday usage reset): the manager checks progress against `STATUS.md`
  gates and tells the owner in 5 lines what moved, what is stuck, and the plan for the week.
