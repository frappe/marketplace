# Marketplace App Analysis: Version Fetching System

## Overview
This is a Frappe/ERPNext app marketplace registry that manages installable apps. It currently has a **manual/pull-request-based** release publishing system, but lacks an **automated mechanism to fetch latest versions from GitHub**.

---

## Current Architecture

### 📁 Project Structure
```
marketplace/
├── apps.json                 # Metadata index (app names, repos, stars, etc.)
├── apps/                     # Release files (one per app, contains version history)
│   ├── helpdesk.json
│   ├── erpnext.json
│   └── ... (100+ apps)
├── tools/
│   ├── add_release.py        # Add a release manually
│   ├── fetch_stars.py        # Update star counts (scheduled daily)
│   ├── migrate_registry.py   # One-time migration utility
│   └── categorize_marketplace.py
├── validation/               # Marketplace checks
│   ├── check.py
│   └── auto_merge.py
└── .github/workflows/
    ├── publish-release.yml   # Reusable workflow for app owners to publish
    ├── refresh-stars.yml     # Daily cron to update stars
    ├── marketplace-app-check.yml
    └── auto-merge-registry.yml
```

### 📊 Data Format

**apps.json** - Metadata only:
```json
{
  "name": "helpdesk",
  "title": "Helpdesk",
  "repo": "https://github.com/frappe/helpdesk",
  "releases": "apps/helpdesk.json"
}
```

**apps/helpdesk.json** - Release history:
```json
{
  "name": "helpdesk",
  "releases": [
    {
      "version": "16.1.0",
      "branch": "version-16",
      "commit": "e326986329...",  // Full 40-char SHA
      "frappe_core": ">=16.0.0,<17.0.0",
      "dependencies": {"telephony": ">=0.0.1,<1.0.0"},
      "channel": "stable"
    }
  ]
}
```

---

## Current Release Publishing Flow

### How Releases Are Added (Today)

1. **App owner publishes a release** in their repo
2. **App owner manually calls** the reusable workflow `publish-release.yml`:
   ```yaml
   jobs:
     publish:
       uses: frappe/marketplace/.github/workflows/publish-release.yml@main
       with:
         app: helpdesk
         branch: main
         channel: stable
   ```
3. **Workflow runs `add_release.py`** which:
   - Reads `pyproject.toml` from the app checkout
   - Extracts version, Frappe core range, dependencies
   - Appends/updates `apps/helpdesk.json`
   - Opens a PR

4. **Marketplace checks validate** the PR (installs the app on a test bench)
5. **Auto-merge** (if checks pass)

### ✅ What Works Well
- **Explicit**: Each release is deliberately advertised
- **Audited**: Every commit is checked before merging
- **Decentralized**: App owners control when they publish

### ❌ What's Missing
- **No automatic detection** of new releases/tags on GitHub
- **No periodic sync** of latest versions across branches
- **No batch updates** for multiple apps
- **Manual workflow for app owners** to remember to publish
- **No tracking of "latest stable" per branch**

---

## Gap Analysis: What Should Happen

### Desired Behavior
An app owner should NOT have to manually publish. Instead, the marketplace should:

1. **Monitor each app's GitHub repository** for new releases/tags
2. **Detect new commits** on tracked branches (main, develop, version-*, etc.)
3. **Fetch version + metadata** from `pyproject.toml` automatically
4. **Create a PR** in the marketplace to add the release
5. **Update existing release entries** when a branch has a newer commit

### Example Flow
```
GitHub (helpdesk repo)
  ↓ (new tag/commit detected)
Marketplace Registry
  ↓ (fetch_latest_versions.py runs on schedule)
Auto-create/update PR
  ↓ (marketplace-app-check validates)
Auto-merge (if checks pass)
```

---

## What Needs to Be Built

### 1. **Tool: `fetch_latest_versions.py`**
   **Purpose**: Automatically detect and add new releases
   
   **Behavior**:
   - For each app in `apps.json`:
     - Query GitHub API for branches (or tags)
     - For each tracked branch, resolve to latest commit
     - Fetch `pyproject.toml` from that commit
     - Compare with existing releases in `apps/<app>.json`
     - If new version/commit: create a release entry
   
   **Output**:
     - Updates `apps/<app>.json` files
     - Exits with status indicating changes
   
   **Similar to**: `migrate_registry.py` (which did one-time migration)

### 2. **Workflow: `refresh-versions.yml`**
   **Purpose**: Run the tool on a schedule
   
   **Triggers**:
     - On schedule (e.g., hourly or every 6 hours)
     - Manual trigger (`workflow_dispatch`)
   
   **Steps**:
     1. Checkout marketplace
     2. Run `fetch_latest_versions.py`
     3. If changes: create/update a PR
     4. Optional: auto-merge if desired
   
   **Based on**: `refresh-stars.yml` pattern

### 3. **Configuration: Tracked Branches Per App** (Optional)
   - Allow apps to specify which branches to track
   - Default to: `main`, `develop`, `version-*`, `master`
   - Could add to `apps.json` or use a separate config

---

## Implementation Strategy

### Phase 1: Build the Tool
Create `tools/fetch_latest_versions.py`:
```python
#!/usr/bin/env python3
"""
Automatically detect and add new releases from GitHub for all apps.

For each app:
  1. Query GitHub for commits on tracked branches
  2. Compare with existing releases in apps/<app>.json
  3. Add any new versions found
  4. Respect existing release structure

Usage:
    GITHUB_TOKEN=ghp_... python3 tools/fetch_latest_versions.py --dry-run
    GITHUB_TOKEN=ghp_... python3 tools/fetch_latest_versions.py --auto-commit
"""
```

**Key Functions**:
- `get_latest_commit(repo_url, branch)` → commit SHA
- `fetch_pyproject(repo_url, commit)` → version, frappe_core, dependencies
- `compare_release(existing, new)` → bool (is new?)
- `update_app_releases(app_name, new_releases)` → None

### Phase 2: Create the Workflow
Create `.github/workflows/refresh-versions.yml`:
```yaml
name: Refresh app versions

on:
  schedule:
    - cron: "0 */6 * * *"  # Every 6 hours
  workflow_dispatch:

jobs:
  refresh:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install packaging
      - run: python tools/fetch_latest_versions.py
        env:
          GITHUB_TOKEN: ${{ github.token }}
      - name: Create PR if changes detected
        # Similar to refresh-stars.yml
```

### Phase 3: Optional Enhancements
- **Skip certain apps**: Add a `skip_auto_publish` field to `apps.json`
- **Per-app branch config**: Add `tracked_branches` to app metadata
- **Nightly vs Stable**: Auto-detect based on branch name (like `migrate_registry.py` does)
- **Notifications**: Slack/email on new versions

---

## Code Reuse Opportunities

### From `migrate_registry.py`
- `GitHubClient` class for API calls
- `fetch_pyproject()` method
- `assign_channels()` logic for nightly/stable detection
- Error handling and retry logic

### From `add_release.py`
- `read_metadata()` to parse pyproject.toml
- `sort_key()` for version comparison
- Release entry structure and JSON writing

### From `fetch_stars.py`
- Workflow pattern (schedule + manual trigger)
- JSON reading/writing
- Simple iteration over apps

---

## Potential Issues & Solutions

| Issue | Solution |
|-------|----------|
| **Rate limits on GitHub API** | Use PAT tokens, batch requests, respect 60-request/min limit |
| **Duplicate releases** | Check by commit SHA before adding |
| **Old versions still in registry** | Keep all versions; let consumers choose |
| **App owner disagrees with auto-detect** | Support `skip_auto_publish` flag; allow override PRs |
| **Breaking changes in dependencies** | Marketplace checks catch them before merge |
| **Commits on branches but not tagged** | Track branch tips, not just tags |

---

## Files to Create/Modify

### New Files
- `tools/fetch_latest_versions.py` (300-400 lines)
- `.github/workflows/refresh-versions.yml` (30-40 lines)

### Potentially Modified
- `apps.json` (add optional `skip_auto_publish` per app, or `tracked_branches`)
- `validation/auto_merge.py` (if auto-PRs should be handled specially)

---

## Success Criteria

✅ When this is done, you should see:
1. **New releases appear in the marketplace automatically** within ~6 hours of being pushed to GitHub
2. **No manual PR needed** by app owners for new versions
3. **Marketplace checks** still validate every release before it's live
4. **Star counts and versions** stay in sync
5. **Nightly and stable channels** are correctly assigned
6. **Existing manually-published releases** still work alongside auto-detected ones

---

## Next Steps
1. Implement `fetch_latest_versions.py` (reuse code from `migrate_registry.py`)
2. Create `refresh-versions.yml` workflow
3. Test on 5-10 apps in dry-run mode
4. Enable for full registry
5. Monitor for issues in first week
