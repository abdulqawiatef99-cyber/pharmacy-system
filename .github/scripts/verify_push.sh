#!/usr/bin/env bash
# Free substitute for branch protection (not available on a private repo on GitHub Free).
# Verifies that a push to main/develop came from a merged PR whose head passed "CI / linux".
# Exit 0 = verified; exit 1 = not verified (reason on stdout). Never changes anything.
# Env: REPO, BRANCH, SHA (pushed commit), FORCED ("true"/"false"), GH_TOKEN.
set -uo pipefail

if [ "${FORCED:-false}" = "true" ]; then
  echo "force push to $BRANCH"; exit 1
fi

pr=""
for attempt in 1 2 3 4 5 6; do   # the commit->PR link can lag a few seconds after a merge
  pr=$(gh api "repos/$REPO/commits/$SHA/pulls" --jq \
    "[.[] | select(.merged_at != null and .base.ref == \"$BRANCH\" and .merge_commit_sha == \"$SHA\")][0] | select(. != null) | \"\(.number) \(.head.sha)\"") || pr=""
  [ -n "$pr" ] && break
  sleep 10
done
if [ -z "$pr" ]; then
  echo "direct push to $BRANCH ($SHA) - not the merge of a pull request"; exit 1
fi

number=${pr%% *}; head=${pr##* }
ok=$(gh api "repos/$REPO/commits/$head/check-runs?check_name=linux&per_page=100" --jq \
  '[.check_runs[] | select(.app.slug == "github-actions" and .conclusion == "success")] | length') || ok=0
if [ "${ok:-0}" -lt 1 ]; then
  echo "PR #$number was merged into $BRANCH without a green \"CI / linux\" on its head $head"; exit 1
fi
echo "verified: PR #$number, CI / linux green on $head"
