import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from auto_merge import get_github_repo, get_merge_blockers

RELEASE = {"version": "2.45.2", "branch": "develop", "commit": "a" * 40, "channel": "nightly"}
NEWER = {**RELEASE, "commit": "b" * 40}
LMS_REPO = "https://github.com/frappe/lms"


def write_registry(root: Path, repos: dict[str, str], releases: dict[str, list]) -> Path:
    (root / "apps").mkdir(parents=True)
    index = [{"name": name, "repo": repo, "releases": f"apps/{name}.json"} for name, repo in repos.items()]
    (root / "apps.json").write_text(json.dumps(index))
    for name, app_releases in releases.items():
        (root / "apps" / f"{name}.json").write_text(json.dumps({"name": name, "releases": app_releases}))
    return root


def always_ahead(repo, base_commit, head_commit):
    return "ahead"


def blockers(base, head, changed, get_commit_order=always_ahead):
    return get_merge_blockers(base, head, changed, get_commit_order)


@pytest.fixture
def registries(tmp_path):
    """Base lists lms (frappe) and easy_cash (a third party); head moves both to a newer commit."""
    repos = {"lms": LMS_REPO, "easy_cash": "https://github.com/someone/easy_cash"}
    base = write_registry(tmp_path / "base", repos, {"lms": [RELEASE], "easy_cash": [RELEASE]})
    head = write_registry(tmp_path / "head", repos, {"lms": [NEWER], "easy_cash": [NEWER]})
    return base, head


def read_index(root: Path) -> list[dict]:
    return json.loads((root / "apps.json").read_text())


def write_index(root: Path, index: list[dict]) -> None:
    (root / "apps.json").write_text(json.dumps(index))


def set_head_releases(head: Path, releases: list) -> None:
    (head / "apps" / "lms.json").write_text(json.dumps({"name": "lms", "releases": releases}))


@pytest.mark.parametrize(
    "changed", [["apps/lms.json"], ["apps/easy_cash.json"], ["apps/lms.json", "apps/easy_cash.json"]]
)
def test_new_releases_of_any_app_merge(registries, changed):
    assert blockers(*registries, changed) == []


@pytest.mark.parametrize(
    ("changed", "outside"),
    [
        (["apps/lms.json", ".github/workflows/x.yml"], [".github/workflows/x.yml"]),
        (["validation/check.py"], ["validation/check.py"]),
        (["apps/lms/extra.json"], ["apps/lms/extra.json"]),
    ],
)
def test_files_outside_the_registry_need_a_reviewer(registries, changed, outside):
    assert blockers(*registries, changed) == [f"it changes {outside} outside apps.json and apps/<app>.json"]


def test_new_app_merges(registries):
    base, head = registries
    write_index(
        head,
        [*read_index(head), {"name": "newapp", "repo": "https://github.com/x/newapp", "releases": "apps/newapp.json"}],
    )
    (head / "apps" / "newapp.json").write_text(json.dumps({"name": "newapp", "releases": [RELEASE]}))
    assert blockers(base, head, ["apps.json", "apps/newapp.json"]) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", "Learn things"),
        ("website", "https://example.com/phish"),
        ("categories", ["Featured"]),
        ("stars", 999999),
        ("repo", "https://github.com/attacker/lms"),
        ("releases", "apps/other.json"),
        ("developer_contact", "attacker@example.com"),
    ],
)
def test_change_to_a_listed_apps_entry_needs_a_reviewer(registries, field, value):
    """The check validates none of an entry's fields, so it passes an edit to any of them."""
    base, head = registries
    index = read_index(head)
    index[0][field] = value
    write_index(head, index)
    assert blockers(base, head, ["apps.json"]) == [f"it changes lms's {field} in apps.json"]


@pytest.mark.parametrize(
    ("base_contact", "head_contact"), [("dev@frappe.io", "attacker@example.com"), ("dev@frappe.io", None)]
)
def test_developer_contact_change_or_removal_needs_a_reviewer(registries, base_contact, head_contact):
    base, head = registries
    for root, contact in ((base, base_contact), (head, head_contact)):
        index = read_index(root)
        index[0] = {key: value for key, value in index[0].items() if key != "developer_contact"}
        if contact:
            index[0]["developer_contact"] = contact
        write_index(root, index)
    assert blockers(base, head, ["apps.json"]) == ["it changes lms's developer_contact in apps.json"]


def test_every_changed_field_is_named(registries):
    base, head = registries
    index = read_index(head)
    index[0] = {**index[0], "website": "https://example.com", "title": "LMS"}
    del index[0]["repo"]
    write_index(head, index)
    assert blockers(base, head, ["apps.json"]) == ["it changes lms's repo, title, website in apps.json"]


def test_new_app_claiming_featured_needs_a_reviewer(registries):
    base, head = registries
    entry = {"name": "newapp", "repo": "https://github.com/x/newapp", "releases": "apps/newapp.json"}
    write_index(head, [*read_index(head), {**entry, "categories": ["Featured", "Support"]}])
    (head / "apps" / "newapp.json").write_text(json.dumps({"name": "newapp", "releases": [RELEASE]}))
    assert blockers(base, head, ["apps.json", "apps/newapp.json"]) == ["new app newapp claims the Featured category"]


def test_removed_app_needs_a_reviewer(registries):
    base, head = registries
    write_index(head, read_index(head)[1:])
    assert blockers(base, head, ["apps.json"]) == ["it removes lms from apps.json"]


def test_duplicate_listing_needs_a_reviewer(registries):
    """Two lms entries: a reader taking the first would see a repo no check compared."""
    base, head = registries
    index = read_index(head)
    write_index(head, [{**index[0], "repo": "https://github.com/attacker/lms"}, *index])
    assert "apps.json lists lms more than once" in blockers(base, head, ["apps.json"])


def test_unlisted_releases_file_needs_a_reviewer(registries):
    base, head = registries
    (head / "apps" / "stray.json").write_text(json.dumps({"name": "stray", "releases": [RELEASE]}))
    assert blockers(base, head, ["apps/stray.json"]) == ["apps/stray.json belongs to no app in apps.json"]


def test_deleted_releases_file_needs_a_reviewer(registries):
    base, head = registries
    (head / "apps" / "lms.json").unlink()
    assert blockers(base, head, ["apps/lms.json"]) == ["it deletes apps/lms.json"]


def test_change_beyond_releases_needs_a_reviewer(registries):
    base, head = registries
    (head / "apps" / "lms.json").write_text(json.dumps({"name": "lms2", "releases": [NEWER]}))
    assert blockers(base, head, ["apps/lms.json"]) == ["it changes more than the releases in apps/lms.json"]


def test_added_release_merges(registries):
    base, head = registries
    set_head_releases(head, [{**RELEASE, "version": "2.46.0", "commit": "c" * 40}, RELEASE])
    assert blockers(base, head, ["apps/lms.json"]) == []


def test_in_place_edit_of_a_release_needs_a_reviewer(registries):
    """Same version, branch and commit: the check sees nothing new and validates nothing."""
    base, head = registries
    set_head_releases(head, [{**RELEASE, "frappe_core": ">=15.0.0,<18.0.0"}])
    assert blockers(base, head, ["apps/lms.json"]) == [
        "it edits release 2.45.2 on develop at aaaaaaaa in place, which the check does not validate"
    ]


def test_removed_release_needs_a_reviewer(registries):
    base, head = registries
    set_head_releases(head, [])
    assert blockers(base, head, ["apps/lms.json"]) == ["it removes release 2.45.2 on develop at aaaaaaaa"]


def test_release_on_another_branch_cannot_replace_one(registries):
    base, head = registries
    set_head_releases(head, [{**RELEASE, "branch": "main", "commit": "c" * 40}])
    assert blockers(base, head, ["apps/lms.json"]) == ["it removes release 2.45.2 on develop at aaaaaaaa"]


@pytest.mark.parametrize("order", ["behind", "diverged"])
def test_release_not_ahead_of_the_advertised_one_needs_a_reviewer(registries, order):
    assert blockers(*registries, ["apps/lms.json"], lambda *_: order) == [
        f"release 2.45.2 on develop at bbbbbbbb is not ahead of the advertised 2.45.2 on develop at aaaaaaaa ({order})"
    ]


def test_commit_order_is_read_from_the_base_repo(registries):
    calls = []
    blockers(*registries, ["apps/lms.json"], lambda *args: calls.append(args) or "ahead")
    assert calls == [(LMS_REPO, "a" * 40, "b" * 40)]


def test_release_on_a_new_branch_needs_no_comparison(registries):
    base, head = registries
    set_head_releases(head, [RELEASE, {**RELEASE, "version": "2.46.0", "branch": "main", "commit": "c" * 40}])
    assert blockers(base, head, ["apps/lms.json"], lambda *_: pytest.fail("compared")) == []


@pytest.mark.parametrize(
    ("repo", "owner_and_name"),
    [
        ("https://github.com/frappe/lms", "frappe/lms"),
        ("https://github.com/frappe/lms.git", "frappe/lms"),
        ("https://gitlab.com/frappe/lms", ""),
        ("http://github.com/frappe/lms", ""),
        ("https://github.com.evil.io/frappe/lms", ""),
        ("https://github.com/frappe", ""),
    ],
)
def test_github_repo(repo, owner_and_name):
    assert get_github_repo(repo) == owner_and_name
