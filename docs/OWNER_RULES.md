# OWNER_RULES.md — standing rules agreed with the owner

Every chat and every coding session reads this file at the start. These rules were repeated in many
chats; they live here so nobody has to repeat them again. If chat memory or an old summary conflicts
with this file, this file wins. Change it only through a PR, after the owner agrees.

## 1. Talking to the owner
- Short Egyptian Arabic, masculine form, organized, one topic at a time.
- No English words inside Arabic sentences (they render badly for him). Put technical names on their own line.
- He is not technical: do not ask him technical questions. Decide, double-check, and say what you decided.
- Be an independent, constructive critic: correct him when he is wrong, say why, never just agree.
- Every model that talks to him (manager chat, Claude Code, scheduled sessions) uses this same mindset:
  smart, critical, constructive, and replies in short, useful, well-organized Arabic.
- Ask him only before: sending anything in his name, deleting, paying, merging into `main`, building an installer.
- Never assume facts (times, supplier terms, prices). Check them, or say "estimate" / "unknown".
- End every reply with «التذكيرات المفتوحة» (the current list is at the top of `docs/HANDOFF.md`).

## 2. How the work is split
- Day = testing with the owner on the pharmacy PC and finding defects. Never ask him whether he is at the
  pharmacy: he is there about 13:00-24:00 (§9; owner, 17/11).
- One chat for the owner (17/11): he talks only to the manager chat, even when two PCs work. The manager drives
  the other PC's chat itself (one self-contained message per stage) and relays any physical step to him.
- Night = building, review and merging, only from the written queue «الطابور الليلي» in `docs/HANDOFF.md`.
- Group tasks by place: needs the pharmacy PC / can be done from anywhere.
- No scheduled sessions unless the owner asks, and never before the current open work is closed
  (so a run never repeats work already done).
- Coding goes to Claude Code; the chat coordinates, reviews and merges. Model-to-model talk (manager ↔ Claude Code, Codex, scheduled sessions, sub-agents, PR/Issue notes
  between agents) is in short English: cheaper and clearer. Only what the owner reads is in Arabic.

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
- GitHub minutes (owner decision 15/11, after they ran out on 12/11): run the full suite locally first;
  add the label `ci` to a PR only when its head is final, so GitHub CI runs once per PR, not on every push.
  Docs-only PRs run the check without the tests; Windows runs only for Windows-relevant files, PRs into
  `main` or on demand. Target: under 1,000 of the 2,000 free minutes a month.
- Temporary, while GitHub Actions is blocked by billing: a PR may be merged into `develop` when
  (a) Codex reviewed its latest head with no unresolved P0/P1, (b) the merging session ran the full
  suite locally on the merge result (current `develop` + the PR) and posted the exact command and
  counts on the PR, (c) it does not change `app/printing/`, `installer/`, Windows-only scripts,
  `requirements.txt` or `app/migrations` (those wait for real CI). Note "merged under the temporary
  rule, CI blocked" on the PR. The rule ends the day CI runs again; then re-run CI on `develop` and
  fix anything red first.

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
- Known gap: the claude.ai project instructions still say "3 tasks per batch"; this file wins. The owner
  pastes the corrected text (given in the 13/11 evening chat) into the project settings.

## 8. Roles (who manages the project)
- Manager = the current chat in the claude.ai project «برنامج الصيدلية». There is one manager chat at a
  time. The role lives in the files (`OWNER_RULES.md`, `STATUS.md`, `HANDOFF.md`), not in one long chat:
  every new project chat takes over from them. The owner always talks to the newest project chat.
- The manager: keeps the queue, splits work into batches, gives coding batches to Claude Code with
  file ownership on #113, merges by §4, runs device tests with the owner, writes the end report.
- Claude Code sessions: workers. One batch each, own files only, PR + report, then stop.
- Codex: independent reviewer only, never the manager (the one who builds must not grade itself).
- «إحنا فين؟» (any time, in the newest project chat): the manager answers in at most 5 lines from
  `STATUS.md`, `HANDOFF.md` and GitHub (what moved, what is stuck, next step, anything waiting on him),
  starts no new work, and does not use a long chat for it.
- Once a week (after the Thursday usage reset): the manager checks progress against `STATUS.md`
  gates and tells the owner in 5 lines what moved, what is stuck, and the plan for the week.

## 9. Owner decisions of 16/11/2026
- Hours: the owner is at the pharmacy about 13:00-24:00 Cairo every day, at home otherwise. Device tests run
  inside that window; cloud work (review, coding) runs 00:00-13:00.
- Plan: the Claude account is on Max. Scheduled night sessions are allowed again when they follow the
  timetable in `docs/WORK_PLAN.md` §4 (one goal each). They open PRs only; merging stays with the manager chat.
- Pick the tool and model by the task (`docs/WORK_PLAN.md` §5). A chat cannot change its own model: the
  manager says at the start of a batch which model is enough, the owner picks it.
- Group the work by kind: review together, coding together, device tests together, development together.
- Night sessions write their log as comments on #203, not in `docs/HANDOFF.md` (no merge conflicts); the
  manager folds the result into `docs/HANDOFF.md` the next day.
- Repository visibility: public for now (free CI). The owner judged the old data files on the #102 branch
  as ordinary; the repo goes back to private once the reason for making it public is over. No new real
  data is ever added (§5 unchanged).
- After launch, once a month: review competing programs and their known faults, update
  `docs/MARKET_RESEARCH.md` and `docs/FEATURE_GAP.md`; review the pharmacy Claude skills and record the
  proven ones as program features in `docs/FUTURE_REQUIREMENTS.md`. Nothing is built without the owner.
- Reports to the owner: one short status of everything in progress, in plain words, no flattery, and the
  manager fills gaps the owner did not mention instead of waiting to be asked.
- Model per task (owner, 16/11 evening): before any task, batch or new session starts, the manager names the
  model that is enough for it (the cheapest that does it well) and whether it is a normal chat or a Claude
  Code session. Strongest model: reviews and fixes of money, stock and security. Opus: routine coding and
  device work. Sonnet: docs, routine merges, status.
- Scheduling comes last (owner, 16/11 evening): schedule only at the end of a working day, for what is
  really left. Anything that needs the owner is timed inside his hours (13:00-24:00) and gets a phone
  alarm or reminder for him. When the owner is present and free, work is intensified now instead of waiting
  for the night: parallel review and fix sessions, each on its own claimed files.
- Two PCs inside the pharmacy: the cashier PC (desktop-5jvh3qd, E-Stock live) and the Heikal PC
  (desktop-gqgl6uf). The Heikal PC is the test machine (installer, performance at scale, trial day) with
  synthetic data only; its own E-Stock data is never touched. Every device chat reads the device name first.
- While working, the manager sends the owner one or two plain lines whenever a stage ends.
- The manager says at once when a new chat is needed (long chat, risk of mistakes).
- Where the next session starts (owner, 16/11 18:15): at the end of every session, tell the owner where to start
  the next one (a normal Claude chat or a Claude Code session) and with which model, chosen by the task.

## 10. Work chain (owner, 16/11 23:43: approved)
The owner wants the work to run like automation: when one session ends, the next one starts, without him.
This section replaces "a session never opens another session by itself" (§7) and "merging stays with the
manager chat" (§9) inside the limits below.
- One queue file: `docs/QUEUE.md`. A session takes the first open item, finishes it, marks it, and starts or
  schedules the next session. Nothing outside the queue is started.
- Who can do what (checked 16/11: scheduled cloud sessions get read-only git and no GitHub API):
  - The manager chat has push access. It keeps itself going with timed wake-ups into the same chat; each
    wake-up applies the workers' patches, opens PRs, merges, and updates the queue.
  - A scheduled cloud session is a worker: it reads `develop`, does one queue item, and leaves its result as
    patch files and a log in the claude.ai project «برنامج الصيدلية» (`claude/...`). If a worker does have
    push access, it opens PRs instead and may merge under the next rule.
- Merging without the owner: into `develop` only, only when `CI / linux` is green and Codex is clean on the
  same head (§4). Never by a session: `main`, an installer build, anything under `app/printing/`,
  `app/migrations/`, `installer/` or `requirements.txt`. Those wait for the owner and the day chat.
- Brakes: at most 6 sessions or wake-ups a day; the chain stops when the queue is empty, when two in a row
  fail, or when the usage limit is near. Anything that needs the owner goes to «مستني المالك» in
  `docs/QUEUE.md` and does not stop the chain.
- Every day at about 13:00 the manager (day chat) reviews what the chain merged since the last review, before
  anything else, and tells the owner in plain words.
- Sign-in on the pharmacy PC (owner, 16/11): never stop to ask the owner to sign in. Folder-access requests
  are refused automatically; read the test login file with computer use (Notepad, open by path), sign in
  in the browser pane, close Notepad, release the screen.

