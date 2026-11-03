import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import get_app_check
from get_app_check import frappe_branch_for

from pilot.exceptions import AppValidationError


@pytest.fixture
def frappe(monkeypatch):
    """Fake frappe remote: `branches` exist, develop is `develop` major, `ls_remote_code` overrides lookups."""
    state = {"branches": {"version-15", "version-16"}, "develop": 17, "ls_remote_code": None}

    def run(command, **kwargs):
        branch = command[-1].removeprefix("refs/heads/")
        code = state["ls_remote_code"]
        if code is None:
            code = 0 if branch in state["branches"] else get_app_check.LS_REMOTE_NO_MATCH
        return subprocess.CompletedProcess(command, code, "", "fatal: unable to access")

    monkeypatch.setattr(get_app_check.subprocess, "run", run)
    monkeypatch.setattr(get_app_check, "frappe_develop_major", lambda: state["develop"])
    return state


def test_existing_version_branch(frappe):
    assert frappe_branch_for(">=15.0.0,<17.0.0") == "version-15"


def test_major_in_development_uses_develop(frappe):
    assert frappe_branch_for(">=17.0.0-dev") == "develop"


def test_major_older_than_any_branch_fails(frappe):
    with pytest.raises(AppValidationError, match="no version-10 branch"):
        frappe_branch_for(">=10.0.0")


def test_major_beyond_develop_fails(frappe):
    with pytest.raises(AppValidationError, match="develop is Frappe 17"):
        frappe_branch_for(">=71.0.0")


def test_failed_lookup_fails_instead_of_using_develop(frappe):
    frappe["ls_remote_code"] = 128
    with pytest.raises(AppValidationError, match="/rereview"):
        frappe_branch_for(">=17.0.0-dev")
