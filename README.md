# Marketplace

This is the marketplace for [Frappe](https://frappeframework.com) apps — a
community-editable registry of installable apps, consumed by
[`bench get-app`](https://github.com/frappe/pilot) and the Pilot admin UI's
Marketplace page.

## What's here

- `apps.json` — the index: one metadata entry per app, pointing at that app's
  release file.
- `apps/<name>.json` — one app's `releases`: each an immutable
  `branch` + `commit` pair with the app version, Frappe compatibility range,
  and dependencies on other marketplace apps.
- `validation/` — the PR-gating checks: `check.py` runs `schema_check.py`
  per changed app, then both `semgrep_check.py` and `get_app_check.py`
  against each changed release, whatever the other's outcome. `validation/utils/` holds
  their shared plumbing (cloning a release at its commit, diffing two
  registry revisions).
- `tools/` — maintenance, not CI-gated. `add_release.py` appends a release
  from an app checkout; `migrate_registry.py` was the one-shot split of the
  old branch-scoped `apps.json`.
- `.github/workflows/marketplace-app-check.yml` — validates every PR that
  touches the registry.
- `.github/workflows/publish-release.yml` — reusable workflow app owners call
  from their repo to open a release PR here.

## How publishing works

Releases are **commit-scoped**: a release names one immutable commit, and the
checks run against that commit. Pushing to a branch publishes nothing — there
is no automation in this repo that advertises new commits on its own. Every
new version, nightly builds included, lands through a PR that the marketplace
checks gate.

Wire up your own automation with the reusable workflow, from your app repo:

```yaml
jobs:
  publish:
    uses: frappe/marketplace/.github/workflows/publish-release.yml@main
    with:
      app: helpdesk
      branch: main
      channel: stable   # or: nightly, for a develop build
    secrets:
      registry_token: ${{ secrets.MARKETPLACE_PR_TOKEN }}
```

It reads `version`, `frappe_core` and `dependencies` from your
`pyproject.toml` at that commit, appends the release to `apps/<app>.json`, and
opens the PR. To do it by hand, run `tools/add_release.py` (or edit the file)
and open the PR yourself.

## Contributing an app

Add a metadata entry to `apps.json`, add `apps/<name>.json` with at least one
release, and open a PR.

### Entry format

```json
{
  "name": "helpdesk",
  "title": "Helpdesk",
  "description": "Well designed, open source ticketing system",
  "repo": "https://github.com/frappe/helpdesk",
  "logo_url": "https://cloud.frappe.io/files/helpdesk (1).png",
  "website": "https://frappe.io/helpdesk",
  "documentation": "https://docs.frappe.io/helpdesk",
  "categories": ["Featured", "Support"],
  "category": "Applications",
  "stars": 3184,
  "releases": "apps/helpdesk.json"
}
```

`apps/helpdesk.json`:

```json
{
  "name": "helpdesk",
  "releases": [
    {
      "version": "1.27.0",
      "branch": "main",
      "commit": "b7d3e0f1a2c45689ab0cd12ef3456789abcdef01",
      "frappe_core": ">=15.109.0,<17.0.0",
      "dependencies": {
        "telephony": ">=0.0.1,<1.0.0"
      },
      "channel": "stable"
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `name` | Yes | Unique snake_case identifier |
| `title` | Yes | Human-readable display name |
| `description` | Yes | Short description shown in the marketplace UI |
| `repo` | Yes | Public GitHub repo URL |
| `logo_url` | No | Direct URL to a square PNG/SVG logo |
| `website` | No | App or project homepage |
| `documentation` | No | Docs URL |
| `categories` | No | Tags shown in the UI (e.g. `Featured`) |
| `category` | Yes | One of: `Applications`, `Compliance`, `Developer Tools`, `Extensions`, `Integrations`, `Utilities` |
| `stars` | No | Kept in sync by `tools/fetch_stars.py` — leave as `0`/omit |
| `releases` | Yes | Must be exactly `apps/<name>.json` — the only path pilot reads |

Each entry in that file's `releases`:

| Field | Required | Notes |
|---|---|---|
| `version` | Yes | App version at this commit — must match the version the commit declares |
| `branch` | Yes | Branch the commit is on; pilot tracks it for later updates |
| `commit` | Yes | Full 40-character SHA, reachable from `branch` |
| `frappe_core` | Yes | Frappe version range this release requires, e.g. `>=15.0.0,<17.0.0` — must come from `[tool.bench.frappe-dependencies].frappe` in the app's `pyproject.toml`; releases without it are rejected |
| `dependencies` | Yes | Other marketplace apps this release requires, as `{name: version-range}` — `{}` is fine, but the key must exist |
| `channel` | No | `stable` (default) or `nightly`. Pilot installs the newest compatible `stable` release, and only falls back to `nightly` when no stable release fits the bench's Frappe version |

`channel` describes the **code line**, not the branch name. Use `nightly` for a
rolling dev branch that exists *alongside* cut release branches — `develop` in an
app that also ships `version-15`/`version-16`/`main`. An app whose only branch is
`develop` (telephony, and most small integration apps) publishes the one line
every bench runs, so its release is `stable`; marking it nightly would make every
production bench look like it fell back to a dev build.

One release per branch is the norm. A branch cannot carry two releases with the
same `version`, so an app whose version never changes (telephony sits at `0.0.1`)
advertises a new commit by **editing its single release entry in place** rather
than appending one. The registry's own git history is the record of what was
advertised when.

## What CI checks

When your PR is opened, CI clones your repo at each changed release's
`commit` — rejecting a commit that is not reachable from the declared
`branch`, a version or `frappe_core` that disagrees with the commit's
`pyproject.toml`, and anything short of a full SHA — then runs two checks,
both always, so one failing never hides the other's findings:

1. **Semgrep scan.** **Blocking** findings (which fail the PR) include:
   - **Code injection** — `eval()`, `exec()`, `compile()`, `safe_eval()`
   - **Template injection** — `render_template` with dynamic input, direct `jinja2.Environment` / `Template` construction
   - **SQL injection** — f-strings or `.format()` inside `frappe.db.sql()`
   - **Command execution** — `subprocess` with `shell=True`, `os.system`, `execute_in_shell`
   - **Authorization bypass** — `ignore_permissions=True` in whitelist methods, `frappe.set_user`
   - **Multitenancy violations** — module-level globals, `redis.set`/`redis.get` without scoping

   Non-blocking findings (WARNING severity) are reported but do not prevent
   merge — a Frappe reviewer will note them in the PR.

2. **get-app validator**. Runs
   [Pilot](https://github.com/frappe/pilot)'s own get-app on your release, at
   its advertised commit, into a real bench on the Frappe version your
   `frappe_core` advertises. That is Pilot's app validation, the install into
   the bench and your assets: the prebuilt archive your CI publishes for the
   commit (on the GitHub release `assets-<branch>`, as
   `<app>-<commit>.tar.gz` with a `.sha256`) when there is one, else a build.
   Catches broken repo structure, syntax errors, undeclared
   `[tool.bench.frappe-dependencies]`/`required_apps` mismatches, Frappe
   version mismatches, and missing imports/dependencies that only show up
   once the app is actually installed. An asset build must fit in 1 GiB —
   see [Memory limit](#memory-limit).

### Memory limit

Marketplace requires apps with heavy builds to publish prebuilt assets for
each release commit, for a faster install experience. CI builds your assets
under a **1 GiB** cap (`[build] memory_limit_mb` in Pilot's `bench.toml`,
enforced with `systemd-run`, no swap); a build that needs more is a
**blocking** finding (`get-app-memory`). Publishing prebuilt assets skips the
build: a GitHub release `assets-<branch>` with `<app>-<commit>.tar.gz` and
its `.sha256`, as
[Pilot's app assets workflow](https://github.com/frappe/pilot/blob/develop/.github/workflows/app-assets.yml)
publishes.

### Reading the results

CI posts a report as a comment on your PR — one comment, edited in place on
each push, stamped with when it last ran — and repeats it in the workflow
run's summary. It lists every
finding grouped by rule, with severity and `file:line` locations, so you
shouldn't need to open the raw job log.

Severities are `Critical`, `Major`, `Minor` and `Info`. **Critical and Major
block the merge**; Minor and Info are advisory and are listed under a
collapsed "advisory findings" section.

PRs from forks get a read-only token, so the comment can't be posted there —
the run summary carries the same report.

### Re-running the checks

Fixed something in your app's repo? Comment `/rereview` — on its own, as the
whole comment — and the checks re-run against your repo's current code. No new
commit is needed here, since the registry hasn't changed. The PR author and
anyone with write access can use it.

The comment gets a 🚀 once the checks restart. It only works after the first
report has been posted, and a second `/rereview` while a run is still going
is ignored.
