import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from auto_merge import get_github_owner, get_merge_blockers

RELEASE = {"version": "2.45.2", "branch": "develop", "commit": "a" * 40, "channel": "nightly"}


def write_registry(root: Path, repos: dict[str, str], releases: dict[str, list]) -> Path:
    (root / "apps").mkdir(parents=True)
    index = [{"name": name, "repo": repo, "releases": f"apps/{name}.json"} for name, repo in repos.items()]
    (root / "apps.json").write_text(json.dumps(index))
    for name, app_releases in releases.items():
        (root / "apps" / f"{name}.json").write_text(json.dumps({"name": name, "releases": app_releases}))
    return root


@pytest.fixture
def registries(tmp_path):
    """Base and head registries listing lms (frappe) and easy_cash (a third party)."""
    repos = {"lms": "https://github.com/frappe/lms", "easy_cash": "https://github.com/someone/easy_cash"}
    base = write_registry(tmp_path / "base", repos, {"lms": [RELEASE], "easy_cash": [RELEASE]})
    newer = {**RELEASE, "commit": "b" * 40}
    head = write_registry(tmp_path / "head", repos, {"lms": [newer], "easy_cash": [newer]})
    return base, head


def test_frappe_release_only_change_merges(registries):
    assert get_merge_blockers(*registries, ["apps/lms.json"]) == []


def test_other_organisations_need_a_reviewer(registries):
    blockers = get_merge_blockers(*registries, ["apps/easy_cash.json"])
    assert blockers == ["easy_cash is hosted by 'someone', not frappe"]


def test_change_beyond_releases_needs_a_reviewer(registries):
    base, head = registries
    payload = json.loads((head / "apps" / "lms.json").read_text())
    (head / "apps" / "lms.json").write_text(json.dumps({**payload, "name": "lms2"}))
    assert get_merge_blockers(base, head, ["apps/lms.json"]) == ["it changes more than the releases in apps/lms.json"]


@pytest.mark.parametrize("changed", [["apps/lms.json", "apps.json"], ["apps.json"], [".github/workflows/x.yml"], []])
def test_anything_but_one_releases_file_needs_a_reviewer(registries, changed):
    assert get_merge_blockers(*registries, changed) == [f"it changes {changed}, not exactly one apps/<app>.json"]


def test_unlisted_app_needs_a_reviewer(registries):
    assert get_merge_blockers(*registries, ["apps/newapp.json"]) == [
        "newapp is not listed yet; a new app needs a reviewer"
    ]


def test_owner_is_read_from_the_base_registry(registries):
    """A PR that points lms's repo at frappe cannot launder a third-party app."""
    base, head = registries
    index = json.loads((head / "apps.json").read_text())
    for entry in index:
        entry["repo"] = "https://github.com/frappe/" + entry["name"]
    (head / "apps.json").write_text(json.dumps(index))
    assert get_merge_blockers(base, head, ["apps/easy_cash.json"]) == ["easy_cash is hosted by 'someone', not frappe"]


@pytest.mark.parametrize(
    ("repo", "owner"),
    [
        ("https://github.com/frappe/lms", "frappe"),
        ("https://github.com/Frappe/lms/", "frappe"),
        ("https://github.com/frappe-fork/lms", "frappe-fork"),
        ("https://gitlab.com/frappe/lms", ""),
        ("http://github.com/frappe/lms", ""),
        ("https://github.com.evil.io/frappe/lms", ""),
        ("https://github.com/frappe", ""),
    ],
)
def test_github_owner(repo, owner):
    assert get_github_owner(repo) == owner


def set_head_releases(head: Path, releases: list) -> None:
    (head / "apps" / "lms.json").write_text(json.dumps({"name": "lms", "releases": releases}))


def test_added_release_merges(registries):
    base, head = registries
    set_head_releases(head, [{**RELEASE, "version": "2.46.0", "commit": "c" * 40}, RELEASE])
    assert get_merge_blockers(base, head, ["apps/lms.json"]) == []


def test_in_place_edit_of_a_release_needs_a_reviewer(registries):
    """Same version, branch and commit: the check sees nothing new and validates nothing."""
    base, head = registries
    set_head_releases(head, [{**RELEASE, "frappe_core": ">=15.0.0,<18.0.0"}])
    assert get_merge_blockers(base, head, ["apps/lms.json"]) == [
        "it edits release 2.45.2 on develop at aaaaaaaa in place, which the check does not validate"
    ]


def test_removed_release_needs_a_reviewer(registries):
    base, head = registries
    set_head_releases(head, [])
    assert get_merge_blockers(base, head, ["apps/lms.json"]) == ["it removes release 2.45.2 on develop at aaaaaaaa"]


def test_release_on_another_branch_cannot_replace_one(registries):
    base, head = registries
    set_head_releases(head, [{**RELEASE, "branch": "main", "commit": "c" * 40}])
    assert get_merge_blockers(base, head, ["apps/lms.json"]) == ["it removes release 2.45.2 on develop at aaaaaaaa"]
