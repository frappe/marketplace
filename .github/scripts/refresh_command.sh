#!/usr/bin/env bash
# Re-run the app check for a PR, in response to a /refresh comment.
# Usage: refresh_command.sh
# Env:   PR, COMMENT_ID, ASSOCIATION, COMMENTER, GITHUB_REPOSITORY, GITHUB_TOKEN

set -euo pipefail

WORKFLOW=marketplace-app-check.yml

react() {
  gh api "repos/$GITHUB_REPOSITORY/issues/comments/$COMMENT_ID/reactions" \
    -f content="$1" --silent 2>/dev/null || true
}

say() {
  gh pr comment "$PR" --body "$1" >/dev/null 2>&1 || echo "$1"
}

pr=$(gh pr view "$PR" --json headRefOid,author)
head_sha=$(jq -r .headRefOid <<<"$pr")

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
run=$(gh run list --workflow "$WORKFLOW" --json databaseId,headSha,status --limit 50 \
  --jq "[.[] | select(.headSha == \"$head_sha\")] | first")

if [ -z "$run" ] || [ "$run" = "null" ]; then
  say "No app check run found for \`${head_sha:0:7}\` to refresh — push a commit to start one."
  exit 0
fi

if [ "$(jq -r .status <<<"$run")" != "completed" ]; then
  say "An app check for \`${head_sha:0:7}\` is already running."
  exit 0
fi

gh run rerun "$(jq -r .databaseId <<<"$run")"
react rocket
echo "Re-ran the app check for $head_sha."
