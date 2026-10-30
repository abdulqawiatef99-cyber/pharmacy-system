"""Offline tests: these do not execute gh, an agent, CI or any network call."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.agent_handoff import GitHub, REPO, checkpoint, claim, eligible, validate


def empty():
    return {"version": 1, "active": None, "runs": {}}


def reserved():
    return claim(empty(), 900, "codex", "test_run_001", "codex/900-doc-test")


def issue():
    return dict(number=900, state="open", labels=[dict(name="agent:ready")],
                assignees=[], body="Synthetic task\n<!-- agent-dependencies: [] -->")


class MemoryGitHub(GitHub):
    """Fake Git graph with GitHub's create-only/non-force ref semantics."""
    def __init__(self):
        self.head = None
        self.parents = {}
        self.calls = []

    def api(self, method, path, body=None, missing_ok=False):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "GET" and path == "git/ref/heads/develop":
            return {"object": {"sha": "develop-tip"}}
        if path == "git/trees":
            return {"sha": "tree"}
        if path == "git/commits":
            sha = f"commit-{len(self.parents)}"
            self.parents[sha] = body["parents"]
            return {"sha": sha}
        if method == "POST" and path == "git/refs":
            if self.head is not None:
                raise RuntimeError("create race")
            self.head = body["sha"]
            return {}
        if method == "PATCH":
            if body["force"] or self.parents[body["sha"]] != [self.head]:
                raise RuntimeError("non-fast-forward")
            self.head = body["sha"]
            return {}
        raise AssertionError((method, path))


class ClaimTests(unittest.TestCase):
    def test_exact_replay_does_not_restart(self):
        state = reserved()
        self.assertEqual(claim(state, 900, "codex", "test_run_001", "codex/900-doc-test"), state)

    def test_occupied_by_other_run_or_issue(self):
        with self.assertRaises(ValueError):
            claim(reserved(), 901, "claude", "other_run_001", "claude/901-doc-test")

    def test_run_id_bound_to_identity(self):
        with self.assertRaises(ValueError):
            claim(reserved(), 901, "codex", "test_run_001", "codex/901-doc-test")

    def test_protected_or_unrelated_branch_rejected(self):
        for branch in ["main", "develop", "codex/901-test", "codex/900-../x", "claude/900-test"]:
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                claim(empty(), 900, "codex", "test_run_001", branch)

    def test_malformed_state_fails_closed(self):
        for state in [{}, {"version": 1, "active": "missing", "runs": {}},
                      {"version": 1, "active": None, "runs": reserved()["runs"]}]:
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate(state)

    def test_blocked_retains_lock(self):
        state = checkpoint(reserved(), "test_run_001", "codex", "blocked", "Next: reconcile", [])
        with self.assertRaises(ValueError):
            claim(state, 901, "claude", "other_run_001", "claude/901-test")
        with self.assertRaises(ValueError):
            checkpoint(state, "test_run_001", "codex", "running", "Resume", [])

    def test_malformed_events_and_absent_active_rejected(self):
        for events in [None, "bad", {}, ["bad"], [{"phase": "running", "note": "Next"}],
                       [{"phase": "running", "note": "Next", "evidence": ["bad-url"]}]]:
            state = reserved()
            state["runs"]["test_run_001"]["events"] = events
            with self.subTest(events=events), self.assertRaises(ValueError):
                validate(state)
        state = empty()
        del state["active"]
        with self.assertRaises(ValueError):
            validate(state)

    def test_last_event_replay_at_capacity(self):
        state = reserved()
        for n in range(50):
            state = checkpoint(state, "test_run_001", "codex", "running", f"Next: {n}", [])
        self.assertEqual(checkpoint(state, "test_run_001", "codex", "running", "Next: 49", []), state)
        with self.assertRaises(ValueError):
            checkpoint(state, "test_run_001", "codex", "running", "New checkpoint", [])

    def test_wrong_owner_and_illegal_transition(self):
        with self.assertRaises(ValueError):
            checkpoint(reserved(), "test_run_001", "claude", "running", "Next", [])
        with self.assertRaises(ValueError):
            checkpoint(reserved(), "test_run_001", "codex", "review", "Next", [])

    def test_review_requires_evidence_and_does_not_release(self):
        state = checkpoint(reserved(), "test_run_001", "codex", "running", "Next: tests", [])
        with self.assertRaises(ValueError):
            checkpoint(state, "test_run_001", "codex", "review", "Next: review", [])
        with self.assertRaises(ValueError):
            checkpoint(state, "test_run_001", "codex", "review", "Next: review", ["https://example.com"])
        state = checkpoint(state, "test_run_001", "codex", "review", "Next: review", [f"https://github.com/{REPO}/pull/999"])
        self.assertEqual(state["active"], "test_run_001")
        with self.assertRaises(ValueError):
            checkpoint(state, "test_run_001", "codex", "released", "CI green", [])

    def test_checkpoint_replay_is_idempotent(self):
        state = checkpoint(reserved(), "test_run_001", "codex", "running", "Next: tests", [])
        self.assertEqual(checkpoint(state, "test_run_001", "codex", "running", "Next: tests", []), state)

    def test_released_run_replay_does_not_reopen(self):
        state = reserved()
        state["active"] = None
        state["runs"]["test_run_001"]["phase"] = "released"
        self.assertEqual(claim(state, 900, "codex", "test_run_001", "codex/900-doc-test"), state)


class EligibilityTests(unittest.TestCase):
    def test_opt_in_with_explicit_empty_dependencies(self):
        eligible(issue(), {}, [])

    def test_owner_sensitive_closed_assigned_or_missing_manifest(self):
        variants = [dict(state="closed"), dict(assignees=[{"login": "worker"}]), dict(body="No manifest"),
                    dict(labels=[]), dict(labels=[dict(name="agent:ready"), dict(name="owner-action")]),
                    dict(labels=[dict(name="agent:ready"), dict(name="launch-critical")])]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                eligible(dict(issue(), **changes), {}, [])

    def test_unresolved_dependency_and_missing_fetch(self):
        task = dict(issue(), body="<!-- agent-dependencies: [899] -->")
        for deps in [{}, {899: {"state": "open"}}]:
            with self.assertRaises(ValueError):
                eligible(task, deps, [])
        eligible(task, {899: {"state": "closed"}}, [])

    def test_overlap_and_truncated_pr_page(self):
        for prs in [[dict(head={"ref": "codex/900-other"}, body="")],
                    [dict(head={"ref": "other"}, body="Closes #900")], [{}] * 100]:
            with self.assertRaises(ValueError):
                eligible(issue(), {}, prs)


class TransportTests(unittest.TestCase):
    def test_initial_claim_race_one_winner(self):
        store = MemoryGitHub()
        winner = store.write(None, reserved())
        with self.assertRaises(RuntimeError):
            store.write(None, reserved())
        self.assertEqual(store.head, winner)

    def test_sibling_updates_one_winner(self):
        store = MemoryGitHub()
        old = store.write(None, reserved())
        running = checkpoint(reserved(), "test_run_001", "codex", "running", "Next", [])
        winner = store.write(old, running)
        with self.assertRaises(RuntimeError):
            store.write(old, reserved())
        self.assertEqual(store.head, winner)

    def test_only_coordination_ref_written_no_force_or_dispatch(self):
        store = MemoryGitHub()
        first = store.write(None, reserved())
        store.write(first, reserved())
        writes = [(path, body) for method, path, body in store.calls if method in {"POST", "PATCH"}]
        self.assertTrue(all(path in {"git/trees", "git/commits", "git/refs", "git/refs/heads/coordination/active"} for path, _ in writes))
        self.assertFalse(writes[-1][1]["force"])

    @patch("scripts.agent_handoff.subprocess.run")
    def test_ambiguous_timeout_is_not_retried(self, run):
        run.side_effect = subprocess.TimeoutExpired(["gh"], 30)
        with self.assertRaises(RuntimeError):
            GitHub().api("PATCH", "git/refs/heads/coordination/active", {"sha": "fake"})
        self.assertEqual(run.call_count, 1)

    @patch("scripts.agent_handoff.subprocess.run")
    def test_auth_failure_is_not_absent_or_retried(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "HTTP 401 sensitive diagnostic")
        with self.assertRaises(RuntimeError) as error:
            GitHub().api("GET", "git/ref/heads/coordination/active", missing_ok=True)
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(run.call_count, 1)

    @patch("scripts.agent_handoff.subprocess.run")
    def test_json_stdin_without_shell(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
        body = {"message": "literal $(unsafe) `text`"}
        GitHub().api("POST", "git/commits", body)
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(json.loads(kwargs["input"]), body)


if __name__ == "__main__":
    unittest.main()
