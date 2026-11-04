import subprocess
import sys
from pathlib import Path

import pytest
from packaging.version import Version

sys.path.insert(0, str(Path(__file__).parent.parent))
import get_app_check
from get_app_check import frappe_branches_for

from pilot.exceptions import AppValidationError


@pytest.fixture
def frappe(monkeypatch):
    """Fake frappe remote: `branches` exist at `versions`, `ls_remote_code` overrides lookups."""
    versions = {"version-15": "15.90.0", "version-16": "16.40.0", "develop": "17.0.0-dev"}
    state = {"branches": {"version-15", "version-16"}, "versions": versions, "ls_remote_code": None}

    def run(command, **kwargs):
        branch = command[-1].removeprefix("refs/heads/")
        code = state["ls_remote_code"]
        if code is None:
            code = 0 if branch in state["branches"] else get_app_check.LS_REMOTE_NO_MATCH
        return subprocess.CompletedProcess(command, code, "", "fatal: unable to access")

    monkeypatch.setattr(get_app_check.subprocess, "run", run)
    monkeypatch.setattr(get_app_check, "frappe_branch_version", lambda branch: Version(state["versions"][branch]))
    return state


@pytest.mark.parametrize(
    ("frappe_core", "branches"),
    [
        (">=15.0.0,<16.0.0", ["version-15"]),
        (">=15.0.0,<17.0.0", ["version-15", "version-16"]),
        (">=15.0.0,<=17.0.0-dev", ["version-15", "version-16", "develop"]),
        (">=16.0.0-dev", ["version-16", "develop"]),
        (">=15.121.3,<18.0.0", ["version-15", "version-16", "develop"]),
    ],
)
def test_every_major_the_range_admits(frappe, frappe_core, branches):
    assert frappe_branches_for(frappe_core) == branches


def test_major_is_skipped_when_its_branch_has_moved_past_the_range(frappe):
    assert frappe_branches_for(">=15.0.0,<16.5.0") == ["version-15"]


@pytest.mark.parametrize("frappe_core", [">=17.0.0-dev", ">=17.0.0-dev,<18.0.0"])
def test_dev_floor_uses_develop(frappe, frappe_core):
    assert frappe_branches_for(frappe_core) == ["develop"]


@pytest.mark.parametrize("frappe_core", [">=17.0.0", "~=17.0"])
def test_range_excluding_the_dev_build_fails(frappe, frappe_core):
    with pytest.raises(AppValidationError, match="declare a dev floor"):
        frappe_branches_for(frappe_core)


def test_major_older_than_any_branch_fails(frappe):
    with pytest.raises(AppValidationError, match=r"no version-10 branch.*check the range"):
        frappe_branches_for(">=10.0.0")


def test_major_beyond_develop_fails(frappe):
    with pytest.raises(AppValidationError, match="check the range's lower bound"):
        frappe_branches_for(">=71.0.0")


def test_failed_lookup_fails_instead_of_using_develop(frappe):
    frappe["ls_remote_code"] = 128
    with pytest.raises(AppValidationError, match="/rereview"):
        frappe_branches_for(">=17.0.0-dev")


def test_failed_lookup_of_a_later_major_fails(frappe):
    def lookup(command, **kwargs):
        code = 0 if command[-1] == "refs/heads/version-15" else 128
        return subprocess.CompletedProcess(command, code, "", "fatal: unable to access")

    get_app_check.subprocess.run = lookup
    with pytest.raises(AppValidationError, match="/rereview"):
        frappe_branches_for(">=15.0.0,<17.0.0")
