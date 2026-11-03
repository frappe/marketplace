import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import get_app_check
from get_app_check import GetAppValidator
from utils.bench import ValidationBench

from pilot.config import BenchConfig
from pilot.exceptions import AppValidationError, BenchError

RELEASE = {"name": "widgets", "repo": "https://github.com/acme/widgets", "branch": "main", "commit": "a" * 40}


class FakeApp:
    """Records the get-app calls the validator makes, in order."""

    def __init__(self, is_marketplace=False, error=None):
        self.is_marketplace = is_marketplace
        self.error = error
        self.calls = []

    def clone(self, on_progress):
        self.calls.append("clone")

    def checkout_commit(self, commit):
        self.calls.append(f"checkout {commit}")

    def validate(self):
        self.calls.append("validate")

    def install(self, commit, on_progress):
        self.calls.append(f"install {commit}")
        if self.error:
            raise self.error


@pytest.fixture
def get_app(monkeypatch, tmp_path):
    """Run the validator's get-app step against `app`, on a stub bench."""
    monkeypatch.setattr(get_app_check.ValidationBench, "get_fresh_bench", lambda self: "bench")

    def run(app):
        monkeypatch.setattr(get_app_check.App, "from_repo", lambda bench, repo, branch: app)
        validator = GetAppValidator(RELEASE, tmp_path)
        validator._get_app("version-16")
        return validator

    return run


def test_unlisted_release_is_left_to_get_app(get_app):
    app = FakeApp()
    get_app(app)
    assert app.calls == [f"install {RELEASE['commit']}"]


def test_listed_release_is_validated_before_get_app(get_app):
    app = FakeApp(is_marketplace=True)
    get_app(app)
    commit = RELEASE["commit"]
    assert app.calls == ["clone", f"checkout {commit}", "validate", f"install {commit}"]


def test_get_app_failure_is_reported(get_app):
    validator = get_app(FakeApp(error=AppValidationError("'widgets' has syntax errors")))
    assert [finding.message for finding in validator.findings] == ["'widgets' has syntax errors"]


def test_bench_setup_failure_is_reported(monkeypatch, tmp_path):
    def fail(self):
        raise BenchError("Node.js is not installed.")

    monkeypatch.setattr(get_app_check.ValidationBench, "get_fresh_bench", fail)
    validator = GetAppValidator(RELEASE, tmp_path)
    validator._get_app("version-16")
    assert "Could not set up a Frappe version-16 bench" in validator.findings[0].message


def test_build_killed_by_the_cap_is_a_memory_finding(get_app):
    error = BenchError("Build ran out of memory: it may use 1024MB of RAM and 0MB of swap on this machine.")
    validator = get_app(FakeApp(error=error))
    assert [finding.rule for finding in validator.findings] == ["get-app-memory"]
    assert "Publish prebuilt assets" in validator.findings[0].message


def test_bench_caps_builds_and_skips_admin(monkeypatch, tmp_path):
    created = {}

    class FakeBench:
        def initialize(self, on_progress):
            (tmp_path / "frappe-version-16").mkdir()

    def create_at(path, name, **options):
        created.update(options)
        return FakeBench()

    monkeypatch.setattr("utils.bench.frappe_requires_python", lambda branch: ">=3.10,<3.15")
    monkeypatch.setattr("utils.bench.Bench.create_at", create_at)
    ValidationBench("version-16", tmp_path).initialize()

    config = BenchConfig.default(name="validation")
    created["configure"](config)
    assert (config.build.memory_limit_mb, config.admin.enabled, config.python_version) == (1024, False, ">=3.10,<3.15")
    assert created["db_type"] == "sqlite"
    assert (tmp_path / "frappe-version-16.snapshot").is_dir()


def test_bench_is_set_up_once_and_reset_per_release(monkeypatch, tmp_path):
    setups = []

    def initialize(self):
        setups.append(self.frappe_branch)
        (self.snapshot_path / "apps").mkdir(parents=True)

    monkeypatch.setattr(ValidationBench, "initialize", initialize)
    monkeypatch.setattr("utils.bench.Bench", lambda path: path)
    bench = ValidationBench("version-16", tmp_path)

    first = bench.get_fresh_bench()
    (first / "apps" / "widgets").mkdir()
    second = bench.get_fresh_bench()

    assert setups == ["version-16"]
    assert second == tmp_path / "frappe-version-16"
    assert list((second / "apps").iterdir()) == []
