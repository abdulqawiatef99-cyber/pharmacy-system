"""Manual, cooperative GitHub claim journal. Never launches an agent or workflow."""
import argparse
import base64
import copy
import json
import re
import subprocess
import sys

REPO = "abdulqawiatef99-cyber/pharmacy-system"
REF = "coordination/active"
PATH = "state.json"
PHASES = {"claimed", "running", "review", "blocked"}


def validate(state):
    if (not isinstance(state, dict) or type(state.get("version")) is not int
            or state.get("version") != 1 or "active" not in state):
        raise ValueError("Unsupported journal")
    runs = state.get("runs")
    if not isinstance(runs, dict) or len(runs) > 100:
        raise ValueError("Invalid journal or journal capacity reached")
    active = state.get("active")
    if active is not None and active not in runs:
        raise ValueError("Missing active receipt")
    for key, run in runs.items():
        if not isinstance(run, dict) or run.get("phase") not in PHASES | {"released"}:
            raise ValueError("Invalid receipt")
        identity(run.get("issue"), run.get("agent"), key, run.get("branch"))
        if (key == active) != (run["phase"] != "released"):
            raise ValueError("Inconsistent active claim")
        events = run.get("events")
        if not isinstance(events, list) or len(events) > 50:
            raise ValueError("Invalid bounded event history")
        for event in events:
            if (not isinstance(event, dict) or event.get("phase") not in PHASES | {"released"}
                    or not isinstance(event.get("note"), str) or not event["note"].strip()
                    or len(event["note"]) > 2000):
                raise ValueError("Invalid checkpoint event")
            evidence_links(event.get("evidence"))
    return state


def identity(issue, agent, run_id, branch):
    if type(issue) is not int or issue <= 0 or agent not in {"claude", "codex", "chatgpt"}:
        raise ValueError("Invalid Issue or agent")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", run_id):
        raise ValueError("Invalid run ID")
    if not isinstance(branch, str) or not re.fullmatch(
        rf"{agent}/{issue}-[a-z0-9]+(?:-[a-z0-9]+)*", branch
    ):
        raise ValueError("Use an isolated agent/issue-slug branch")


def eligible(issue, dependencies, prs):
    labels = {x["name"] for x in issue.get("labels", [])}
    if issue.get("state") != "open" or "pull_request" in issue:
        raise ValueError("Expected an open Issue")
    if "agent:ready" not in labels or labels & {"owner-action", "launch-critical"}:
        raise ValueError("Not explicitly eligible for this limited pilot")
    if issue.get("assignees"):
        raise ValueError("Issue already assigned; reconcile manually")
    marker = re.findall(r"<!-- agent-dependencies: (\[[^\n]*\]) -->", issue.get("body") or "")
    if len(marker) != 1:
        raise ValueError("Missing explicit dependency manifest")
    ids = json.loads(marker[0])
    if not isinstance(ids, list) or len(ids) > 5 or any(type(x) is not int or x <= 0 for x in ids):
        raise ValueError("Invalid dependency manifest")
    if set(ids) != set(dependencies) or any(x.get("state") != "closed" for x in dependencies.values()):
        raise ValueError("Unresolved or unchecked dependency")
    if len(prs) >= 100:
        raise ValueError("PR snapshot may be truncated")
    for pr in prs:
        if (re.search(rf"(?:^|/){issue['number']}-", pr["head"]["ref"])
                or re.search(rf"(?<!\w)#{issue['number']}(?!\d)", pr.get("body") or "")):
            raise ValueError("Potential overlapping PR; reconcile manually")


def claim(state, issue, agent, run_id, branch):
    validate(state)
    identity(issue, agent, run_id, branch)
    prior = state["runs"].get(run_id)
    if prior:
        if (prior["issue"], prior["agent"], prior["branch"]) != (issue, agent, branch):
            raise ValueError("Run ID reused for a different identity")
        return copy.deepcopy(state)  # Replay never reopens released work.
    if state["active"] is not None or len(state["runs"]) >= 100:
        raise ValueError("Claim occupied or journal full; no automatic takeover")
    result = copy.deepcopy(state)
    result["active"] = run_id
    result["runs"][run_id] = dict(issue=issue, agent=agent, branch=branch, phase="claimed", events=[])
    return result


def evidence_links(evidence):
    if not isinstance(evidence, list) or len(evidence) > 10 or any(
        not isinstance(url, str) or not re.fullmatch(
            rf"https://github\.com/{re.escape(REPO)}/(?:pull/\d+|commit/[a-f0-9]{{40}}|issues/\d+(?:#issuecomment-\d+)?|actions/runs/\d+)", url
        ) for url in evidence
    ):
        raise ValueError("Use bounded evidence links in this repository")


def checkpoint(state, run_id, agent, phase, note, evidence):
    validate(state)
    if state["active"] != run_id or state["runs"][run_id]["agent"] != agent:
        raise ValueError("Not the active claim")
    old = state["runs"][run_id]["phase"]
    allowed = {"claimed": {"running", "blocked"}, "running": {"running", "review", "blocked"},
               "review": {"review", "blocked"}, "blocked": {"blocked"}}
    if phase not in allowed[old] or not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise ValueError("Invalid transition or missing checkpoint/next step")
    if phase == "review" and not evidence:
        raise ValueError("Review requires a commit or PR evidence URL")
    evidence_links(evidence)
    result = copy.deepcopy(state)
    run = result["runs"][run_id]
    event = dict(phase=phase, note=note, evidence=evidence)
    if run["events"] and run["events"][-1] == event:
        return result
    if len(run["events"]) >= 50:
        raise ValueError("Receipt event limit reached; reconcile manually")
    run["phase"] = phase
    run["events"].append(event)
    return result


class GitHub:
    """Existing gh auth only. No installation, login, token discovery or retries."""
    def api(self, method, path, body=None, missing_ok=False):
        args = ["gh", "api", "--hostname", "github.com", "--method", method,
                f"repos/{REPO}/{path}"]
        if body is not None:
            args += ["--input", "-"]
        try:
            p = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                               text=True, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Transport unavailable/outcome unknown; inspect remote state before retry") from exc
        if p.returncode:
            if missing_ok and "(HTTP 404)" in p.stderr:
                return None
            raise RuntimeError("GitHub call failed; do not retry writes blindly or print credential diagnostics")
        return json.loads(p.stdout)

    def read(self):
        ref = self.api("GET", f"git/ref/heads/{REF}", missing_ok=True)
        if ref is None:
            return None, {"version": 1, "active": None, "runs": {}}
        head = ref["object"]["sha"]
        # Pin content to the observed head, never to a moving ref.
        file = self.api("GET", f"contents/{PATH}?ref={head}")
        if file.get("size", 0) > 262144 or file.get("encoding") != "base64":
            raise ValueError("Invalid journal file")
        return head, validate(json.loads(base64.b64decode(file["content"])))

    def write(self, observed, state):
        validate(state)
        content = json.dumps(state, sort_keys=True, indent=2) + "\n"
        if len(content.encode()) > 262144:
            raise ValueError("Journal size limit reached")
        parent = observed or self.api("GET", "git/ref/heads/develop")["object"]["sha"]
        # This is a state-only branch: never merge it into develop or main.
        tree = self.api("POST", "git/trees", {"tree": [dict(path=PATH, mode="100644", type="blob", content=content)]})
        commit = self.api("POST", "git/commits", dict(message="Record cooperative agent checkpoint",
                                                     tree=tree["sha"], parents=[parent]))
        if observed is None:
            self.api("POST", "git/refs", dict(ref=f"refs/heads/{REF}", sha=commit["sha"]))
        else:
            # Sibling commits lose the non-force fast-forward race; never force or rebase here.
            self.api("PATCH", f"git/refs/heads/{REF}", dict(sha=commit["sha"], force=False))
        return commit["sha"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "claim", "checkpoint"])
    parser.add_argument("--issue", type=int)
    parser.add_argument("--agent", choices=["claude", "codex", "chatgpt"])
    parser.add_argument("--run-id")
    parser.add_argument("--branch")
    parser.add_argument("--phase", choices=sorted(PHASES))
    parser.add_argument("--note", default="")
    parser.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args()
    github = GitHub()
    head, state = github.read()
    if args.command == "status":
        print(json.dumps(dict(head=head, state=state), indent=2))
        return
    if args.command == "claim":
        new = claim(state, args.issue, args.agent, args.run_id, args.branch)
        if new != state:
            issue = github.api("GET", f"issues/{args.issue}")
            marker = re.findall(r"<!-- agent-dependencies: (\[[^\n]*\]) -->", issue.get("body") or "")
            ids = json.loads(marker[0]) if len(marker) == 1 else None
            if not isinstance(ids, list) or len(ids) > 5 or any(type(n) is not int or n <= 0 for n in ids):
                raise ValueError("Explicit bounded dependency manifest required")
            dependencies = {n: github.api("GET", f"issues/{n}") for n in ids}
            prs = github.api("GET", "pulls?state=open&per_page=100")
            eligible(issue, dependencies, prs)
    else:
        new = checkpoint(state, args.run_id, args.agent, args.phase, args.note, args.evidence)
    if new == state:
        print("Idempotent replay; no write or dispatch")
    else:
        print("Checkpoint commit:", github.write(head, new))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        sys.exit(1)
