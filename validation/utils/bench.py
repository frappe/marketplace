#!/usr/bin/env python3
"""
Real Pilot benches that get-app installs releases into, one per Frappe branch.

Each bench is set up once with `pilot init`'s own steps, on SQLite and with
the Admin UI off since no site is ever created, then snapshotted. Once set
up, its asset builds are capped at BUILD_MEMORY_LIMIT_MB through bench.toml's
[build] memory_limit_mb, which pilot enforces with a systemd-run scope, so an
app build a small server cannot afford is killed here just as it would be
there. Setup itself runs uncapped: it builds Frappe's own assets when Frappe
publishes none, which is not the app's cost. Every
release starts from a copy of the snapshot at the same path, so the venv's
absolute paths stay valid and one release never sees another's install.
Benches live under $VALIDATION_BENCHES_DIR (see .github/workflows).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError

BENCHES_DIR_ENV = "VALIDATION_BENCHES_DIR"
BUILD_MEMORY_LIMIT_MB = 1024  # benches run on small servers; past this, a build risks the OOM killer
FRAPPE_PYPROJECT_URL = "https://raw.githubusercontent.com/frappe/frappe/{branch}/pyproject.toml"


def report(message: str) -> None:
    """Pilot progress, indented under the check's own output."""
    print(f"  {message}", flush=True)


def benches_dir() -> Path:
    return Path(os.environ.get(BENCHES_DIR_ENV) or Path(tempfile.gettempdir()) / "marketplace-benches")


def frappe_requires_python(frappe_branch: str) -> str:
    """frappe's requires-python on `frappe_branch`, e.g. '>=3.14,<3.15', as a uv Python request."""
    url = FRAPPE_PYPROJECT_URL.format(branch=frappe_branch)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            project = tomllib.loads(response.read().decode()).get("project", {})
    except (urllib.error.URLError, TimeoutError, tomllib.TOMLDecodeError) as exc:
        raise BenchError(f"Could not read frappe@{frappe_branch}'s pyproject.toml ({exc})") from exc
    requires_python = project.get("requires-python")
    if not requires_python:
        raise BenchError(f"frappe@{frappe_branch} declares no requires-python")
    return requires_python


class ValidationBench:
    """A bench on one Frappe branch, set up once and reset before each release."""

    def __init__(self, frappe_branch: str, root: Path | None = None) -> None:
        root = root or benches_dir()
        self.frappe_branch = frappe_branch
        self.path = root / f"frappe-{frappe_branch}"
        self.snapshot_path = root / f"frappe-{frappe_branch}.snapshot"

    def get_fresh_bench(self) -> Bench:
        """The bench as `pilot init` left it, with nothing from an earlier release."""
        if not self.snapshot_path.is_dir():
            self.initialize()
        shutil.rmtree(self.path, ignore_errors=True)
        copy_tree(self.snapshot_path, self.path)
        return Bench(self.path)

    def initialize(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        python = frappe_requires_python(self.frappe_branch)

        def configure(config: BenchConfig) -> None:
            config.python_version = python
            config.admin.enabled = False

        report(f"Setting up a Frappe {self.frappe_branch} bench at {self.path}...")
        bench = Bench.create_at(
            self.path,
            self.path.name,
            frappe_branch=self.frappe_branch,
            db_type="sqlite",
            admin_password="validation",
            configure=configure,
        )
        bench.initialize(on_progress=report)
        bench.config.build.memory_limit_mb = BUILD_MEMORY_LIMIT_MB
        bench.config.write(bench.path)
        # Copied aside then renamed, so a copy cut short never passes for a snapshot.
        partial = self.snapshot_path.with_name(f"{self.snapshot_path.name}.partial")
        shutil.rmtree(partial, ignore_errors=True)
        copy_tree(self.path, partial)
        partial.rename(self.snapshot_path)


def copy_tree(source: Path, destination: Path) -> None:
    """`cp -a`: keeps symlinks, modes and the bench's node_modules quickly."""
    subprocess.run(["cp", "-a", str(source), str(destination)], check=True)
