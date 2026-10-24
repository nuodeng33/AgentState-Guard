"""Verifier report gate rejects incomplete or merely truthy browser evidence."""
import sqlite3
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.verify_pending_instruction import (
    REQUIRED_BROWSER_CHECKS,
    validate_browser_report,
)


def complete(phase):
    return {'result': 'PASS', 'phase': phase, 'checks': dict.fromkeys(REQUIRED_BROWSER_CHECKS[phase], True)}


@pytest.mark.parametrize('phase', ['exercise', 'reopen'])
def test_complete_phase_report_is_accepted(phase):
    report = complete(phase)
    assert validate_browser_report(report, phase) is report


@pytest.mark.parametrize('phase', ['exercise', 'reopen'])
@pytest.mark.parametrize('mutation', ['missing', 'false', 'string', 'integer', 'extra', 'empty', 'list', 'null', 'wrong_phase', 'fail', 'missing_result'])
def test_incomplete_or_malformed_checks_never_pass(phase, mutation):
    report = deepcopy(complete(phase))
    key = next(iter(report['checks']))
    if mutation == 'missing':
        report['checks'].pop(key)
    elif mutation == 'false':
        report['checks'][key] = False
    elif mutation == 'string':
        report['checks'][key] = 'true'
    elif mutation == 'integer':
        report['checks'][key] = 1
    elif mutation == 'extra':
        report['checks']['unreviewed_success'] = True
    elif mutation == 'empty':
        report['checks'] = {}
    elif mutation == 'list':
        report['checks'] = [True]
    elif mutation == 'null':
        report['checks'] = None
    elif mutation == 'wrong_phase':
        report['phase'] = 'reopen' if phase == 'exercise' else 'exercise'
    elif mutation == 'fail':
        report['result'] = 'FAIL'
    else:
        report.pop('result')
    with pytest.raises(ValueError, match='QUEUE_BROWSER_REPORT_INVALID'):
        validate_browser_report(report, phase)


@pytest.mark.parametrize('report', [None, [], 'PASS', True, 1])
def test_non_object_report_never_passes(report):
    with pytest.raises(ValueError, match='QUEUE_BROWSER_REPORT_INVALID'):
        validate_browser_report(report, 'exercise')


@pytest.mark.parametrize('phase', ['unknown', '', None, [], {}])
def test_unknown_or_malformed_requested_phase_never_passes(phase):
    with pytest.raises(ValueError, match='QUEUE_BROWSER_REPORT_INVALID'):
        validate_browser_report(complete('exercise'), phase)


def diagnostic_core(tmp_path):
    from scripts.verify_pending_instruction import Core
    core = Core.__new__(Core)
    core.db_path = tmp_path / 'state.db'
    core.runtime = SimpleNamespace(thread=None, last_error='EXECUTION_WORKER_UNAVAILABLE', accepting=False)
    core.app = SimpleNamespace(state=SimpleNamespace(protection=SimpleNamespace(thread=None, last_error=None)))
    return core


def test_failed_core_enter_retains_worker_and_schema_evidence_and_closes_socket(tmp_path, monkeypatch):
    from scripts import verify_pending_instruction as verifier
    core = diagnostic_core(tmp_path)
    with sqlite3.connect(core.db_path) as connection:
        connection.executescript('CREATE TABLE schema_migrations(version INTEGER); INSERT INTO schema_migrations VALUES(20); CREATE TABLE work_runs(id TEXT);')
    entered = threading.Event()

    class Server:
        started = False
        should_exit = False
        def __init__(self, _config):
            pass
        def run(self, *, sockets):
            self.started = True
            entered.set()
            while not self.should_exit:
                threading.Event().wait(.001)

    def fail_wait(*_args, **_kwargs):
        assert entered.wait(1)
        raise verifier.AcceptanceTimeout('core_start', False)

    monkeypatch.setattr(verifier.uvicorn, 'Server', Server)
    monkeypatch.setattr(verifier, 'wait', fail_wait)
    with pytest.raises(verifier.AcceptanceTimeout) as captured:
        core.__enter__()
    diagnostic = captured.value.diagnostic
    assert diagnostic['core']['server_started'] is True
    assert diagnostic['core']['execution_accepting'] is False
    assert diagnostic['core']['execution']['last_error'] == 'EXECUTION_WORKER_UNAVAILABLE'
    assert diagnostic['core']['schema'] == {
        'table_count': 2, 'work_runs': True, 'pending_instructions': False, 'versions': [20]}
    assert diagnostic['after_cleanup']['server_thread_alive'] is False
    assert core.socket.fileno() == -1
    assert not core.thread.is_alive()


def test_startup_diagnostics_do_not_create_or_migrate_database(tmp_path):
    core = diagnostic_core(tmp_path)
    core.server = SimpleNamespace(started=True)
    core.thread = SimpleNamespace(is_alive=lambda: True)
    assert core.startup_snapshot()['database_exists'] is False
    assert not core.db_path.exists()
    with sqlite3.connect(core.db_path) as connection:
        connection.executescript('CREATE TABLE schema_migrations(version INTEGER); INSERT INTO schema_migrations VALUES(1);')
    before = core.db_path.read_bytes()
    assert core.startup_snapshot()['schema'] == {
        'table_count': 1, 'work_runs': False, 'pending_instructions': False, 'versions': [1]}
    assert core.db_path.read_bytes() == before
