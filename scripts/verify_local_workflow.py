"""Real local Core/browser/file acceptance. No demo APIs or model requests."""

import argparse
import json
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentguard.api.server import create_app

CREATE_CHECKS = ('created_work', 'created_task', 'manual_artifact', 'layout_saved',
                 'language_preserved_content', 'review_separate_from_acceptance', 'owner_accepted')
REOPEN_CHECKS = ('work_persisted', 'artifact_persisted', 'layout_persisted', 'acceptance_persisted', 'mobile_readable')
RECOVERY_UI_CHECKS = ('ui_global_directory_and_version', 'ui_preview_read_only', 'ui_actual_diff',
                      'ui_selective_restore', 'ui_undo_restore', 'ui_expired_preview_rejected',
                      'ui_mobile_confirm_accessible', 'ui_keyboard_focus')
RECOVERY_REOPEN_CHECKS = ('ui_recovery_history_after_restart',)
REQUIRED_CHECKS = ('browser_work_delivery', 'native_versions_and_attribution', 'preview_is_read_only',
                   'actual_diff', 'selective_restore', 'operation_idempotency', 'undo_restore',
                   'stale_preview_rejected', 'operation_survives_restart',
                   'browser_reopens_after_core_restart', 'database_integrity', 'no_execution_created',
                   *RECOVERY_UI_CHECKS, *RECOVERY_REOPEN_CHECKS, 'ui_final_bytes')


def validate_temp_parent(value):
    parent = Path(value).resolve(strict=True)
    if parent.parts[1:2] and parent.parts[1] in {
        'bin', 'boot', 'dev', 'etc', 'lib', 'lib64', 'proc', 'run', 'sbin', 'sys', 'usr', 'var',
    }:
        raise ValueError('TEMP_PARENT_SYSTEM_SCOPE')
    if any((ancestor / '.git').exists() for ancestor in (parent, *parent.parents)):
        raise ValueError('TEMP_PARENT_INSIDE_GIT')
    return parent


def validate_browser_report(report, required):
    if report.get('result') != 'PASS' or any(report.get('checks', {}).get(name) is not True for name in required):
        raise RuntimeError('BROWSER_CHECK_FAILED')


def wait_for(read, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.05)
    raise RuntimeError('LOCAL_WORKFLOW_TIMEOUT')


class CoreServer:
    """Own a random loopback socket and an ordinary, unmodified Core instance."""

    def __init__(self, state):
        self.state = state

    def __enter__(self):
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.url = f'http://127.0.0.1:{self.socket.getsockname()[1]}'
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(state_db_path=self.state / 'state.db', config={'base_dir': str(self.state)}),
            log_level='error', access_log=False,
        ))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, daemon=True)
        self.thread.start()
        try:
            wait_for(lambda: self.server.started, bool)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_args):
        self.server.should_exit = True
        self.thread.join(timeout=15)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError('CORE_SERVER_CLEANUP_FAILED')


def browser_phase(url, directory, phase, work_id=None):
    command = ['node', str(ROOT / 'scripts/verify_local_workflow_browser.cjs'), '--url', url,
               '--report-dir', str(directory), '--phase', phase]
    if work_id:
        command += ['--work-id', work_id]
    with (directory / f'browser-{phase}.log').open('w', encoding='utf-8') as output:
        completed = subprocess.run(command, check=False, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, timeout=120)
    if completed.returncode:
        raise RuntimeError('BROWSER_PROCESS_FAILED: ' + phase)
    result = json.loads((directory / f'browser-{phase}.json').read_text(encoding='utf-8'))
    validate_browser_report(result, CREATE_CHECKS if phase == 'create' else REOPEN_CHECKS)
    return result


def recovery_browser_phase(url, directory, phase, fixture):
    command = ['node', str(ROOT / 'scripts/verify_file_recovery_browser.cjs'), '--url', url,
               '--report-dir', str(directory), '--phase', phase, '--fixture', str(fixture)]
    with (directory / f'browser-recovery-{phase}.log').open('w', encoding='utf-8') as output:
        completed = subprocess.run(command, check=False, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, timeout=120)
    if completed.returncode:
        raise RuntimeError('RECOVERY_BROWSER_PROCESS_FAILED: ' + phase)
    result = json.loads((directory / f'browser-recovery-{phase}.json').read_text(encoding='utf-8'))
    validate_browser_report(result, RECOVERY_UI_CHECKS if phase == 'exercise' else RECOVERY_REOPEN_CHECKS)
    return result


def ui_bytes_after_undo(project):
    return ((project / 'A.txt').read_bytes() == b'changed-A\n' and not (project / 'B.txt').exists()
            and (project / 'C.txt').read_bytes() == b'new-C\n')


def prepare_ui_fixture(client, project, state, work_id):
    """Only prepare disposable real files; recovery itself must use the browser UI."""
    project.mkdir()
    (project / 'A.txt').write_bytes(b'original-A\n')
    (project / 'B.txt').write_bytes(b'original-B\n')
    scope = request(client, 'POST', '/protection/scopes',
                    {'request_id': str(uuid4()), 'source_id': 'local', 'path': str(project)}, 201)
    route = '/protection/scopes/' + scope['scope_id']
    baseline = request(client, 'GET', route + '/versions')['items'][0]
    (project / 'A.txt').write_bytes(b'changed-A\n')
    (project / 'B.txt').unlink()
    (project / 'C.txt').write_bytes(b'new-C\n')
    request(client, 'POST', route + '/capture', {})
    return {'work_id': work_id, 'scope_id': scope['scope_id'], 'version_id': baseline['id'],
            'project': str(project), 'database': str(state / 'state.db'), 'python': sys.executable}


def request(client, method, path, body=None, expected=200):
    response = client.request(method, '/api/v1' + path, json=body)
    if response.status_code != expected:
        raise RuntimeError(f'LOCAL_HTTP_STATUS: {method} {path} {response.status_code}')
    return response.json()


def protect_and_restore(client, project, checks):
    def post(path, body, expected=200):
        return request(client, 'POST', path, body, expected)

    def get(path):
        return request(client, 'GET', path)

    def execute(preview, request_id):
        command = {'request_id': request_id, 'preview_id': preview['preview_id'], 'digest': preview['digest']}
        queued = post('/recovery/operations', command, 202)
        result = wait_for(lambda: get('/recovery/operations/' + queued['id']),
                          lambda item: item['state'] not in {'QUEUED', 'RUNNING'})
        return result, command

    a, b, c = project / 'A.txt', project / 'B.txt', project / 'C.txt'
    scope = post('/protection/scopes', {'request_id': str(uuid4()), 'source_id': 'local', 'path': str(project)}, 201)
    route = '/protection/scopes/' + scope['scope_id']
    first = get(route + '/versions')['items'][0]
    ids = {item['relative_path']: item['file_id'] for item in first['files']}
    a.write_bytes(b'changed-A\n')
    b.unlink()
    c.write_bytes(b'new-C\n')
    post(route + '/capture', {})
    changes = get(route + '/changes')['items']
    checks['native_versions_and_attribution'] = bool(changes) and all(item['attribution'] == 'UNATTRIBUTED' for item in changes)
    preview = post('/recovery/previews', {'scope_id': scope['scope_id'], 'mode': 'restore_version',
                   'version_id': first['id'], 'file_ids': [ids['A.txt'], ids['B.txt']]})
    checks['preview_is_read_only'] = a.read_bytes() == b'changed-A\n' and not b.exists()
    difference = get(f"/recovery/previews/{preview['preview_id']}/files/{ids['A.txt']}/diff")
    checks['actual_diff'] = '-changed-A' in difference['text_diff'] and '+original-A' in difference['text_diff']
    result, command = execute(preview, str(uuid4()))
    checks['selective_restore'] = result['state'] == 'SUCCEEDED' and a.read_bytes() == b'original-A\n' and b.read_bytes() == b'original-B\n' and c.read_bytes() == b'new-C\n'
    checks['operation_idempotency'] = post('/recovery/operations', command, 202)['id'] == result['id']
    undo = post('/recovery/previews', {'scope_id': scope['scope_id'], 'mode': 'undo_operation',
                'operation_id': result['id'], 'file_ids': [ids['A.txt'], ids['B.txt']]})
    undone, _ = execute(undo, str(uuid4()))
    checks['undo_restore'] = undone['state'] == 'SUCCEEDED' and a.read_bytes() == b'changed-A\n' and not b.exists() and c.read_bytes() == b'new-C\n'
    stale = post('/recovery/previews', {'scope_id': scope['scope_id'], 'mode': 'restore_version',
                 'version_id': first['id'], 'file_ids': [ids['A.txt']]})
    a.write_bytes(b'external-edit\n')
    rejected, _ = execute(stale, str(uuid4()))
    checks['stale_preview_rejected'] = rejected['state'] == 'REJECTED' and a.read_bytes() == b'external-edit\n'
    return result['id']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--temp-root', type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument('--report-dir', type=Path, default=ROOT / 'artifacts/local-workflow')
    args = parser.parse_args(argv)
    directory = args.report_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = {'result': 'RUNNING', 'scope': 'manual Work + native file protection; no model calls',
              'environment': platform.system(), 'checked_at': time.time(), 'run_id': str(uuid4()), 'checks': {}}
    destination = directory / 'report.json'
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    checks = report['checks']
    try:
        parent = validate_temp_parent(args.temp_root)
        with tempfile.TemporaryDirectory(prefix='asg-local-workflow-', dir=parent) as temporary:
            root = Path(temporary)
            state, project = root / 'state', root / 'project'
            state.mkdir()
            project.mkdir()
            (project / 'A.txt').write_bytes(b'original-A\n')
            (project / 'B.txt').write_bytes(b'original-B\n')
            with CoreServer(state) as server:
                created = browser_phase(server.url, directory, 'create')
                checks['browser_work_delivery'] = True
                with httpx.Client(base_url=server.url, timeout=15, trust_env=False) as client:
                    client.headers['X-Session-Token'] = client.get('/api/session').json()['token']
                    operation_id = protect_and_restore(client, project, checks)
                    ui_project = root / 'ui-project'
                    fixture = root / 'ui-recovery-fixture.json'
                    fixture.write_text(json.dumps(prepare_ui_fixture(client, ui_project, state, created['work_id'])), encoding='utf-8')
                ui = recovery_browser_phase(server.url, directory, 'exercise', fixture)
                checks.update({name: ui['checks'][name] for name in RECOVERY_UI_CHECKS})
                checks['ui_final_bytes'] = ui_bytes_after_undo(ui_project)
            with CoreServer(state) as server:
                with httpx.Client(base_url=server.url, timeout=15, trust_env=False) as client:
                    client.headers['X-Session-Token'] = client.get('/api/session').json()['token']
                    checks['operation_survives_restart'] = request(client, 'GET', '/recovery/operations/' + operation_id)['state'] == 'SUCCEEDED'
                browser_phase(server.url, directory, 'reopen', created['work_id'])
                checks['browser_reopens_after_core_restart'] = True
                ui = recovery_browser_phase(server.url, directory, 'reopen', fixture)
                checks.update({name: ui['checks'][name] for name in RECOVERY_REOPEN_CHECKS})
            with sqlite3.connect(state / 'state.db') as db:
                checks['database_integrity'] = db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                checks['no_execution_created'] = db.execute('SELECT COUNT(*) FROM work_runs').fetchone()[0] == 0 and db.execute('SELECT COUNT(*) FROM work_sessions').fetchone()[0] == 0
            if any(checks.get(name) is not True for name in REQUIRED_CHECKS):
                raise RuntimeError('LOCAL_WORKFLOW_CHECK_FAILED')
            report['result'] = 'PASS'
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, sqlite3.DatabaseError,
            httpx.HTTPError, subprocess.SubprocessError) as error:
        report.update(result='FAIL', reason=str(error)[:500])
    except BaseException:
        report.update(result='FAIL', reason='RUN_INTERRUPTED')
        raise
    finally:
        if report['result'] == 'RUNNING':
            report.update(result='FAIL', reason='RUN_INTERRUPTED')
        destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
