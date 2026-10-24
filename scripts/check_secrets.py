#!/usr/bin/env python3
"""Pre-commit guard: refuses to commit pharmacy data or secrets.
Install once:  python scripts/check_secrets.py --install
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BLOCKED_EXT = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".xlsx", ".xls", ".csv", ".pdf",
               ".jpg", ".jpeg", ".png", ".zip", ".env", ".key", ".pem"}
ALLOWED_PREFIX = ("app/web/", "docs/")
SECRET_PATTERNS = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_\-]{20,}",            # Anthropic keys
    r"AKIA[0-9A-Z]{16}",                      # AWS
    r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"(?i)(api[_-]?key|secret|password)\s*=\s*['\"][^'\"\s]{12,}['\"]",
    r"ghp_[A-Za-z0-9]{30,}",                  # GitHub tokens
)]
TEST_SAFE = re.compile(r"(?i)(-Pass-\d|dummy-password|test only)")  # obvious test passwords


def staged() -> list[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def check(paths: list[str]) -> list[str]:
    problems = []
    for p in paths:
        ext = "".join(Path(p).suffixes[-1:]).lower()
        if (ext in BLOCKED_EXT or p.endswith((".db-wal", ".db-shm"))) and not p.startswith(ALLOWED_PREFIX):
            problems.append(f"{p}: data/secret file type must not be committed")
            continue
        try:
            text = Path(p).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for rx in SECRET_PATTERNS:
            for m in rx.finditer(text):
                if not TEST_SAFE.search(m.group(0)):
                    problems.append(f"{p}: looks like a secret: {m.group(0)[:12]}…")
    return problems


def main(argv):
    if "--install" in argv:
        hook = Path(".git/hooks/pre-commit")
        hook.write_text("#!/bin/sh\npython3 scripts/check_secrets.py || exit 1\n")
        hook.chmod(0o755)
        print("pre-commit hook installed")
        return 0
    problems = check(argv[1:] or staged())
    for pr in problems:
        print("BLOCKED:", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
