# How the marketplace check works

Every PR runs the **Marketplace App Check**. When the PR changes `apps.json` or `apps/<app>.json`, the check validates each app and release the PR adds or changes. Other PRs pass straight away.

## What happens, step by step

1. **Is the PR up to date?** It must contain the latest `main`, because dependency checks read the registry on `main`.
2. **Schema.** Each changed app's entry and releases file must have every required field, and `releases` must be `apps/<name>.json`.
3. **Clone at the exact commit.** Each new release is cloned at its `commit`. The commit must be reachable from the release's `branch`, so a fork or an unmerged PR commit is rejected.
4. **Truthful metadata.** `version` and `frappe_core` must match what the commit's `pyproject.toml` declares.
5. **Semgrep scan.** The code is scanned for security and multi-tenancy problems, such as `eval`, SQL built with f-strings, `shell=True`, or `ignore_permissions=True` in whitelisted methods. A new release on a branch that already has one is checked only for findings the advertised commit did not have.
6. **Install on a real bench.** Pilot's own `get-app` installs the release, as it would on a user's server. See below.

Every check runs, even when an earlier one fails, so one report shows everything to fix.

## What is the "bench" the check installs into?

A throwaway Pilot bench, built on the CI runner only to test your app.

- **One bench per Frappe version.** The check reads your `frappe_core` and picks every Frappe major it admits. `>=15.0.0,<17.0.0` tests on `version-15` and `version-16`, and `develop` is used when its dev version fits.
- **Set up like a real one.** It runs `pilot init`'s own steps, on SQLite, with no site and no Admin UI, so it costs only what an install needs.
- **Fresh for every release.** The bench is snapshotted once set up and restored before each release, so one app's install never leaks into the next.
- **Same limits as a small server.** Asset builds run under a 1 GiB memory cap with no swap, enforced by `systemd-run`. A build that needs more fails here, as it would on a small server.

The install is Pilot's full app validation: repo structure, syntax, declared Frappe dependencies, imports, and your assets. The assets come from your prebuilt archive when you publish one, otherwise they are built.

## Where do I see the results?

The check posts one comment on your PR, edited in place on each run, and repeats it in the workflow run's summary. Findings are grouped by rule with `file:line` locations.

- **Critical** and **Major** findings fail the check.
- **Minor** and **Info** findings are advice; they sit in a collapsed section and never block.

PRs from forks get a read-only token, so the comment may not appear there; the run summary has the same report.

## How do I re-run it?

Comment `/rereview`, on its own, on the PR. The PR's author and anyone with write access can use it, once the first report is posted. The check runs again on the same commits, which helps after a network or GitHub failure.

To test new code, publish a new commit instead: the check only ever validates the commit a release names.

Something failed? See [check failures](failures.md).
