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
# A commit can head more than one PR, so match the branch too, take the run
# GitHub attributes to this PR, and never touch one attributed to another.
runs=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/$WORKFLOW/runs?event=pull_request&head_sha=$head_sha&branch=$head_branch&per_page=20" \
  --jq .workflow_runs)
mine=$(jq -c --argjson pr "$PR" '[.[] | select(.pull_requests | map(.number) | index($pr))]' <<<"$runs")
run=$(jq -c 'first // empty' <<<"$mine")

if [ -z "$run" ]; then
  # Forks arrive with no PR attribution, so bind from the PR side instead: if
  # this commit belongs to no other pull request, a run on it cannot be
  # another PR's. Counting unattributed runs would not establish that.
  others=$(gh api "repos/$GITHUB_REPOSITORY/commits/$head_sha/pulls" \
    --jq "[.[] | select(.number != $PR)] | length" 2>/dev/null || echo unknown)
  unattributed=$(jq -c '[.[] | select(.pull_requests | length == 0)]' <<<"$runs")
  if [ "$others" = "0" ] && [ "$(jq length <<<"$unattributed")" = "1" ]; then
    run=$(jq -c '.[0]' <<<"$unattributed")
  fi
fi

if [ -z "$run" ]; then
  say "No app check run for \`${head_sha:0:7}\` could be matched to this PR — push a commit to start a fresh one."
  exit 0
fi

if [ "$(jq -r .status <<<"$run")" != "completed" ]; then
  say "An app check for \`${head_sha:0:7}\` is already running."
  exit 0
fi

gh run rerun "$(jq -r .id <<<"$run")"
react rocket
echo "Re-ran the app check for $head_sha."
