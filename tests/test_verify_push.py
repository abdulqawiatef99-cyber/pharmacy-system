"""Branch guard (#12): `.github/scripts/verify_push.sh` decides whether a push to main/develop is the
merge of a PR with a green "CI / linux". A fake `gh` serves recorded GitHub API answers and applies the
script's own `--jq` expressions with the real jq, so both the shell logic and the selection are tested.
The workflow wiring (Issue + backup on failure, installer publish gate) is checked from the YAML."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "verify_push.sh"
SHA, HEAD = "a" * 40, "b" * 40

FAKE_GH = r"""#!/usr/bin/env bash
# fake gh: `gh api <path> --jq <expr>` -> jq over a recorded answer chosen by the path
path="$2"; expr="$4"
case "$path" in
  *"/pulls") f="$FAKE_DIR/pulls.json" ;;
  *"check-runs"*) f="$FAKE_DIR/checks.json" ;;
  *) echo "unexpected call: $*" >&2; exit 2 ;;
esac
[ -f "$f" ] || { echo "HTTP 502" >&2; exit 1; }      # API failure
jq -r "$expr" "$f"
"""


@unittest.skipIf(os.name == "nt", "the guard runs only on ubuntu runners (branch-guard.yml, the installer gate)")
@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "bash and jq needed (present on CI runners)")
class TestVerifyPush(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir / "gh").write_bytes(FAKE_GH.encode())          # LF line endings whatever the OS (Codex on #53)
        (self.dir / "gh").chmod(0o755)

    def answers(self, pulls=None, checks=None):
        if pulls is not None:
            (self.dir / "pulls.json").write_text(json.dumps(pulls))
        if checks is not None:
            (self.dir / "checks.json").write_text(json.dumps({"check_runs": checks}))

    def run_guard(self, branch="main", forced="false"):
        env = {**os.environ, "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(self.dir),
               "REPO": "o/r", "BRANCH": branch, "SHA": SHA, "FORCED": forced, "RETRY_SLEEP": "0", "GH_TOKEN": "x"}
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.strip()

    def merged(self, base="main", merge_sha=SHA, number=7):
        return {"number": number, "merged_at": "2026-10-29T00:00:00Z", "base": {"ref": base},
                "merge_commit_sha": merge_sha, "head": {"sha": HEAD}}

    green = {"app": {"slug": "github-actions"}, "conclusion": "success"}

    def test_merged_pr_with_green_ci_is_verified(self):
        self.answers([self.merged()], [self.green])
        self.assertEqual(self.run_guard(), (0, f"verified: PR #7, CI / linux green on {HEAD}"))

    def test_force_push_never_passes(self):
        self.answers([self.merged()], [self.green])
        code, out = self.run_guard(forced="true")
        self.assertEqual(code, 1)
        self.assertIn("force push", out)

    def test_direct_push_is_reported(self):
        for pulls in ([], [self.merged(base="develop")], [self.merged(merge_sha="c" * 40)],
                      [{**self.merged(), "merged_at": None}]):
            self.answers(pulls, [self.green])
            code, out = self.run_guard()
            self.assertEqual(code, 1, pulls)
            self.assertIn("direct push", out)

    def test_merge_without_green_ci_is_reported(self):
        for checks in ([], [{**self.green, "conclusion": "failure"}],
                       [{"app": {"slug": "someone-else"}, "conclusion": "success"}]):   # only GitHub Actions counts
            self.answers([self.merged()], checks)
            code, out = self.run_guard()
            self.assertEqual(code, 1, checks)
            self.assertIn('without a green "CI / linux"', out)

    def test_api_failure_is_not_a_pass(self):
        code, out = self.run_guard()                                    # no recorded answers: every call fails
        self.assertEqual(code, 1)
        self.answers([self.merged()])                                   # PR found, check-runs call fails
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn('without a green "CI / linux"', out)


class TestGuardWiring(unittest.TestCase):
    def test_installer_publishes_only_after_the_gate(self):
        wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        self.assertIn("run: .github/scripts/verify_push.sh", wf)
        self.assertIn("needs: gate", wf)
        self.assertIn("needs.gate.result == 'success' || needs.gate.result == 'skipped'", wf)
        self.assertIn("if: github.event_name == 'push'", wf)              # manual review builds skip the gate

    def test_installer_artifact_is_short_lived_and_never_blocks_the_release(self):
        wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        upload = wf.split("- uses: actions/upload-artifact@v4", 1)[1].split("- name:", 1)[0]
        self.assertIn("retention-days: 14", upload)                       # #122: quota filled at 90 days
        self.assertIn("continue-on-error: true", upload)                     # #122: a full quota blocks no delivery
        self.assertLess(wf.index("actions/upload-artifact@v4"), wf.index('Publish as release "installer-latest"'))

    def test_program_files_have_the_line_ends_github_serves(self):
        """#18 B13: the runner's git writes CRLF (core.autocrlf); the in-app update downloads LF from GitHub."""
        wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        archives = [line.strip() for line in wf.splitlines() if " archive " in line and line.strip().startswith("git")]
        self.assertEqual(len(archives), 2)                                   # the program and the update candidate
        for line in archives:
            self.assertIn("-c core.autocrlf=false", line)

    def test_manual_review_build_is_delivered_as_a_prerelease(self):
        wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        step = wf.split('- name: Publish review build as pre-release "installer-review"', 1)[1].split("- name:", 1)[0]
        self.assertIn("if: github.event_name == 'workflow_dispatch'", step)
        self.assertIn("gh release create installer-review", step)
        self.assertIn("--prerelease", step)                                   # never shown as "latest"
        self.assertNotIn("installer-latest", step)                            # main's release stays untouched
        self.assertIn("Get-FileHash", step)

    def test_failure_opens_an_issue_and_keeps_the_old_tip(self):
        wf = (ROOT / ".github" / "workflows" / "branch-guard.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [main, develop]", wf)
        self.assertIn('gh issue create --repo "$REPO"', wf)
        self.assertIn('backup="guard/$BRANCH-${BEFORE:0:12}"', wf)
        self.assertIn("verify_push.sh) && { echo", wf)                    # a verified push stops before any write


if __name__ == "__main__":
    unittest.main()
