import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import semgrep_check
from semgrep_check import SemgrepValidator, load_skip_list


def test_skip_list_reads_one_app_name_per_line(tmp_path):
    (tmp_path / "semgrep-skip.txt").write_text("erpnext\n\n  crm  \n# hrms\n")

    assert load_skip_list(tmp_path) == {"erpnext", "crm"}


def test_registry_without_a_skip_list_skips_nothing(tmp_path):
    assert load_skip_list(tmp_path) == set()


def fail_scan(target):
    raise AssertionError("a skipped app must not be scanned")


def test_skipped_app_passes_with_a_note_and_is_not_scanned(tmp_path, monkeypatch):
    monkeypatch.setattr(semgrep_check, "scan_target", fail_scan)
    validator = SemgrepValidator(tmp_path, "frappe/erpnext@abc", is_skipped=True)

    assert validator.run()
    assert [note.message for note in validator.notes] == ["Skipped: the app is in semgrep-skip.txt."]


def test_app_not_on_the_list_still_fails_on_a_blocking_finding(tmp_path, monkeypatch):
    finding = {"path": str(tmp_path / "app.py"), "check_id": "rules.frappe-ssti", "extra": {"severity": "ERROR"}}
    monkeypatch.setattr(semgrep_check, "scan_target", lambda target: [finding])

    assert not SemgrepValidator(tmp_path, "acme/widgets@abc").run()


def test_shipped_skip_list_names_erpnext():
    assert "erpnext" in load_skip_list(Path(__file__).parents[2])
