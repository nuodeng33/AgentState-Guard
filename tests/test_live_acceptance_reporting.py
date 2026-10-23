"""Real-provider acceptance cannot pass when any required observation failed."""

import json

import pytest

from scripts import verify_managed_session_live as live

BOOLEAN_CHECKS = (
    'actual_files', 'retry_same_run', 'durable_output', 'sequential_events',
    'attribution_unclaimed', 'exact_native_reconciliation',
    'fixed_delivery_acceptance', 'continue_exact_session', 'selective_restore',
)


def report():
    return {'checks': {**dict.fromkeys(BOOLEAN_CHECKS, True),
                       'connection': {'state': 'READY'}, 'protocol_cancel': {'state': 'CANCELLED'}}}


@pytest.mark.parametrize('check', BOOLEAN_CHECKS)
@pytest.mark.parametrize('bad', [False, None, 1])
def test_required_check_must_be_explicitly_true(check, bad):
    result = report()
    result['checks'][check] = bad
    with pytest.raises(RuntimeError, match=check):
        live.finalize_report(result)
    assert result.get('result') != 'PASS'


def test_complete_report_passes():
    result = report()
    live.finalize_report(result)
    assert result['result'] == 'PASS'


def test_missing_check_is_not_success():
    result = report()
    del result['checks']['continue_exact_session']
    with pytest.raises(RuntimeError, match='continue_exact_session'):
        live.finalize_report(result)


def test_cli_requires_explicit_provider_opt_in(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Provider execution must not start without explicit opt-in')
    monkeypatch.setattr(live, 'main', forbidden)
    destination = tmp_path / 'report.json'
    assert live.run_cli(['--report', str(destination)]) == 2
    assert json.loads(destination.read_text())['result'] == 'BLOCKED'


def test_cli_does_not_trust_early_return_or_false_checks(tmp_path, monkeypatch):
    def incomplete(result, **kwargs):
        result.update(report())
        result['checks']['retry_same_run'] = False
    monkeypatch.setattr(live, 'main', incomplete)
    destination = tmp_path / 'report.json'
    assert live.run_cli(['--run-live', '--report', str(destination)]) == 1
    result = json.loads(destination.read_text())
    assert result['result'] == 'FAIL'
    assert 'retry_same_run' in result['reason']


@pytest.mark.parametrize('failure', [TypeError('invalid response'), KeyboardInterrupt()])
def test_live_run_replaces_previous_pass_before_start(tmp_path, monkeypatch, failure):
    destination = tmp_path / 'report.json'
    destination.write_text('{"result":"PASS"}')

    def interrupted(_report, **_kwargs):
        assert json.loads(destination.read_text())['result'] == 'RUNNING'
        raise failure

    monkeypatch.setattr(live, 'main', interrupted)
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            live.run_cli(['--run-live', '--report', str(destination)])
    else:
        assert live.run_cli(['--run-live', '--report', str(destination)]) == 1
    assert json.loads(destination.read_text())['result'] == 'FAIL'


def test_revalidation_removes_previous_pass():
    result = report()
    live.finalize_report(result)
    result['checks']['retry_same_run'] = False
    with pytest.raises(RuntimeError):
        live.finalize_report(result)
    assert result.get('result') != 'PASS'


def test_interruption_after_internal_pass_cannot_leave_success(tmp_path, monkeypatch):
    destination = tmp_path / 'report.json'

    def interrupted(result, **_kwargs):
        result.update(report())
        live.finalize_report(result)
        raise KeyboardInterrupt()

    monkeypatch.setattr(live, 'main', interrupted)
    with pytest.raises(KeyboardInterrupt):
        live.run_cli(['--run-live', '--report', str(destination)])
    assert json.loads(destination.read_text())['result'] == 'FAIL'
