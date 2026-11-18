# Publishing releases

A release names one exact commit of your app. Pushing to your branch publishes nothing; each new release reaches benches through a PR here.

## How do I publish a release automatically?

Call the reusable workflow from your app's repo, for example on every push to the branch you ship:

```yaml
jobs:
  publish:
    uses: frappe/marketplace/.github/workflows/publish-release.yml@main
    with:
      app: helpdesk
      branch: main
      channel: stable   # or nightly, for a develop build
    secrets:
      registry_token: ${{ secrets.MARKETPLACE_PR_TOKEN }}
```

It reads `version`, `frappe_core` and `dependencies` from your `pyproject.toml` at that commit, adds the release to `apps/<app>.json`, and opens the PR.

## Can I publish by hand?

Yes. Run `tools/add_release.py` from a checkout of your app, or edit `apps/<app>.json` yourself, then open the PR.

## Why is there only one PR for my branch?

Each app branch has one rolling PR, on `publish/<app>-<branch>`. Publishing again while it is open moves the same PR to the newer commit and cancels the older check. Commits replaced before a merge are never advertised, which is fine: benches only install the newest.

## Does someone have to merge it?

Not usually. A release PR merges on its own once the check passes; see the [auto-merge rules](../auto-merge/rules.md).

## Stable or nightly?

`channel` describes the code line, not the branch name. Pilot installs the newest compatible `stable` release and falls back to `nightly` only when no stable release fits the bench's Frappe version.

- Use `nightly` for a rolling dev branch that lives alongside release branches, such as `develop` in an app that also ships `version-15` or `main`.
- Use `stable` when your only branch is `develop`, as for telephony and most small integrations. That branch is what every bench runs, so marking it nightly would make production benches look like they fell back to a dev build.

## My version never changes. How do I publish a new commit?

A branch carries one release per version. Re-advertising the same version at a newer commit replaces that release, which is what `add_release.py` does. The registry's git history records what was advertised when.

## Should I publish prebuilt assets?

Yes, if your asset build is heavy. The check builds assets within 1 GiB of memory, and Pilot installs faster when it can download them. Publish a GitHub release `assets-<branch>` holding `<app>-<commit>.tar.gz` and its `.sha256` for each release commit, as [Pilot's app assets workflow](https://github.com/frappe/pilot/blob/develop/.github/workflows/app-assets.yml) does.
