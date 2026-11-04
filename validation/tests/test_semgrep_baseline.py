import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import semgrep_check
from semgrep_check import new_findings
from utils.diff import find_changed_releases

REPO = "https://github.com/acme/widgets"
OLD = {"version": "1.0.0", "branch": "main", "commit": "a" * 40}
NEW = {"version": "1.1.0", "branch": "main", "commit": "b" * 40}
RISKY = 'frappe.db.sql(f"select {x}")'


def app(*releases, repo=REPO):
    return {"widgets": {"name": "widgets", "repo": repo, "releases": list(releases)}}


def test_release_is_scanned_against_the_commit_advertised_on_its_branch():
    [release] = find_changed_releases(app(OLD), app(NEW, OLD))
    assert release["baseline_commit"] == OLD["commit"]


def test_release_on_a_new_branch_has_no_baseline():
    [release] = find_changed_releases(app(OLD), app(OLD, {**NEW, "branch": "develop"}))
    assert release["baseline_commit"] is None


def test_new_app_and_moved_repo_have_no_baseline():
    assert "baseline_commit" not in find_changed_releases({}, app(NEW))[0]
    moved = find_changed_releases(app(OLD), app(NEW, repo="https://github.com/acme/other"))
    assert all("baseline_commit" not in release for release in moved)


def write_source(root: Path, code: str) -> Path:
    root.mkdir()
    (root / "api.py").write_text(code)
    return root


def findings_of(source_dir: Path) -> list[dict]:
    """One finding per RISKY match, shaped like semgrep's JSON."""
    code = (source_dir / "api.py").read_text()
    findings, start = [], code.find(RISKY)
    while start != -1:
        offsets = ({"offset": start}, {"offset": start + len(RISKY)})
        findings.append({"check_id": "sql", "path": "api.py", "start": offsets[0], "end": offsets[1]})
        start = code.find(RISKY, start + 1)
    return findings


def scan(baseline_code: str, code: str, tmp_path: Path, monkeypatch) -> list[dict]:
    monkeypatch.setattr(semgrep_check, "scan_target", findings_of)
    baseline_dir = write_source(tmp_path / "baseline", baseline_code)
    clone_dir = write_source(tmp_path / "app", code)
    return new_findings(findings_of(clone_dir), clone_dir, baseline_dir)


def test_finding_moved_by_unrelated_lines_is_not_new(tmp_path, monkeypatch):
    assert scan(RISKY, f"import frappe\n\n{RISKY}", tmp_path, monkeypatch) == []


def test_second_copy_of_a_known_finding_is_new(tmp_path, monkeypatch):
    assert len(scan(RISKY, f"{RISKY}\n{RISKY}", tmp_path, monkeypatch)) == 1
