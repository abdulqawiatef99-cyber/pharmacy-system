"""CI browser install (#133): when the apt mirrors are down, install Chromium alone instead of failing
every PR; a failed fallback must still fail the job."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "install_browser.sh"


@unittest.skipIf(os.name == "nt" or not shutil.which("bash") or not shutil.which("timeout"),
                 "needs bash and coreutils timeout (the script runs on the linux CI job only)")
class TestBrowserInstallFallback(unittest.TestCase):
    def _run(self, deps_body: str, plain_body: str, limit: str = "20"):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        log = d / "calls.log"
        pw = d / "playwright"
        pw.write_text("#!/usr/bin/env bash\n"
                      f'echo "$*" >> "{log}"\n'
                      f'if [ "$2" = "--with-deps" ]; then {deps_body}; else {plain_body}; fi\n', encoding="utf-8")
        pw.chmod(0o755)
        p = subprocess.run(["bash", str(SCRIPT), str(pw)], env={**os.environ, "PW_DEPS_TIMEOUT": limit},
                           capture_output=True, text=True, timeout=60)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return p, calls

    def test_normal_install_needs_no_fallback(self):
        p, calls = self._run("exit 0", "exit 0")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(calls, ["install --with-deps chromium"])

    def test_failed_apt_falls_back_to_the_browser_alone(self):
        p, calls = self._run("exit 100", "exit 0")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(calls, ["install --with-deps chromium", "install chromium"])
        self.assertIn("apt mirrors unreachable", p.stdout)

    def test_hanging_apt_is_cut_off_then_falls_back(self):
        p, calls = self._run("exec sleep 30", "exit 0", limit="1")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(calls, ["install --with-deps chromium", "install chromium"])

    def test_failed_fallback_fails_the_job(self):
        p, calls = self._run("exit 100", "exit 7")
        self.assertEqual(p.returncode, 7)
        self.assertEqual(calls[-1], "install chromium")


class TestCiUsesTheScript(unittest.TestCase):
    def test_ci_installs_the_browser_through_the_script(self):
        wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn('bash scripts/ci/install_browser.sh "$RUNNER_TEMP/pw/node_modules/.bin/playwright"', wf)
        self.assertNotIn("install --with-deps chromium\n", wf.split("Browser for the UI tests", 1)[1].split("- name:", 1)[0])


if __name__ == "__main__":
    unittest.main()
