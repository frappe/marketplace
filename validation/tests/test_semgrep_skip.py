import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import semgrep_check
from semgrep_check import SemgrepValidator, load_skip_list


def test_skip_list_maps_app_names_to_reasons(tmp_path):
    (tmp_path / "semgrep-skip.json").write_text(json.dumps({"erpnext": "First-party app."}))

    assert load_skip_list(tmp_path) == {"erpnext": "First-party app."}


def test_registry_without_a_skip_list_skips_nothing(tmp_path):
    assert load_skip_list(tmp_path) == {}


def fail_scan(target):
    raise AssertionError("a skipped app must not be scanned")


def test_skipped_app_passes_with_a_note_and_is_not_scanned(tmp_path, monkeypatch):
    monkeypatch.setattr(semgrep_check, "scan_target", fail_scan)
    validator = SemgrepValidator(tmp_path, "frappe/erpnext@abc", skip_reason="First-party app.")

    assert validator.run()
    assert [note.message for note in validator.notes] == [
        "Skipped: the app is in semgrep-skip.json. First-party app."
    ]


def test_app_not_on_the_list_still_fails_on_a_blocking_finding(tmp_path, monkeypatch):
    finding = {"path": str(tmp_path / "app.py"), "check_id": "rules.frappe-ssti", "extra": {"severity": "ERROR"}}
    monkeypatch.setattr(semgrep_check, "scan_target", lambda target: [finding])

    assert not SemgrepValidator(tmp_path, "acme/widgets@abc").run()


def test_shipped_skip_list_is_valid_json_of_reasons():
    skip_list = load_skip_list(Path(__file__).parents[2])

    assert skip_list and all(isinstance(reason, str) and reason for reason in skip_list.values())
