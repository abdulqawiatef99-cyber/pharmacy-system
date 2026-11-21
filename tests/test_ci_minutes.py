"""CI must stay inside GitHub Free's 2,000 minutes a month (they ran out on 12/11/2026 after 435 runs).

Text checks on the workflow files (no YAML library in requirements): a PR runs CI only with the "ci"
label, docs-only changes skip the test steps, Windows runs only where it matters, images are pinned
(ubuntu-latest moved to a new Ubuntu from 19/10/2026; a silent image change must not break CI).
"""
import re
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"


class TestCiMinutes(unittest.TestCase):
    def setUp(self):
        self.ci = (WF / "ci.yml").read_text(encoding="utf-8")

    def test_pull_requests_run_only_with_the_ci_label(self):
        self.assertIn("types: [opened, synchronize, reopened, labeled]", self.ci)
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'ci')", self.ci)
        self.assertIn("github.event.label.name == 'ci'", self.ci)       # another label must not start a run
        self.assertRegex(self.ci, r"  linux:\n    needs: changes\n")

    def test_skipped_events_never_cancel_a_running_validation(self):
        conc = self.ci[self.ci.index("concurrency:"):self.ci.index("jobs:")]
        self.assertIn("format('ci-{0}', github.ref)", conc)
        self.assertIn("format('ci-skip-{0}', github.run_id)", conc)     # unique group for events that run nothing
        gate = "contains(github.event.pull_request.labels.*.name, 'ci')"
        self.assertIn(gate, conc)
        self.assertIn("github.event.label.name == 'ci'", conc)

    def test_docs_only_changes_skip_the_test_steps_but_keep_the_check(self):
        linux = self.ci[self.ci.index("\n  linux:\n"):self.ci.index("\n  windows:\n")]
        for step in ("Old release source", "Install", "Browser for the UI tests", "Tests"):
            block = linux[linux.index(f"- name: {step}"):]
            self.assertTrue(block.split("\n")[1].strip() == "if: needs.changes.outputs.code == 'true'", step)
        self.assertIn("No data files or secrets", linux)               # the secrets check always runs
        self.assertNotIn("if:", linux[linux.index("No data files"):linux.index("Old release source")])

    def test_windows_runs_only_where_it_matters(self):
        win = self.ci[self.ci.index("\n  windows:\n"):]
        self.assertIn("needs.changes.outputs.windows == 'true'", win)
        for path in ("app/printing/", "installer/", "scripts/windows/", "app/migrations/", "requirements"):
            self.assertIn(path, self.ci)

    def test_runner_images_are_pinned(self):
        for f in WF.glob("*.yml"):
            text = f.read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"runs-on:\s*\S*-latest", text), [], f.name)


if __name__ == "__main__":
    unittest.main()
