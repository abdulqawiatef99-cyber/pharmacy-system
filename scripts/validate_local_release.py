"""Portable isolated release checks; never starts a server or accesses live data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import tempfile
import time


def isolated_environment(data: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('PHARM_') and k != 'ANTHROPIC_API_KEY'}
    env.update(PYTHONUTF8='1', ANTHROPIC_API_KEY='', PHARM_DATA_DIR=str(data),
               PHARM_DB_PATH=str(data / 'synthetic.db'), PHARM_UPLOAD_DIR=str(data / 'uploads'),
               PHARM_BACKUP_DIR=str(data / 'backups'), PHARM_PRINTER='none',
               PHARM_RELEASE_CANDIDATE='1')
    return env


# The whole suite on a GitHub Windows runner: over 300 s with 850 tests (12/11), 12 minutes with 1,830 (19/11
# morning), and with 1,905 it was cut at 900 s after about 1,500 of them (run 1052): about 19 minutes. The limit
# is there to stop a hung run, not to fail a slow one; the other steps keep 300 s.
TESTS_TIMEOUT = 1800


def validate(root: Path, output: Path, *, timeout: int = 300, tests_timeout: int = TESTS_TIMEOUT) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = {'platform': platform.platform(), 'python': platform.python_version(),
              'source': str(root.resolve()), 'scope': 'synthetic automated checks only',
              'windows_hardware_accepted': False, 'ok': False, 'steps': []}
    with tempfile.TemporaryDirectory(prefix='pharmacy-release-check-') as temporary:
        env = isolated_environment(Path(temporary))
        steps = [('tests', ['-m', 'unittest', 'discover', '-s', 'tests', '-t', '.']),
                 ('selftest', ['manage.py', 'selftest', '--quick']),
                 ('init', ['manage.py', 'init', '--pharmacy', 'Synthetic release check',
                           '--username', 'synthetic-owner', '--password', secrets.token_urlsafe(24)]),
                 ('verify', ['manage.py', 'verify'])]
        for name, arguments in steps:
            started = time.monotonic()
            log = output / (name + '.log')
            error = None
            with log.open('wb') as stream:
                try:
                    result = subprocess.run([sys.executable, *arguments], cwd=root, env=env,
                                            stdout=stream, stderr=subprocess.STDOUT,
                                            timeout=tests_timeout if name == 'tests' else timeout,
                                            stdin=subprocess.DEVNULL, check=False)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code, error = -1, 'timeout'
                except OSError as exc:
                    code, error = -1, type(exc).__name__
            report['steps'].append({'name': name, 'returncode': code, 'error': error,
                                    'seconds': round(time.monotonic() - started, 3), 'log': log.name})
            if code:
                break
        report['ok'] = len(report['steps']) == len(steps) and all(s['returncode'] == 0 for s in report['steps'])
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Dedicated folder for synthetic check logs')
    args = parser.parse_args()
    report = validate(Path(__file__).resolve().parents[1], args.output.resolve())
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
