import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import get_app_check
from get_app_check import GetAppValidator
from utils import bench as bench_module
from utils.bench import ValidationBench, build_oom_kills

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

    def install(self, commit, install_dependencies, on_progress):
        self.calls.append(f"install {commit}" + (" with dependencies" if install_dependencies else ""))
        if self.error:
            raise self.error


@pytest.fixture
def get_app(monkeypatch, tmp_path):
    """Run the validator's get-app step against `app`, on a stub bench whose OOM-kill counter
    reads `oom_kills` in turn (no kills by default)."""
    monkeypatch.setattr(get_app_check.ValidationBench, "get_fresh_bench", lambda self: "bench")

    def run(app, oom_kills=(0, 0)):
        monkeypatch.setattr(get_app_check, "build_oom_kills", iter(oom_kills).__next__)
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
    assert app.calls == ["clone", f"checkout {commit}", "validate", f"install {commit} with dependencies"]


def test_get_app_failure_is_reported(get_app):
    validator = get_app(FakeApp(error=AppValidationError("'widgets' has syntax errors")))
    assert [finding.message for finding in validator.findings] == ["On Frappe version-16: 'widgets' has syntax errors"]


def test_bench_setup_failure_is_reported(monkeypatch, tmp_path):
    def fail(self):
        raise BenchError("Node.js is not installed.")

    monkeypatch.setattr(get_app_check.ValidationBench, "get_fresh_bench", fail)
    validator = GetAppValidator(RELEASE, tmp_path)
    validator._get_app("version-16")
    assert validator.findings[0].message.startswith("On Frappe version-16: could not set up a bench")


def test_build_killed_by_the_cap_is_a_memory_finding(get_app):
    error = BenchError("Command '/bench/env/bin/python' failed with exit code 1.")
    validator = get_app(FakeApp(error=error), oom_kills=(3, 4))
    assert [finding.rule for finding in validator.findings] == ["get-app-memory"]
    assert "Marketplace requires apps with heavy builds to publish prebuilt assets" in validator.findings[0].message


def test_failure_without_an_oom_kill_keeps_pilots_message(get_app):
    error = BenchError("Command '/bench/env/bin/python' failed with exit code 1.")
    validator = get_app(FakeApp(error=error), oom_kills=(3, 3))
    assert [(finding.rule, finding.message) for finding in validator.findings] == [
        ("", f"On Frappe version-16: {error}")
    ]


def test_repo_rejected_by_pilot_is_reported(get_app, monkeypatch, tmp_path):
    def reject(bench, repo, branch):
        raise BenchError("'frappe' is the base framework, not an app")

    monkeypatch.setattr(get_app_check.App, "from_repo", reject)
    validator = GetAppValidator(RELEASE, tmp_path)
    validator._get_app("version-16")
    assert validator.findings[0].message == "On Frappe version-16: 'frappe' is the base framework, not an app"


def test_release_is_installed_on_every_frappe_major(monkeypatch, tmp_path):
    branches = []
    monkeypatch.setattr(get_app_check, "frappe_branches_for", lambda frappe_core: ["version-15", "version-16"])
    monkeypatch.setattr(GetAppValidator, "_reject_untruthful_metadata", lambda self, frappe_core: None)
    monkeypatch.setattr(GetAppValidator, "_get_app", lambda self, branch: branches.append(branch))
    GetAppValidator({**RELEASE, "frappe_core": ">=15.0.0,<17.0.0"}, tmp_path).validate()
    assert branches == ["version-15", "version-16"]


def test_oom_kills_are_read_from_the_user_slice(monkeypatch, tmp_path):
    events = tmp_path / "memory.events"
    events.write_text("low 0\nhigh 0\nmax 12\noom 2\noom_kill 2\noom_group_kill 0\n")
    monkeypatch.setattr(bench_module, "Path", lambda path: events if path.endswith("memory.events") else Path(path))
    assert build_oom_kills() == 2


def test_oom_kills_are_zero_where_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(bench_module, "Path", lambda path: tmp_path / "missing")
    assert build_oom_kills() == 0


def test_bench_caps_app_builds_only_after_setup(monkeypatch, tmp_path):
    created = {}

    class FakeBench:
        """Records the build cap as setup sees it and as bench.toml ends up with it."""

        path = tmp_path / "frappe-version-16"

        def initialize(self, on_progress):
            self.path.mkdir()
            created["cap_during_setup"] = self.config.build.memory_limit_mb

        def write(self, path):
            created["written_cap"] = self.config.build.memory_limit_mb

    bench = FakeBench()

    def create_at(path, name, **options):
        created.update(options)
        bench.config = BenchConfig.default(name="validation")
        options["configure"](bench.config)
        bench.config.write = bench.write
        return bench

    monkeypatch.setattr("utils.bench.frappe_requires_python", lambda branch: ">=3.10,<3.15")
    monkeypatch.setattr("utils.bench.Bench.create_at", create_at)
    ValidationBench("version-16", tmp_path).initialize()

    assert (bench.config.admin.enabled, bench.config.python_version) == (False, ">=3.10,<3.15")
    assert created["db_type"] == "sqlite"
    assert (created["cap_during_setup"], created["written_cap"]) == (0, 1024)
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


def test_failed_setup_is_not_retried_for_later_releases(monkeypatch, tmp_path):
    setups = []

    def initialize(self):
        setups.append(self.frappe_branch)
        raise BenchError("Node.js is not installed.")

    monkeypatch.setattr(ValidationBench, "initialize", initialize)
    monkeypatch.setattr(ValidationBench, "setup_errors", {})
    for _ in range(2):
        with pytest.raises(BenchError, match=r"Node\.js"):
            ValidationBench("version-16", tmp_path).get_fresh_bench()
    assert setups == ["version-16"]
