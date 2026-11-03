#!/usr/bin/env python3
"""
Track the peak memory of this process and every process it spawns, so a
check can report what its work (git clones, venv creation, `uv pip install`
and the builds it triggers) cost at its worst.

PSS (proportional set size) is summed across the whole process tree: unlike
RSS, it splits each shared page (shared libraries, copy-on-write pages after
fork) between the processes mapping it, so the sum counts it once. PSS is
Linux-only; elsewhere (local runs on macOS) RSS is used and over-counts.
Sampled on a background thread, so a spike shorter than the sampling
interval can be missed.
"""

from __future__ import annotations

import threading

import psutil

SAMPLE_INTERVAL_SECONDS = 0.2


HAS_PSS = psutil.LINUX


def process_memory(process: psutil.Process) -> int:
    """PSS where the platform reports it, RSS otherwise, in bytes."""
    if HAS_PSS:
        return process.memory_full_info().pss
    return process.memory_info().rss


def tree_memory(process: psutil.Process) -> int:
    """Memory of a process and all its descendants, in bytes."""
    total = 0
    for member in [process, *process.children(recursive=True)]:
        try:
            total += process_memory(member)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return total


class PeakMemory:
    """Context manager recording the peak tree memory seen while it is open."""

    def __init__(self, interval: float = SAMPLE_INTERVAL_SECONDS) -> None:
        self.interval = interval
        self.peak_bytes = 0
        self._process = psutil.Process()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> PeakMemory:
        self._record()
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        self._thread.join()
        self._record()

    def _sample(self) -> None:
        while not self._stop.wait(self.interval):
            self._record()

    def _record(self) -> None:
        self.peak_bytes = max(self.peak_bytes, tree_memory(self._process))
