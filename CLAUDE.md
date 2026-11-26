# CLAUDE.md

Read `docs/OWNER_RULES.md` (the owner's rules, they win), then `AGENTS.md` (shared agent rules), then
`docs/STATUS.md`, the top of `docs/HANDOFF.md` and the last comments on #113.

Claude-specific notes:
- Claude Code is the primary execution agent and technical lead; Codex does independent reviews and
  selected tasks. Several Claude sessions may work in parallel: each claims its files on #113 and works
  on its own branch, so never two agents on the same files.
- GitHub access in cloud sessions is through the GitHub MCP tools (no `gh` CLI).
- Talk to the owner in short Egyptian Arabic; code, commits and agent docs in English.
- Commit messages are short one-liners (details go in the PR body).
- Do not put model names or identifiers in commits, PRs or code.
