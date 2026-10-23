"""The no-model acceptance runner must refuse unsafe or incomplete evidence."""

import pytest

from scripts import verify_local_workflow as local


def test_temporary_parent_cannot_inherit_a_git_workspace(tmp_path):
    (tmp_path / '.git').mkdir()
    child = tmp_path / 'temporary'
    child.mkdir()
    with pytest.raises(ValueError, match='TEMP_PARENT_INSIDE_GIT'):
        local.validate_temp_parent(child)


def test_system_scope_cannot_be_used_for_protection_acceptance():
    with pytest.raises(ValueError, match='TEMP_PARENT_SYSTEM_SCOPE'):
        local.validate_temp_parent('/var/tmp')


def test_missing_browser_evidence_cannot_pass():
    with pytest.raises(RuntimeError, match='BROWSER_CHECK_FAILED'):
        local.validate_browser_report({'result': 'PASS', 'checks': {}}, ('created_work',))


def test_browser_failure_cannot_be_hidden_by_true_checks():
    with pytest.raises(RuntimeError, match='BROWSER_CHECK_FAILED'):
        local.validate_browser_report({'result': 'FAIL', 'checks': {'created_work': True}}, ('created_work',))


def test_file_recovery_browser_cannot_pass_without_required_ui_evidence(tmp_path, monkeypatch):
    import json
    import subprocess
    (tmp_path / 'browser-recovery-exercise.json').write_text(json.dumps({'result': 'PASS', 'checks': {}}))
    monkeypatch.setattr(local.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0))
    with pytest.raises(RuntimeError, match='BROWSER_CHECK_FAILED'):
        local.recovery_browser_phase('http://127.0.0.1:1234', tmp_path, 'exercise', tmp_path / 'fixture.json')


def test_final_ui_bytes_check_preserves_unselected_file(tmp_path):
    (tmp_path / 'A.txt').write_bytes(b'changed-A\n')
    (tmp_path / 'C.txt').write_bytes(b'new-C\n')
    assert local.ui_bytes_after_undo(tmp_path) is True
    (tmp_path / 'C.txt').write_bytes(b'other-C\n')
    assert local.ui_bytes_after_undo(tmp_path) is False


@pytest.mark.parametrize('failure', [TypeError('invalid response'), KeyboardInterrupt()])
def test_new_run_replaces_old_pass_even_when_interrupted(tmp_path, monkeypatch, failure):
    import json
    destination = tmp_path / 'report.json'
    destination.write_text('{"result":"PASS"}')

    def interrupted(_value):
        assert json.loads(destination.read_text())['result'] == 'RUNNING'
        raise failure

    monkeypatch.setattr(local, 'validate_temp_parent', interrupted)
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            local.main(['--report-dir', str(tmp_path)])
    else:
        assert local.main(['--report-dir', str(tmp_path)]) == 1
    assert json.loads(destination.read_text())['result'] == 'FAIL'
