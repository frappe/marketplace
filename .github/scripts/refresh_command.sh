#!/usr/bin/env bash
# Re-run the app check for a PR, in response to a /refresh comment.
# Usage: refresh_command.sh
# Env:   PR, COMMENT_ID, ASSOCIATION, COMMENTER, BODY, GITHUB_REPOSITORY, GITHUB_TOKEN

set -euo pipefail

WORKFLOW=marketplace-app-check.yml

react() {
  gh api "repos/$GITHUB_REPOSITORY/issues/comments/$COMMENT_ID/reactions" \
    -f content="$1" --silent 2>/dev/null || true
}

say() {
  gh pr comment "$PR" --body "$1" >/dev/null 2>&1 || echo "$1"
}

# Exact match only: "/refresher" and "/refresh rm -rf" are not this command.
command=$(printf '%s' "$BODY" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [ "$command" != "/refresh" ]; then
  echo "Not a /refresh command; ignoring."
  exit 0
fi

pr=$(gh pr view "$PR" --json headRefOid,headRefName,author)
head_sha=$(jq -r .headRefOid <<<"$pr")
head_branch=$(jq -r .headRefName <<<"$pr")

# A re-run spends CI on code the commenter may not own; the PR's own author
# qualifies, since pushing a commit would rerun the checks anyway.
case "$ASSOCIATION" in
  OWNER | MEMBER | COLLABORATOR) ;;
  *)
    if [ "$(jq -r .author.login <<<"$pr")" != "$COMMENTER" ]; then
      echo "Ignoring /refresh from $COMMENTER ($ASSOCIATION)."
      react confused
      exit 0
    fi
    ;;
esac

react eyes
# Scoped to this PR: a commit can be the head of more than one PR, so match
# the branch too, and prefer a run GitHub already associates with this PR.
runs=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/$WORKFLOW/runs?event=pull_request&head_sha=$head_sha&branch=$head_branch&per_page=20" \
  --jq .workflow_runs)
run=$(jq -c --argjson pr "$PR" '
  (map(select(.pull_requests | map(.number) | index($pr))) | first)
  // first // empty' <<<"$runs")

if [ -z "$run" ] || [ "$run" = "null" ]; then
  say "No app check run found for \`${head_sha:0:7}\` to refresh — push a commit to start one."
  exit 0
fi

if [ "$(jq -r .status <<<"$run")" != "completed" ]; then
  say "An app check for \`${head_sha:0:7}\` is already running."
  exit 0
fi

gh run rerun "$(jq -r .id <<<"$run")"
react rocket
echo "Re-ran the app check for $head_sha."
