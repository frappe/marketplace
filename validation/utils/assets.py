#!/usr/bin/env python3
"""
Pilot's own asset build, run against a release as get-app runs it.

The build runs Pilot's scripts/build-app-assets.sh from the checkout named
by $PILOT_DIR (see .github/workflows). The caller tracks its memory.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

PILOT_DIR_ENV = "PILOT_DIR"
BUILD_TIMEOUT_SECONDS = 30 * 60
LOG_TAIL_LINES = 40


@dataclass
class AssetBuild:
    returncode: int
    log_tail: str


def build_script() -> Path:
    pilot_dir = os.environ.get(PILOT_DIR_ENV)
    if not pilot_dir:
        raise RuntimeError(f"${PILOT_DIR_ENV} is not set; point it at a frappe/pilot checkout")
    script = Path(pilot_dir) / "scripts" / "build-app-assets.sh"
    if not script.is_file():
        raise RuntimeError(f"{script} not found; ${PILOT_DIR_ENV} must be a frappe/pilot checkout")
    return script


def build_assets(clone_dir: Path, frappe_branch: str, out_dir: Path) -> AssetBuild:
    result = subprocess.run(
        [str(build_script()), str(clone_dir), frappe_branch, str(out_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    print(result.stdout, end="", flush=True)
    tail = "\n".join(result.stdout.splitlines()[-LOG_TAIL_LINES:])
    return AssetBuild(result.returncode, tail)
