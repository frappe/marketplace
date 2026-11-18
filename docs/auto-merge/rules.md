# Auto-merge rules

A registry PR merges on its own once the marketplace check passes, if it only makes changes the check can vouch for. Anyone can open it, for any app. Everything else waits for a reviewer.

## When does a PR merge on its own?

When the Marketplace App Check passes on the PR's latest commit and the PR only:

- adds a new app (its `apps.json` entry and `apps/<app>.json`),
- adds releases to a listed app, or
- re-advertises a release's version and branch at a newer commit.

## When is it held for review?

The PR waits for a reviewer, with the `held-for-review` label, when its check passed but it:

- changes a file other than `apps.json` and `apps/<app>.json`;
- changes any field of a listed app's `apps.json` entry, such as `repo`, `website`, `logo_url`, `categories`, `stars` or `developer_contact`;
- adds a new app that lists itself under `Featured`;
- removes an app, or lists one twice;
- advertises a commit that is not ahead of the release already advertised on that branch, or comes from a repo outside GitHub;
- edits a release in place or removes one;
- deletes an `apps/<app>.json`, or adds one that no app points to;
- conflicts with `main`, or GitHub refuses the merge.

The check validates only new releases. Metadata, removals and edits pass it trivially, so a person reviews them.

## Why does it hold those?

Each one is something the check cannot prove safe. A changed `repo` could point an app at someone else's code, an older commit could quietly roll every bench back, and metadata such as a website or logo is shown to every user.

## My PR is labelled `held-for-review`. What now?

Open the latest **Auto-merge Registry** run in the Actions tab; its log names each reason. Either change the PR so none applies, or wait for a reviewer to merge it by hand.

## My check failed. Will it get the label?

No. The auto-merge only runs after a passing check. Fix the failure first; see [check failures](../marketplace-check/failures.md).

## What about drafts, and PRs I push to again?

Drafts are skipped until marked ready. If you push after the check passed, nothing merges until the new commit's own check passes.

## Can a PR change these rules?

No. The auto-merge always runs the workflow and rules from `main` (`.github/workflows/auto-merge-registry.yml` and `validation/auto_merge.py`), never from the PR, and any PR touching them is held anyway.
