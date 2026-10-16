"""Selecting the apps, releases and dependency releases an audit run covers."""

from __future__ import annotations

import sys
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from utils.diff import load_registry

FIRST_PARTY_PREFIX = "https://github.com/frappe/"


class UnknownAppError(Exception):
    """An app was asked for by name that the registry does not carry."""


def is_first_party(app: dict) -> bool:
    return (app.get("repo") or "").startswith(FIRST_PARTY_PREFIX)


def select_apps(apps: dict[str, dict], *, names: list[str] | None = None, first_party: bool = False) -> list[dict]:
    """Registry entries to audit, by name, by first-party, or all of them."""
    if names:
        missing = sorted(set(names) - set(apps))
        if missing:
            raise UnknownAppError(f"not in the registry: {', '.join(missing)}")
        chosen = [apps[name] for name in names]
    elif first_party:
        chosen = [app for app in apps.values() if is_first_party(app)]
    else:
        chosen = list(apps.values())
    return sorted(chosen, key=lambda app: app["name"])


def releases_of(app: dict) -> list[dict]:
    """Every release in apps/<name>.json, each carrying its app's name and repo."""
    return [{"name": app["name"], "repo": app["repo"], **release} for release in app.get("releases", [])]


def resolve_dependencies(apps: dict[str, dict], release: dict) -> list[dict]:
    """The registry releases this release depends on, transitively.

    Best match wins: the highest version satisfying the declared specifier. A
    dependency the registry doesn't carry, or that nothing satisfies, is left
    out - the validators then report it as a missing app rather than the audit
    guessing.
    """
    resolved: dict[str, dict] = {}
    queue = list(release.get("dependencies", {}).items())
    while queue:
        name, specifier = queue.pop(0)
        if name in resolved or name == "frappe" or name not in apps:
            continue
        match = best_release(apps[name], specifier)
        if match is None:
            continue
        resolved[name] = match
        queue.extend(match.get("dependencies", {}).items())
    return list(resolved.values())


def best_release(app: dict, specifier: str) -> dict | None:
    """The highest-version release of `app` that satisfies `specifier`."""
    allowed = _specifier_set(specifier)
    candidates = []
    for release in app.get("releases", []):
        try:
            version = Version(release["version"])
        except (InvalidVersion, KeyError, TypeError):
            continue
        if allowed is None or version in allowed:
            candidates.append((version, release))
    if not candidates:
        return None
    _, release = max(candidates, key=lambda candidate: candidate[0])
    return {"name": app["name"], "repo": app["repo"], **release}


def _specifier_set(specifier: str) -> SpecifierSet | None:
    """None when the specifier is absent or unreadable - every release matches."""
    if not specifier:
        return None
    try:
        # Prereleases on: nightly releases version themselves 17.0.0-dev.
        return SpecifierSet(specifier, prereleases=True)
    except InvalidSpecifier:
        return None


__all__ = [
    "UnknownAppError",
    "best_release",
    "is_first_party",
    "load_registry",
    "releases_of",
    "resolve_dependencies",
    "select_apps",
]
