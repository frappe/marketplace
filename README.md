# Marketplace

This is the marketplace for [Frappe](https://frappeframework.com) apps — a
community-editable registry of installable apps, consumed by
[`bench get-app`](https://github.com/frappe/pilot) and the Pilot admin UI's
Marketplace page.

## What's here

- `apps.json` — the registry itself: one entry per app, each with a list of
  installable `targets` (version, branch, Frappe compatibility range, and
  dependencies on other apps).
- `scripts/` — validation and maintenance scripts. `check_marketplace_apps.py`
  runs the semgrep and get-app checks CI gates PRs on; `migrate_registry.py`
  refreshes each app's `targets` from its `pyproject.toml` on GitHub.
- `.github/workflows/marketplace-app-check.yml` — validates every PR that
  touches `apps.json`.

## Contributing an app

Add an entry to `apps.json` and open a PR.

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
  "targets": [
    {
      "version": "1.27.0",
      "target_type": "branch",
      "target": "main",
      "frappe_core": ">=15.109.0,<17.0.0",
      "dependencies": {
        "telephony": ">=0.0.1,<1.0.0"
      }
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
| `stars` | No | Kept in sync by `scripts/fetch_stars.py` — leave as `0`/omit |
| `targets` | Yes | At least one installable version — see below |

Each entry in `targets`:

| Field | Required | Notes |
|---|---|---|
| `version` | Yes | App version at this target |
| `target_type` | Yes | `branch` (only supported type today) |
| `target` | Yes | Branch name to install |
| `frappe_core` | Yes | Frappe version range this target requires, e.g. `>=15.0.0,<17.0.0` — must come from `[tool.bench.frappe-dependencies].frappe` in the app's `pyproject.toml`; targets without it are rejected |
| `dependencies` | No | Other marketplace apps this target requires, as `{name: version-range}` |

## What CI checks

When your PR is opened, CI clones your repo at the specified `target` and
runs two checks, in order:

1. **Semgrep scan.** **Blocking** findings (which fail the PR) include:
   - **Code injection** — `eval()`, `exec()`, `compile()`, `safe_eval()`
   - **Template injection** — `render_template` with dynamic input, direct `jinja2.Environment` / `Template` construction
   - **SQL injection** — f-strings or `.format()` inside `frappe.db.sql()`
   - **Command execution** — `subprocess` with `shell=True`, `os.system`, `execute_in_shell`
   - **Authorization bypass** — `ignore_permissions=True` in whitelist methods, `frappe.set_user`
   - **Multitenancy violations** — module-level globals, `redis.set`/`redis.get` without scoping

   Non-blocking findings (WARNING severity) are reported but do not prevent
   merge — a Frappe reviewer will note them in the PR.

2. **get-app validator** (only runs if semgrep passed). Installs the app
   into a throwaway venv alongside a real Frappe checkout of the version
   your target's `frappe_core` advertises — the same install
   [`bench get-app`](https://github.com/frappe/pilot) itself performs. Catches
   broken repo structure, syntax errors, undeclared `[tool.bench.frappe-dependencies]`/`required_apps`
   mismatches, and missing imports/dependencies that only show up once the
   app is actually installed.
