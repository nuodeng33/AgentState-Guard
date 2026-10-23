"""Disposable real native Codex/API acceptance; never prints credentials or raw RPC."""

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from agentguard.api.server import create_app

REQUIRED_BOOLEAN_CHECKS = (
    'actual_files', 'retry_same_run', 'durable_output', 'sequential_events',
    'attribution_unclaimed', 'exact_native_reconciliation',
    'fixed_delivery_acceptance', 'continue_exact_session', 'selective_restore',
)


def finalize_report(report):
    """A missing, false or merely truthy check cannot certify live acceptance."""
    report.pop('result', None)
    checks = report.get('checks', {})
    for name in REQUIRED_BOOLEAN_CHECKS:
        if checks.get(name) is not True:
            raise RuntimeError('LIVE_ACCEPTANCE_CHECK_FAILED: ' + name)
    for name, state in (('connection', 'READY'), ('protocol_cancel', 'CANCELLED')):
        if not isinstance(checks.get(name), dict) or checks[name].get('state') != state:
            raise RuntimeError('LIVE_ACCEPTANCE_CHECK_FAILED: ' + name)
    report['result'] = 'PASS'


def wait(read, predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if predicate(result):
            return result
        time.sleep(0.1)
    raise RuntimeError('LIVE_ACCEPTANCE_TIMEOUT: ' + str(result.get('state', result.get('reason_code'))))


def main(report, *, temp_root=None):
    with tempfile.TemporaryDirectory(prefix='asg-managed-live-', dir=temp_root) as temporary:
        root = Path(temporary)
        state, project = root / 'state', root / 'project'
        state.mkdir()
        project.mkdir()
        (project / 'A.txt').write_text('old-A\n', encoding='utf-8')
        (project / 'B.txt').write_text('old-B\n', encoding='utf-8')
        app = create_app(state_db_path=state / 'state.db', config={'base_dir': str(state)}, product_startup_discovery=True)
        with TestClient(app) as client:
            client.headers['X-Session-Token'] = client.get('/api/session').json()['token']
            def post(path, body, expected=201):
                response = client.post('/api/v1' + path, json=body)
                if response.status_code != expected:
                    raise RuntimeError('LIVE_API_REJECTED: ' + path + ' ' + str(response.json().get('reason_code', response.status_code)))
                return response.json()
            def get(path):
                response = client.get('/api/v1' + path)
                response.raise_for_status()
                return response.json()
            application = post('/discovery/applications', {'source_id': 'local', 'path': shutil.which('codex')})
            store_path = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
            store = post('/discovery/session-stores', {'source_id': 'local', 'provider': 'CODEX', 'path': store_path})
            scope = post('/protection/scopes', {'request_id': str(uuid4()), 'source_id': 'local', 'path': str(project)})
            work = post('/works', {'request_id': str(uuid4()), 'title': 'Disposable managed acceptance', 'owner': 'Acceptance',
                                   'requirement': 'Change A, delete B, add C only in the selected directory.'})
            work_root = '/works/' + work['work_id']
            created = post(work_root + '/sessions', {'request_id': str(uuid4()), 'expected_revision': work['revision'],
                'application_id': application['application_id'], 'store_id': store['store_id'], 'scope_id': scope['scope_id'],
                'mode': 'NEW', 'read_only': False, 'control_consent': True})
            session_root = work_root + '/sessions/' + created['session_id']
            session = wait(lambda: get(session_root), lambda row: row['state'] != 'CONNECTING', timeout=60)
            report['checks']['connection'] = {'state': session['state'], 'reason_code': session['reason_code'],
                                               'capabilities': session['capabilities'], 'native_session_id': session['native_session_id']}
            if session['state'] != 'READY':
                raise RuntimeError('LIVE_SESSION_NOT_READY: ' + session['reason_code'])
            command = {'request_id': str(uuid4()), 'expected_revision': session['revision'], 'requirement_version': 1,
                'input': 'Using workspace tools: replace A.txt with asg-managed-ok, delete B.txt, create C.txt containing asg-added-ok. Do not change other files. Reply asg-acceptance-ok when done.', 'context_refs': []}
            started_at = time.monotonic()
            run = post(session_root + '/runs', command)
            run_root = session_root + '/runs/' + run['run_id']
            result = wait(lambda: get(run_root), lambda row: row['state'] in {'COMPLETED', 'FAILED', 'CANCELLED', 'UNCERTAIN', 'WAITING_APPROVAL', 'WAITING_INPUT'})
            report['checks']['execution'] = {'state': result['state'], 'reason_code': result['reason_code'],
                                              'file_result': result['file_result'], 'native_turn_id': result['native_turn_id'],
                                              'seconds': time.monotonic() - started_at}
            if result['state'] != 'COMPLETED':
                if result['state'].startswith('WAITING'):
                    post(run_root + '/cancel', {'request_id': str(uuid4())}, 200)
                raise RuntimeError('LIVE_RUN_NOT_COMPLETED: ' + result['state'])
            actual = (project / 'A.txt').read_text(encoding='utf-8').strip() == 'asg-managed-ok' and not (project / 'B.txt').exists() and (project / 'C.txt').read_text(encoding='utf-8').strip() == 'asg-added-ok'
            report['checks']['actual_files'] = actual
            report['checks']['retry_same_run'] = post(session_root + '/runs', command)['run_id'] == run['run_id']
            events = get(run_root + '/events')['items']
            report['checks']['durable_output'] = any(event['type'] == 'TEXT' for event in events)
            report['checks']['sequential_events'] = [e['seq'] for e in events] == list(range(1, len(events) + 1))
            changes = get('/protection/scopes/' + scope['scope_id'] + '/changes')['items']
            report['checks']['file_changes'] = [entry['relative_path'] for batch in changes for entry in batch['entries']]
            report['checks']['attribution_unclaimed'] = all(batch['attribution'] == 'UNATTRIBUTED' for batch in changes)
            if not actual or not report['checks']['durable_output']:
                raise RuntimeError('LIVE_FILE_OR_OUTPUT_MISMATCH')
            # Simulate local status loss after a genuine provider completion.
            with app.state.execution.journal() as service, service.db.transaction() as conn:
                conn.execute("UPDATE work_runs SET state='UNCERTAIN',completed_at=NULL WHERE id=?", (run['run_id'],))
            reconciled = post(work_root + '/runs/' + run['run_id'] + '/reconcile', {'request_id': str(uuid4())}, 200)
            result = wait(lambda: get(run_root), lambda row: row['state'] == 'COMPLETED', timeout=30)
            report['checks']['exact_native_reconciliation'] = reconciled['native_status'] == 'completed'
            artifact = post(work_root + '/runs/' + run['run_id'] + '/artifacts', {'request_id': str(uuid4()),
                'expected_revision': get(work_root)['revision'], 'title': 'Real provider output'})
            submission = post(work_root + '/submissions', {'request_id': str(uuid4()), 'expected_revision': artifact['revision'],
                'requirement_version': 1, 'run_id': run['run_id'], 'responsibility': 'Acceptance',
                'artifact_refs': [{'artifact_id': artifact['artifact_id'], 'version': artifact['version']}],
                'file_refs': [{'scope_id': scope['scope_id'], 'version_id': result['file_result']['version_id']}]})
            review = post(work_root + '/submissions/' + submission['submission_id'] + '/reviews', {'request_id': str(uuid4()),
                'expected_revision': submission['revision'], 'decision': 'APPROVED', 'body': 'Verified A/B/C actual bytes.', 'reviewer': 'Acceptance'})
            accepted = post(work_root + '/submissions/' + submission['submission_id'] + '/acceptances', {'request_id': str(uuid4()),
                'expected_revision': review['revision'], 'decision': 'ACCEPTED', 'basis': 'Checked selected output and file versions.', 'owner': 'Acceptance'})
            report['checks']['fixed_delivery_acceptance'] = accepted['decision'] == 'ACCEPTED'
            session = get(session_root)
            post(session_root + '/archive', {'request_id': str(uuid4()), 'expected_revision': session['revision']}, 200)
            continued = post(work_root + '/sessions', {'request_id': str(uuid4()), 'expected_revision': accepted['revision'],
                'application_id': application['application_id'], 'store_id': store['store_id'], 'scope_id': scope['scope_id'],
                'mode': 'CONTINUE', 'binding_id': session['binding_id'], 'read_only': False, 'control_consent': True})
            continued_root = work_root + '/sessions/' + continued['session_id']
            resumed = wait(lambda: get(continued_root), lambda row: row['state'] != 'CONNECTING', timeout=60)
            report['checks']['continue_exact_session'] = resumed['state'] == 'READY' and resumed['native_session_id'] == session['native_session_id']
            if resumed['state'] != 'READY':
                raise RuntimeError('LIVE_CONTINUE_FAILED: ' + resumed['reason_code'])
            cancelled = post(continued_root + '/runs', {'request_id': str(uuid4()), 'expected_revision': resumed['revision'],
                'requirement_version': 1, 'input': 'Read A.txt and explain its contents. Do not change files.', 'context_refs': []})
            cancelled_root = continued_root + '/runs/' + cancelled['run_id']
            wait(lambda: get(cancelled_root), lambda row: bool(row['native_turn_id']) or row['state'] in {'FAILED', 'UNCERTAIN'}, timeout=30)
            post(cancelled_root + '/cancel', {'request_id': str(uuid4())}, 200)
            stopped = wait(lambda: get(cancelled_root), lambda row: row['state'] in {'CANCELLED', 'COMPLETED', 'FAILED', 'UNCERTAIN'}, timeout=60)
            report['checks']['protocol_cancel'] = {'state': stopped['state'], 'provider_result': stopped['result']}
            if stopped['state'] != 'CANCELLED':
                raise RuntimeError('LIVE_CANCEL_NOT_CONFIRMED: ' + stopped['state'])
            versions = get('/protection/scopes/' + scope['scope_id'] + '/versions')['items']
            before = next(v for v in versions if v['id'] == result['baseline_id'])
            selected_files = [entry['file_id'] for entry in before['files'] if entry['relative_path'] in {'A.txt', 'B.txt'}]
            preview = post('/recovery/previews', {'scope_id': scope['scope_id'], 'mode': 'restore_version',
                'version_id': result['baseline_id'], 'file_ids': selected_files}, 200)
            restored = post('/recovery/operations', {'request_id': str(uuid4()), 'preview_id': preview['preview_id'], 'digest': preview['digest']}, 202)
            operation = wait(lambda: get('/recovery/operations/' + restored['id']),
                             lambda row: row['state'] in {'SUCCEEDED', 'REJECTED', 'NEEDS_ATTENTION', 'COMPENSATED'}, timeout=60)
            report['checks']['selective_restore'] = operation['state'] == 'SUCCEEDED' and (project / 'A.txt').read_text(encoding='utf-8') == 'old-A\n' and (project / 'B.txt').read_text(encoding='utf-8') == 'old-B\n' and (project / 'C.txt').read_text(encoding='utf-8').strip() == 'asg-added-ok'
            if not report['checks']['selective_restore']:
                raise RuntimeError('LIVE_RESTORE_FAILED')
            finalize_report(report)
    return report


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', help='Explicitly allow real Codex requests and disposable file writes')
    parser.add_argument('--temp-root', type=Path, help='Existing parent for disposable files, outside Git and system scopes')
    parser.add_argument('--report', type=Path, default=Path(__file__).resolve().parents[1] / 'artifacts/live-acceptance/native-live.json')
    args = parser.parse_args(argv)
    result = {'provider': 'CODEX', 'environment': platform.system(), 'checked_at': time.time(), 'run_id': str(uuid4()), 'result': 'RUNNING', 'checks': {}}
    destination = args.report
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding='utf-8')
    try:
        if not args.run_live:
            result.update(result='BLOCKED', reason='LIVE_OPT_IN_REQUIRED')
        else:
            main(result, temp_root=args.temp_root)
            finalize_report(result)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, httpx.HTTPError) as error:
        result.update(result='FAIL', reason=str(error)[:500])
    except BaseException:
        result.update(result='FAIL', reason='RUN_INTERRUPTED')
        raise
    finally:
        if result.get('result') in {None, 'RUNNING'}:
            result.update(result='FAIL', reason='RUN_INTERRUPTED')
        destination.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['result'] == 'PASS' else 2 if result['result'] == 'BLOCKED' else 1


if __name__ == '__main__':
    sys.exit(run_cli())
