# Marketplace scripts restructure: validation/ vs tools/

## Problem

`scripts/` mixes two unrelated concerns: PR-gating validation (invoked by
`Marketplace App Check` CI) and one-off/periodic maintenance tooling
(registry migration, star counts, CSV imports). There's no directory
boundary between them, and validator file names don't reflect which
checks are the real gates (`get_app_check`, `semgrep_check`) versus
plumbing that only exists to feed them a changed-target list.

This matters now because future work will have a bot post per-app
failure messages on marketplace PRs, reading structured output from the
validation checks specifically — that work should live in a directory
that contains only checks and their plumbing, not registry-migration
scripts.

## Scope

Directory restructure + renames + reference updates only. No behavior
change, no new structured-error interface (that's future work, out of
scope here — see "Future work" below).

## Verified usage (no dead files)

Traced every import edge from `check_marketplace_apps.py` (the CI
entrypoint). All 7 files it transitively pulls in are live:

```
check_marketplace_apps.py
  -> clone_utils.clone_app
  -> diff_marketplace_apps.{load_apps, find_changed_targets}
  -> validate_registry_schema.SchemaValidator   -> validator.Validator
  -> run_semgrep_validations.SemgrepValidator   -> validator.Validator
  -> run_get_app_validation.GetAppValidator      -> validator.Validator
```

`validator.py` is the shared base class (`fail()`/`validate()`/`run()`)
all three checks subclass. Nothing here is unused; nothing to delete.

The other 4 files (`categorize_marketplace.py`, `fetch_stars.py`,
`import_marketplace.py`, `migrate_registry.py`) are not imported by
anything in the validation cluster and vice versa — a real seam, not an
arbitrary line.

## New layout

```
validation/                          # PR-gating checks, CI-invoked
  check.py               # was check_marketplace_apps.py — orchestrator/CI entrypoint
  get_app_check.py        # was run_get_app_validation.py — GetAppValidator (primary check)
  semgrep_check.py         # was run_semgrep_validations.py — SemgrepValidator (primary check)
  schema_check.py           # was validate_registry_schema.py — SchemaValidator (primary check)
  utils/
    base.py                 # was validator.py — shared Validator base class
    clone.py                 # was clone_utils.py — clone_app
    diff.py                   # was diff_marketplace_apps.py — load_apps/find_changed_targets
  semgrep-rules/              # unchanged, moves as a unit

tools/                                # one-off / periodic maintenance, not CI-gated
  categorize_marketplace.py
  fetch_stars.py
  import_marketplace.py
  migrate_registry.py
```

Rationale for what's a top-level "check" vs `utils/`: `get_app_check`,
`semgrep_check`, and `schema_check` all subclass `Validator` and expose
the same `.validate()`/`.run() -> bool` pass/fail shape — they're peers,
not one heavier and two lighter. `base.py`/`clone.py`/`diff.py` have no
pass/fail of their own; they only fetch or diff the "which app/target
changed" input the checks consume — matching the framing that they're
utilities to find the changed/new app, not checks themselves.

## Mechanical changes

1. `git mv` each validation file to its new path/name (7 files + `semgrep-rules/`); `git mv` the 4 maintenance files into `tools/`.
2. Fix `check.py`'s imports to new module paths/names:
   - `from clone_utils import clone_app` → `from utils.clone import clone_app`
   - `from diff_marketplace_apps import find_changed_targets, load_apps` → `from utils.diff import find_changed_targets, load_apps`
   - `from run_get_app_validation import GetAppValidator` → `from get_app_check import GetAppValidator`
   - `from run_semgrep_validations import SemgrepValidator` → `from semgrep_check import SemgrepValidator`
   - `from validate_registry_schema import SchemaValidator` → `from schema_check import SchemaValidator`
   - Needs `utils/__init__.py` (empty) so `from utils.clone import ...` resolves; `sys.path.insert(0, str(Path(__file__).parent))` in `check.py` stays as-is (still same directory).
3. Fix `semgrep_check.py`'s own imports (`from validator import Validator` → `from utils.base import Validator`) and its `sys.path.insert` (parent dir unchanged, still correct). `RULES_DIR = Path(__file__).parent / "semgrep-rules"` needs no change — still same directory.
4. Fix `get_app_check.py`'s and `schema_check.py`'s `from validator import Validator` → `from utils.base import Validator`, and their `sys.path.insert` lines (unchanged, still same directory as `check.py`).
5. Update path references:
   - `.github/workflows/marketplace-app-check.yml`: `scripts/check_marketplace_apps.py` → `validation/check.py`
   - `.github/workflows/refresh-registry.yml`: `scripts/migrate_registry.py` → `tools/migrate_registry.py`
   - `README.md`: both `scripts/` mentions (the `scripts/` directory-listing line, and the `scripts/fetch_stars.py` mention)
   - Each moved file's own `Run:` docstring usage line (self-referential, currently says `scripts/<old name>.py`)
6. Delete now-empty `scripts/` and its stale `__pycache__/`.

## Testing

No behavior change — verify by running `check.py` locally against a
throwaway `apps.json` diff (or just re-running the existing CI workflow
on this PR) and confirming it still passes/fails the same way it did
before the move.

## Future work (explicitly out of scope here)

A bot that posts per-app failure messages on marketplace PRs will need
`GetAppValidator`/`SemgrepValidator`/`SchemaValidator.run()` to return a
structured reason instead of just `bool`. This restructure sets that up
by putting only checks + their plumbing under `validation/`, but no
structured-error interface is being added in this change (YAGNI).
