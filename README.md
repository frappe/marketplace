# Marketplace

The registry of installable [Frappe](https://frappeframework.com) apps. [Pilot](https://github.com/frappe/pilot) reads it for `get-app` and for the Admin UI's Marketplace page. Anyone can add an app or a release by opening a PR.

## Guides

| Question | Guide |
|---|---|
| How do I publish a new release of my app? | [Publishing releases](docs/publishing/releases.md) |
| What does the marketplace check do? | [How the check works](docs/marketplace-check/how-it-works.md) |
| My check failed. What now? | [Check failures](docs/marketplace-check/failures.md) |
| Why was my PR merged, or held for review? | [Auto-merge rules](docs/auto-merge/rules.md) |

## Adding an app

1. Add an entry for your app to `apps.json`.
2. Add `apps/<name>.json` with at least one release.
3. Open a PR. The [marketplace check](docs/marketplace-check/how-it-works.md) installs your app on a real bench. If it passes, a new app [merges on its own](docs/auto-merge/rules.md).

### The `apps.json` entry

```json
{
  "name": "helpdesk",
  "title": "Helpdesk",
  "description": "Well designed, open source ticketing system",
  "repo": "https://github.com/frappe/helpdesk",
  "logo_url": "https://cloud.frappe.io/files/helpdesk (1).png",
  "website": "https://frappe.io/helpdesk",
  "documentation": "https://docs.frappe.io/helpdesk",
  "categories": ["Support"],
  "category": "Applications",
  "stars": 0,
  "releases": "apps/helpdesk.json"
}
```

Every field is required.

| Field | Notes |
|---|---|
| `name` | Unique snake_case identifier |
| `title` | Display name |
| `description` | One line, shown in the Marketplace UI |
| `repo` | Public GitHub repo URL |
| `logo_url` | Direct URL to a square PNG or SVG |
| `website` | App or project homepage |
| `documentation` | Docs URL |
| `categories` | Non-empty list of tags shown in the UI. Reviewers add `Featured` |
| `category` | One of `Applications`, `Compliance`, `Developer Tools`, `Extensions`, `Integrations`, `Utilities` |
| `stars` | Use `0`; a scheduled job keeps it in sync |
| `releases` | Exactly `apps/<name>.json`, the only path Pilot reads |

### The releases file

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
      "dependencies": { "telephony": ">=0.0.1,<1.0.0" },
      "channel": "stable"
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `version` | Yes | The version the commit's `pyproject.toml` declares |
| `branch` | Yes | The branch the commit is on; Pilot tracks it for updates |
| `commit` | Yes | Full 40-character SHA, reachable from `branch` |
| `frappe_core` | Yes | Copied from `[tool.bench.frappe-dependencies].frappe` in `pyproject.toml`, with a lower bound, e.g. `>=15.0.0,<17.0.0` |
| `dependencies` | Yes | Other marketplace apps this release needs, as `{name: version-range}`; `{}` is fine |
| `channel` | No | `stable` (default) or `nightly`; see [stable or nightly](docs/publishing/releases.md#stable-or-nightly) |

After the first release, publish new ones with the [reusable workflow](docs/publishing/releases.md).

## Repository layout

- `apps.json` and `apps/` are the registry.
- `validation/` holds the marketplace check (`check.py`) and the auto-merge rules (`auto_merge.py`), with tests in `validation/tests/`.
- `tools/` holds maintenance scripts. `add_release.py` adds a release from an app checkout.
- `.github/workflows/` runs the check and the auto-merge, and holds the reusable publish workflow.
