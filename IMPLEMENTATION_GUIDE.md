# Implementation Guide: Automated Version Fetching

## Quick Reference

### What to Build
1. `tools/fetch_latest_versions.py` - Main tool to detect new releases
2. `.github/workflows/refresh-versions.yml` - Scheduled workflow to run the tool

### Architecture
```
Every 6 hours (or on manual trigger):
  1. fetch_latest_versions.py runs
  2. For each app in apps.json:
     - Query GitHub for commits on tracked branches
     - Fetch pyproject.toml from each commit
     - Compare with existing releases in apps/<app>.json
     - Add any new releases detected
  3. If changes made:
     - Create/update PR in marketplace
     - Marketplace checks validate
     - Auto-merge if checks pass
```

---

## File: `tools/fetch_latest_versions.py`

### Key Components

#### 1. Configuration (top of file)
```python
TRACKED_BRANCHES = ["main", "develop", "version-16", "version-15", "master"]
NIGHTLY_BRANCHES = ("develop",)  # From migrate_registry.py
REGISTRY = Path(__file__).parent.parent / "apps.json"
APPS_DIR = REGISTRY.parent / "apps"
```

#### 2. GitHubClient (reuse from migrate_registry.py)
```python
class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token
    
    def fetch_pyproject(self, repo_url: str, branch: str, log: Log) -> dict | None:
        """Fetch pyproject.toml from branch"""
        # Reuse this method from migrate_registry.py
        pass
    
    def resolve_branch_to_commit(self, repo_url: str, branch: str) -> str | None:
        """Get latest commit SHA for a branch"""
        # Use GitHub API: GET /repos/{owner}/{repo}/commits?sha={branch}
        pass
```

#### 3. Release Detection
```python
def get_new_releases(app_name: str, repo_url: str, client: GitHubClient) -> list[dict]:
    """
    For each tracked branch:
      1. Get latest commit
      2. Fetch pyproject.toml
      3. Compare with existing releases
      4. Return only new ones
    """
    new_releases = []
    
    for branch in TRACKED_BRANCHES:
        commit = client.resolve_branch_to_commit(repo_url, branch)
        if not commit:
            continue
        
        # Check if this commit is already advertised
        if is_commit_already_released(app_name, commit):
            continue
        
        # Fetch metadata
        toml = client.fetch_pyproject(repo_url, branch)
        if not toml:
            continue
        
        # Build release entry
        version = extract_version(toml)
        frappe_core = extract_frappe_core(toml)
        dependencies = extract_dependencies(toml)
        
        release = {
            "version": version,
            "branch": branch,
            "commit": commit,
            "frappe_core": frappe_core,
            "dependencies": dependencies,
            "channel": "nightly" if branch in NIGHTLY_BRANCHES else "stable"
        }
        
        new_releases.append(release)
    
    return new_releases
```

#### 4. Release Update
```python
def update_app_releases(app_name: str, new_releases: list[dict]) -> bool:
    """
    Add new releases to apps/<app>.json
    Returns True if changes were made
    """
    path = APPS_DIR / f"{app_name}.json"
    
    if not path.exists():
        return False
    
    payload = json.loads(path.read_text())
    releases = payload.setdefault("releases", [])
    
    changes_made = False
    for release in new_releases:
        # Check if exact commit already exists
        if any(r.get("commit") == release["commit"] for r in releases):
            continue
        
        # Check if (version, branch) exists and replace it
        existing = next(
            (r for r in releases 
             if (r.get("version"), r.get("branch")) == (release["version"], release["branch"])),
            None
        )
        
        if existing:
            releases[releases.index(existing)] = release
        else:
            releases.append(release)
        
        changes_made = True
    
    if changes_made:
        # Sort newest first
        releases.sort(key=lambda r: Version(r["version"]), reverse=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    
    return changes_made
```

#### 5. Main Function
```python
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--app", type=str, help="Process only this app (optional)")
    args = parser.parse_args()
    
    token = os.environ.get("GITHUB_TOKEN")
    client = GitHubClient(token)
    apps = json.loads(REGISTRY.read_text())
    
    total_changes = 0
    
    for app in apps:
        app_name = app["name"]
        
        if args.app and app_name != args.app:
            continue
        
        if not app.get("repo"):
            continue
        
        if app.get("skip_auto_publish"):
            print(f"⊘ {app_name}: skipped (skip_auto_publish)")
            continue
        
        try:
            new_releases = get_new_releases(app_name, app["repo"], client)
            
            if new_releases:
                if args.dry_run:
                    print(f"Would add {len(new_releases)} release(s) to {app_name}:")
                    for rel in new_releases:
                        print(f"  - {rel['version']} ({rel['branch']}) @ {rel['commit'][:8]}")
                    total_changes += len(new_releases)
                else:
                    if update_app_releases(app_name, new_releases):
                        print(f"✓ {app_name}: added {len(new_releases)} release(s)")
                        total_changes += len(new_releases)
        except Exception as e:
            print(f"✗ {app_name}: error - {e}")
    
    if total_changes:
        print(f"\nTotal: {total_changes} new release(s)")
    else:
        print("No new releases found")
    
    return 0 if total_changes > 0 else 0  # Always exit 0 (workflow handles changes check)
```

---

## File: `.github/workflows/refresh-versions.yml`

```yaml
name: Refresh app versions

on:
  schedule:
    # Run every 6 hours
    - cron: "0 */6 * * *"
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  refresh:
    runs-on: ubuntu-22.04
    steps:
      - name: Check out marketplace
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install dependencies
        run: pip install packaging

      - name: Fetch latest versions
        env:
          GITHUB_TOKEN: ${{ github.token }}
        run: python3 tools/fetch_latest_versions.py

      - name: Create or update PR if changes detected
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          if git diff --quiet apps/; then
            echo "No changes detected"
            exit 0
          fi
          
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          
          branch="refresh/versions-$(date +%s)"
          git checkout -B "$branch"
          git commit -am "chore: refresh app versions"
          git push -f origin "$branch"
          
          # Create or update PR
          number=$(gh pr list --head "$branch" --base main --state open --json number --jq '.[0].number // empty')
          if [ -n "$number" ]; then
            echo "Updating existing PR #$number"
            gh pr edit "$number" --body "Automated version refresh"
          else
            echo "Creating new PR"
            gh pr create \
              --title "chore: refresh app versions" \
              --body "Automated detection of new app releases" \
              --base main \
              --head "$branch"
          fi
```

---

## Code Reuse Checklist

- [ ] Copy `GitHubClient` class from `tools/migrate_registry.py`
- [ ] Reuse `fetch_pyproject()` method
- [ ] Adapt `assign_channels()` logic for nightly/stable detection
- [ ] Use `sort_key()` from `tools/add_release.py` for version sorting
- [ ] Reference `refresh-stars.yml` workflow pattern
- [ ] Reuse error handling and retry logic from `migrate_registry.py`

---

## Testing Strategy

### Phase 1: Dry Run
```bash
# Test with single app
GITHUB_TOKEN=ghp_... python3 tools/fetch_latest_versions.py --app helpdesk --dry-run

# Test all apps
GITHUB_TOKEN=ghp_... python3 tools/fetch_latest_versions.py --dry-run
```

### Phase 2: Local Test
```bash
# Actually write changes locally
GITHUB_TOKEN=ghp_... python3 tools/fetch_latest_versions.py --app helpdesk

# Check what was changed
git diff apps/helpdesk.json
```

### Phase 3: Workflow Test
- Trigger workflow manually in GitHub UI
- Monitor PR creation
- Check marketplace-app-check runs
- Verify auto-merge happens

---

## Monitoring & Observability

### Success Indicators
- ✅ New releases appear within 6 hours of GitHub push
- ✅ Marketplace checks pass for all new releases
- ✅ No duplicate releases (same commit advertised twice)
- ✅ Nightly/stable channels correctly assigned
- ✅ No false positives (pre-releases, etc.)

### Debugging
- Check workflow runs at: `.github/workflows/refresh-versions.yml`
- View tool output in workflow logs
- Dry-run locally to test: `--dry-run` flag
- Check git diff to see what would change

---

## Optional Enhancements

### 1. Skip Certain Apps
Add to `apps.json`:
```json
{
  "name": "my-app",
  "skip_auto_publish": true,
  ...
}
```

### 2. Custom Tracked Branches Per App
Add to `apps.json`:
```json
{
  "name": "my-app",
  "tracked_branches": ["main", "develop", "custom-branch"],
  ...
}
```

### 3. Pre-Release Handling
Filter out pre-releases from version comparison:
```python
from packaging.version import Version
v = Version("1.0.0rc1")
if v.is_prerelease:
    # Skip or mark as nightly
```

### 4. Notifications
Add Slack webhook to notify on new releases:
```python
# When new releases detected
requests.post(SLACK_WEBHOOK, json={
    "text": f"🎉 New {app_name} {version} release detected"
})
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Workflow doesn't trigger on schedule** | Check Actions permissions in repo settings |
| **Rate limit errors** | Use fine-grained PAT token, implement backoff |
| **Duplicate releases** | Check `is_commit_already_released()` logic |
| **Wrong channel (nightly vs stable)** | Verify `NIGHTLY_BRANCHES` config |
| **Slow execution** | Use ThreadPoolExecutor for parallel GitHub queries |

---

## References

- Existing `tools/migrate_registry.py` (one-time migration)
- `tools/add_release.py` (manual release addition)
- `tools/fetch_stars.py` (refresh pattern)
- `.github/workflows/refresh-stars.yml` (scheduled workflow pattern)
- `validation/auto_merge.py` (auto-merge rules)
