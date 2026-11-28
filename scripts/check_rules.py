"""Fail if a cancelled owner rule reappears anywhere in the repository.

All owner rules live in docs/OWNER_RULES.md only. The phrases below come from rules the owner
cancelled (28/11/2026). They are search patterns, not rules. When the owner cancels another rule,
add its wording here in the same PR that removes it from docs/OWNER_RULES.md.

Usage: python scripts/check_rules.py [files...]   (no args = every tracked text file)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
SKIP = {SELF, ROOT / "tests" / "test_check_rules.py"}  # they hold the patterns on purpose

STALE = [
    r"نزّ?له(?![اهمو])",                         # «install only after the owner says نزّله»
    r"after\s+«?نزّ?له»?",
    r"waits? for (the owner's )?«?نزّ?له",
    r"E-Stock (stays|is kept) as read-only",
    r"إي ستوك هو الكاشير",
    r"E-Stock is the cashier",
    r"Claude Code is the primary",
    r"Claude Code (leads|implements)",
    r"implement and lead technically",
    r"البرمجة لكلود كود",
    r"اسألني (بس )?قبل[^\n]{0,40}الدمج",
    r"at most 6 started sessions",
    r"One goal per session",
    r"دفعة واحدة لكل محادثة",
    r"Codex silent for 40 minutes",
    r"Daily ~?13:00",
    r"3 مشاريع مفتوحة بس",
    r"No multi-branch",
    r"WhatsApp: read only, never send",
    r"launch freeze",
    r"الإطلاق مقفول على الإضافات",
]
PATTERN = re.compile("|".join(f"(?:{p})" for p in STALE), re.IGNORECASE)
TEXT_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".txt", ".json", ".js", ".html", ".ps1", ".bat", ".toml", ".cfg", ".ini"}
# Files where the word «نزّله» is ordinary Arabic ("downloaded it"), not the cancelled rule.
ALLOW = {
    ("docs/PROTECTION_PLAN.md", "اللي نزّله قبل كده"),
}


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line]


def scan(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        path = path.resolve()
        if path in SKIP or path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            m = PATTERN.search(line)
            if not m:
                continue
            if any(rel == f and ok in line for f, ok in ALLOW):
                continue
            hits.append(f"{rel}:{no}: cancelled owner rule: {m.group(0)!r}")
    return hits


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] if argv else tracked_files()
    hits = scan(paths)
    for h in hits:
        print(h)
    if hits:
        print(f"{len(hits)} line(s) repeat a cancelled rule. Owner rules live only in docs/OWNER_RULES.md.")
        return 1
    print("check_rules: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
