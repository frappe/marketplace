# Check failures

A failed marketplace check means one of your app's releases cannot be advertised yet: benches would get code that does not install, does not match its metadata, or has a blocking security finding. The PR stays open until a new run passes.

Read the report comment on the PR (or the run summary for a fork). Each finding names the check, and for install failures the Frappe branch, that produced it. Find the message below.

## "This PR is behind main"

Your branch is missing recent changes on `main`. Merge or rebase `main` into your branch and push. Rolling publish PRs are rebuilt from `main` on the next publish.

## "app missing '…'" or "release 0 missing '…'"

A required field is missing from `apps.json` or `apps/<app>.json`. Add it; see the field tables in the [README](../../README.md#adding-an-app). `categories` must be a non-empty list, `stars` an integer (`0` is fine), and `dependencies` an object (`{}` is fine).

## "app must set 'releases' to 'apps/<name>.json'"

The entry's `releases` must point at exactly `apps/<name>.json`, using the app's `name`.

## "… is not reachable from <repo>@<branch>"

The release's commit is not on its branch, for example a commit from a fork or an unmerged PR. Merge it into the branch first, or advertise a commit that is already there.

## "advertised version … but the commit declares …"

`version` in the release does not match `pyproject.toml` (or `__version__`) at that commit. Copy the version the commit declares, or publish with the reusable workflow, which reads it for you.

## "advertised frappe_core … but the commit declares …"

`frappe_core` must equal `[tool.bench.frappe-dependencies].frappe` in the commit's `pyproject.toml`. Copy it across.

## "No frappe_core declared" or "declares no lower bound"

Declare a Frappe range with a lower bound in `pyproject.toml`, such as `>=15.0.0,<17.0.0`, and publish again.

## "needs Frappe N, which has no version-N branch, and develop … does not fit it"

Your range starts at a Frappe major that is not released yet. To test against `develop`, use a dev floor such as `>=17.0.0-dev`.

## A Semgrep finding marked Critical or Major

The scan found risky code, such as `eval`, SQL built from f-strings, `shell=True`, or `ignore_permissions=True` in a whitelisted method. Fix it in your app's repo and publish the new commit. If you believe it is a false positive, explain why in a PR comment so a reviewer can look.

## "On Frappe version-N: …" install errors

Pilot's `get-app` failed on that Frappe branch: a missing dependency, a syntax or import error, or a Frappe incompatibility. Reproduce it with Pilot on a bench of that version, fix it in your app, and publish the new commit. If only one Frappe version fails, consider narrowing `frappe_core`.

## "the asset build needed more than 1024 MiB"

Your asset build is too heavy for a small server. Publish prebuilt assets for each release commit: a GitHub release `assets-<branch>` with `<app>-<commit>.tar.gz` and its `.sha256`. See [publishing releases](../publishing/releases.md#should-i-publish-prebuilt-assets).

## "Could not read …", "Timed out …", or a crash

Something outside your app failed, such as GitHub or the network. Comment `/rereview` to run the check again.

## I fixed my app. Why does `/rereview` still fail?

A release names one exact commit, and the check validates only that commit. Publish your fix as a new commit; the rolling PR moves to it and the check runs again.
