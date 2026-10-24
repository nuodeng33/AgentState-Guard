"""Isolated actual Core/SQLite + production browser queue acceptance.

Provider messages are a controlled no-model fixture. No user credentials,
host UI, ADB, provider executable, or production test hook is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from agentguard.api.server import create_app
from agentguard.device_link.product import UnavailableDeviceLinkController
from agentguard.discovery.application_probe import probe_entry
from agentguard.discovery.session_metadata import inspect_store
from agentguard.work.models import CreateWork
from tests.test_execution_core import Discovery
from tests.test_pending_instructions import QueueProvider

REQUIRED_BROWSER_CHECKS = {
    'exercise': frozenset({
        'explicit_queue_immutable_exact_text', 'one_slot_no_early_dispatch_and_draft_clear',
        'real_completed_file_result_precedes_child_once', 'promoted_execution_visible',
        'stop_cancels_current_and_pending_no_next_send', 'durable_pending_prepared_for_restart',
        'no_model_calls', 'no_page_errors',
    }),
    'reopen': frozenset({
        'reopened_held_exact_text', 'no_automatic_resubmit_or_replay', 'no_model_calls', 'no_page_errors',
    }),
}


def validate_browser_report(report, phase):
    """Only the complete, exact set of literal successful phase checks can pass."""
    required = REQUIRED_BROWSER_CHECKS.get(phase) if isinstance(phase, str) else None
    if not required or not isinstance(report, dict) or report.get('phase') != phase or report.get('result') != 'PASS':
        raise ValueError('QUEUE_BROWSER_REPORT_INVALID')
    checks = report.get('checks')
    if not isinstance(checks, dict) or set(checks) != required or any(value is not True for value in checks.values()):
        raise ValueError('QUEUE_BROWSER_REPORT_INVALID')
    return report


class AcceptanceTimeout(RuntimeError):
    def __init__(self, stage, value):
        super().__init__("QUEUE_ACCEPTANCE_TIMEOUT")
        self.diagnostic = {"stage": stage, "last_observation": value}


def wait(read, predicate, timeout=15, stage="core_start"):
    value = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.025)
    raise AcceptanceTimeout(stage, value)


class Core:
    def __init__(self, state, owner):
        self.owner, self.providers = owner, []
        core_state = state / 'core'
        core_state.mkdir(exist_ok=True)
        self.app = create_app(state_db_path=core_state / 'state.db', config={'base_dir': str(core_state)},
                              device_link_controller=UnavailableDeviceLinkController(), discovery_service=Discovery())
        self.runtime = self.app.state.execution

        def factory(session, event):
            provider = QueueProvider(session, event, owner)
            self.providers.append(provider)
            return provider
        self.runtime.provider_factory = factory

    def __enter__(self):
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.url = 'http://127.0.0.1:' + str(self.socket.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, daemon=True)
        self.thread.start()
        wait(lambda: self.server.started and self.runtime.accepting, bool)
        return self

    def __exit__(self, *_args):
        self.server.should_exit = True
        self.thread.join(timeout=15)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError('CORE_CLEANUP_FAILED')


def seed(core, state):
    project = state / 'project'
    project.mkdir()
    (project / 'A.txt').write_text('Original bytes', encoding='utf-8')
    store = state / 'store'
    (store / 'sessions').mkdir(parents=True)
    core.owner['workspace'] = project
    with core.runtime.service() as service:
        work = service.work.create(CreateWork(request_id=uuid4(), title='Pending instruction acceptance',
            owner='Acceptance', requirement='Verify explicit next input using controlled protocol messages.'))
        app = service.catalog._save_application('local', probe_entry(sys.executable), registered=True)
        with service.db.transaction() as conn:
            conn.execute("UPDATE discovery_applications SET metadata_json=json_set(metadata_json,'$.provider','CODEX') WHERE id=?", (app,))
        store_id = service.catalog._save_store('local', 'CODEX', str(store), inspect_store('CODEX', store), registered=True)
        scope = service.protection.register('local', str(project), str(uuid4()))['scope_id']
    with httpx.Client(base_url=core.url, timeout=10) as client:
        client.headers['X-Session-Token'] = client.get('/api/session').json()['token']
        created = client.post('/api/v1/works/' + work['work_id'] + '/sessions', json={
            'request_id': str(uuid4()), 'expected_revision': 1, 'application_id': app,
            'store_id': store_id, 'scope_id': scope, 'control_consent': True})
        if created.status_code != 201:
            raise RuntimeError('SESSION_CREATE_' + created.json().get('reason_code', str(created.status_code)))
        session_id = created.json()['session_id']
        wait(lambda: client.get('/api/v1/works/' + work['work_id'] + '/sessions/' + session_id).json(),
             lambda row: row['state'] == 'READY', stage='session_ready')
    return {'work_id': work['work_id'], 'session_id': session_id}


def control_server(core, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != '/drive' or not secrets.compare_digest(self.headers.get('X-Verify-Token', ''), token):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 128:
                    raise ValueError()
                action = json.loads(self.rfile.read(length))['action']
                if action == 'complete':
                    core.owner['finish_next'] = True
                    core.providers[0].finish()
                    result = {'completed': True}
                elif action == 'wait':
                    core.owner['finish_next'] = False
                    result = {'waiting': True}
                elif action == 'inspect':
                    result = {'sends': core.owner['sends'], 'cancels': core.owner['cancels']}
                else:
                    raise ValueError()
                code = 200
            except (ValueError, RuntimeError, KeyError, IndexError):
                code, result = 500, {'reason': 'CONTROL_FAILED'}
            body = json.dumps(result).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def browser(core, owner, directory, temporary, identifiers, phase):
    token = secrets.token_hex(32)
    server, thread = control_server(core, token)
    try:
        fixture = temporary / 'fixture.json'
        fixture.write_text(json.dumps({**identifiers, 'url': core.url, 'phase': phase,
            'driver_url': 'http://127.0.0.1:' + str(server.server_address[1]) + '/drive',
            'driver_token': token, 'initial_sends': owner['sends']}), encoding='utf-8')
        destination = directory / phase
        destination.mkdir()
        with (destination / 'browser.log').open('w', encoding='utf-8') as log:
            completed = subprocess.run(['node', str(ROOT / 'scripts/verify_pending_instruction_browser.cjs'),
                str(fixture), str(destination)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                timeout=150, check=False)
        result = json.loads((destination / 'report.json').read_text(encoding='utf-8'))
        if completed.returncode:
            reason = result.get('reason') if isinstance(result, dict) else None
            raise RuntimeError(reason if isinstance(reason, str) and reason.isupper() and reason.replace('_', '').isalnum() else 'QUEUE_BROWSER_FAILED')
        return validate_browser_report(result, phase)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError('DRIVER_CLEANUP_FAILED')


def http_phase(core, identifiers):
    checks = {}
    session_id, work_id = identifiers['session_id'], identifiers['work_id']
    base = '/api/v1/works/' + work_id + '/sessions/' + session_id
    with httpx.Client(base_url=core.url, timeout=10) as client:
        client.headers['X-Session-Token'] = client.get('/api/session').json()['token']

        def post(path, body):
            response = client.post(path, json=body)
            if response.status_code not in {200, 201}:
                raise RuntimeError('HTTP_QUEUE_ACTION_FAILED')
            return response.json()

        def start(text):
            session = client.get(base).json()
            return post(base + '/runs', {'request_id': str(uuid4()), 'expected_revision': session['revision'],
                'requirement_version': 1, 'input': text, 'context_refs': []})['run_id']

        def enqueue(predecessor, text):
            session = client.get(base).json()
            return post(base + '/pending-instruction', {'request_id': str(uuid4()),
                'expected_revision': session['revision'], 'requirement_version': 1, 'input': text,
                'context_refs': [], 'predecessor_run_id': predecessor})

        predecessor = start('HTTP predecessor')
        wait(lambda: client.get(base + '/runs/' + predecessor).json(), lambda row: row['state'] == 'RUNNING', stage='predecessor_running')
        text = 'HTTP exact next input\nKeep newline'
        receipt = enqueue(predecessor, text)
        item = client.get(base + '/pending-instruction').json()['item']
        if item['input'] != text or item['state'] != 'PENDING' or core.owner['sends'] != 1:
            raise RuntimeError('HTTP_QUEUE_EARLY_DISPATCH_OR_INPUT_CHANGED')
        checks['explicit_pending_exact_input_no_early_send'] = True
        core.owner['finish_next'] = True
        core.providers[0].finish()
        item = wait(lambda: client.get(base + '/pending-instruction').json()['item'], lambda row: row['state'] == 'PROMOTED', stage='queue_promoted')
        child = wait(lambda: client.get(base + '/runs/' + item['promoted_run_id']).json(), lambda row: row['state'] == 'COMPLETED', stage='child_completed')
        if child['file_result']['status'] != 'AVAILABLE' or core.owner['sends'] != 2 or not core.owner['prompts'][1].endswith('Current input:\n' + text):
            raise RuntimeError('HTTP_QUEUE_PROMOTION_FAILED')
        checks['real_completed_file_results_and_exact_child_send_once'] = True
        core.owner['finish_next'] = False
        stopped = start('HTTP predecessor to stop')
        wait(lambda: client.get(base + '/runs/' + stopped).json(), lambda row: row['state'] == 'RUNNING', stage='predecessor_running')
        enqueue(stopped, 'Never send after stop')
        post(base + '/runs/' + stopped + '/cancel', {'request_id': str(uuid4())})
        wait(lambda: client.get(base + '/runs/' + stopped).json(), lambda row: row['state'] == 'CANCELLED', stage='stop_cancelled')
        if client.get(base + '/pending-instruction').json()['item']['state'] != 'CANCELLED' or core.owner['sends'] != 3:
            raise RuntimeError('HTTP_STOP_FAILED')
        checks['stop_cancels_both_without_next_send'] = True
        restart = start('HTTP predecessor before restart')
        wait(lambda: client.get(base + '/runs/' + restart).json(), lambda row: row['state'] == 'RUNNING', stage='predecessor_running')
        enqueue(restart, 'Keep until manual review')
        checks['pending_saved_before_restart'] = True
        if not receipt['pending_id']:
            raise RuntimeError('HTTP_QUEUE_RECEIPT_MISSING')
    return {'result': 'PASS', 'scope': 'real loopback Core HTTP with controlled provider; NO browser', 'checks': checks}


def http_reopen(core, identifiers, sends):
    base = '/api/v1/works/' + identifiers['work_id'] + '/sessions/' + identifiers['session_id']
    with httpx.Client(base_url=core.url, timeout=10) as client:
        client.headers['X-Session-Token'] = client.get('/api/session').json()['token']
        item = client.get(base + '/pending-instruction').json()['item']
        if item['state'] != 'HELD' or item['input'] != 'Keep until manual review' or core.owner['sends'] != sends:
            raise RuntimeError('HTTP_RESTART_REPLAYED_OR_LOST_PENDING')
    return {'result': 'PASS', 'scope': 'actual restarted Core HTTP; NO browser',
            'checks': {'held_original_text_no_replay': True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', required=True, type=Path)
    parser.add_argument('--http-only', action='store_true', help='Actual Core HTTP only; explicitly excludes browser UI acceptance')
    args = parser.parse_args()
    directory = args.report_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = {'result': 'RUNNING', 'scope': 'actual production browser/Core/SQLite with no-model provider fixture',
              'exclusions': ['real provider execution', 'model quality', 'Windows host UI', 'ADB', 'user data']}
    owner = {'creates': 0, 'sends': 0, 'cancels': 0, 'prompts': []}
    try:
        if args.http_only:
            report['scope'] = 'actual Core HTTP/SQLite with no-model protocol fixture; NO browser UI'
            report['exclusions'].append('browser UI')
        index = ROOT / 'agentguard/web_static/index.html'
        report['frontend_index_sha256'] = hashlib.sha256(index.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='asg-queue-browser-') as temporary:
            state = Path(temporary)
            with Core(state, owner) as core:
                identifiers = seed(core, state)
                report['exercise'] = http_phase(core, identifiers) if args.http_only else browser(core, owner, directory, state, identifiers, 'exercise')
            before = owner['sends']
            with Core(state, owner) as restarted:
                report['reopen'] = http_reopen(restarted, identifiers, before) if args.http_only else browser(restarted, owner, directory, state, identifiers, 'reopen')
                report['restart_sent_nothing'] = owner['sends'] == before
                if not report['restart_sent_nothing']:
                    raise RuntimeError('RESTART_REPLAYED_PENDING')
        report['result'] = 'PASS'
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        reason = str(error)
        if isinstance(error, AcceptanceTimeout):
            report['diagnostic'] = error.diagnostic
        report.update(result='FAIL', reason=reason if reason.isupper() and reason.replace('_', '').isalnum() else 'QUEUE_ACCEPTANCE_FAILED')
    finally:
        if report['result'] == 'RUNNING':
            report.update(result='FAIL', reason='RUN_INTERRUPTED')
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
