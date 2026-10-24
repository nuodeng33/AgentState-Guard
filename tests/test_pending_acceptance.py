"""Verifier report gate rejects incomplete or merely truthy browser evidence."""
from copy import deepcopy

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
