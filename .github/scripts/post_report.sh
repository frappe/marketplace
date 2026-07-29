#!/usr/bin/env bash
# Publish the app check report: to the run summary, and as a single PR comment
# that later runs edit in place.
#
# Usage: post_report.sh <report.md>
# Env:   PR, HEAD_SHA, GITHUB_REPOSITORY, GITHUB_TOKEN, GITHUB_STEP_SUMMARY

set -euo pipefail

REPORT=${1:?usage: post_report.sh <report.md>}
MARKER='<!-- marketplace-app-check'

if [ ! -f "$REPORT" ]; then
  echo "No report written — the check crashed before scanning."
  exit 0
fi

# Per-run, so always safe to write.
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$REPORT" >> "$GITHUB_STEP_SUMMARY"
fi

# The last comment carrying the marker is this check's own. Matching on it
# rather than "the bot's last comment" avoids overwriting an unrelated one.
existing_comment() {
  gh api "repos/$GITHUB_REPOSITORY/issues/$PR/comments" --paginate \
    --jq ".[] | select(.body | startswith(\"$MARKER\")) | \"\(.id) \(.body | split(\"\n\")[0])\"" |
    tail -1
}

# Whether this run may write is decided by ancestry rather than by timing, so
# it cannot go stale between the check and the write: the commit a report
# describes is recorded in its marker.
may_write() {
  local posted=$1
  [ -z "$posted" ] && return 0                                    # pre-marker comment
  [ "$posted" = "$HEAD_SHA" ] && return 0
  git fetch --no-tags --quiet origin "$posted" 2>/dev/null || true
  git merge-base --is-ancestor "$posted" "$HEAD_SHA" 2>/dev/null && return 0
  git merge-base --is-ancestor "$HEAD_SHA" "$posted" 2>/dev/null && return 1
  # Neither descends from the other: the branch was rebased or force pushed, or
  # this run is for a commit since rewritten away. Whoever is the tip now owns
  # the report — otherwise a rewritten head would leave old findings up for good.
  [ "$(gh pr view "$PR" --json headRefOid --jq .headRefOid)" = "$HEAD_SHA" ]
}

existing=$(existing_comment)
comment_id=${existing%% *}
posted_sha=$(printf '%s' "$existing" | sed -n "s/.*marketplace-app-check \([0-9a-f]\{40\}\) -->.*/\1/p")

if ! may_write "$posted_sha"; then
  echo "A report for $posted_sha already covers this branch; leaving it alone."
  exit 0
fi

jq -Rs '{body: .}' "$REPORT" > /tmp/comment.json

# Fork PRs get a read-only token; the run summary is the fallback there.
if [ -n "$comment_id" ]; then
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$comment_id" \
    --input /tmp/comment.json --silent ||
    echo "Could not update the PR comment (expected for forks); see the job summary."
else
  gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR/comments" \
    --input /tmp/comment.json --silent ||
    echo "Could not comment on the PR (expected for forks); see the job summary."
fi
