"""Supplemental real desktop/Core pairing acceptance; no physical phone/LAN/TLS claim.

Uses the real Core, gateway, SQLite binding store and scoped Device Link ASGI app.
Only LAN selector/firewall/listener and one explicit binding-failure fixture are injected.
The browser runs current frontend source with Vite; no build or production hook is added.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.device_link.api import create_device_link_app
from agentguard.device_link.crypto import (
    generate_ecdsa_p256_keypair,
    public_key_to_der,
    sign_challenge,
    verify_signature,
)
from agentguard.device_link.gateway import (
    DeviceLinkGateway,
    GatewayConfig,
    build_auth_message,
)
from agentguard.device_link.identity import DesktopIdentityStore
from agentguard.device_link.lifecycle import DeviceLinkController
from agentguard.device_link.network import LanCandidate
from agentguard.device_link.persistence import DeviceBindingStore
from agentguard.storage.db import StateDB

REQUIRED = (
    'explicit_enable', 'qr_retained_across_polls',
    'sas_exact_android_first', 'android_first_bound', 'bound_list_android_first', 'mutual_auth_android_first',
    'sas_exact_desktop_first', 'desktop_first_bound', 'bound_list_desktop_first', 'mutual_auth_desktop_first',
    'revoke_invalidates_token', 'binding_failure_not_paired', 'binding_failure_registry_empty',
    'late_cancel_response_exercised', 'late_cancel_not_reopened',
    'no_model_requests', 'no_page_errors',
)


class AcceptanceBindings(DeviceBindingStore):
    """All normal writes are real SQLite writes; refusal is a scoped negative fixture."""
    refuse = False

    def add(self, *args, **kwargs):
        if self.refuse:
            raise ValueError('Acceptance binding refusal')
        return super().add(*args, **kwargs)


class Selector:
    def select(self):
        return LanCandidate('Acceptance', 'Acceptance fixture', '192.168.50.10', 24, True, 10, 10)


class Firewall:
    def __init__(self):
        self.applies = 0

    def apply(self, _candidate):
        self.applies += 1

    def remove(self):
        pass


class Listener:
    def __init__(self):
        self.starts = 0

    def start(self, _host, _port):
        self.starts += 1

    def stop(self):
        pass


class Controller(DeviceLinkController):
    invitation = None

    def create_pairing_invitation(self):
        self.invitation = super().create_pairing_invitation()
        return self.invitation


class DelayedReply:
    """One genuine Core poll reply can be held after computation to exercise cancellation."""
    def __init__(self, app):
        self.app = app
        self.target = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.poll_count = 0

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        is_poll = scope.get('method') == 'GET' and path.startswith('/api/v1/device-link/pairings/')
        if is_poll:
            self.poll_count += 1
        if not is_poll or path != self.target:
            return await self.app(scope, receive, send)
        self.target = None
        messages = []

        async def capture(message):
            messages.append(message)

        await self.app(scope, receive, capture)
        self.entered.set()
        if not await asyncio.to_thread(self.release.wait, 15):
            raise RuntimeError('VERIFIER_DELAY_TIMEOUT')
        for message in messages:
            await send(message)


class Core:
    def __init__(self, app):
        self.app = app

    def __enter__(self):
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.url = f'http://127.0.0.1:{self.socket.getsockname()[1]}'
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        if not self.server.started:
            self.__exit__()
            raise RuntimeError('VERIFIER_CORE_START_FAILED')
        return self

    def __exit__(self, *_args):
        self.server.should_exit = True
        self.thread.join(timeout=15)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError('VERIFIER_CORE_CLEANUP_FAILED')


class Driver:
    def __init__(self, controller, registry, identity, client, delayed):
        self.controller, self.registry, self.identity = controller, registry, identity
        self.client, self.delayed = client, delayed
        self.number = 0
        self.mobile = None

    @staticmethod
    def checked(response):
        if response.status_code != 200:
            raise RuntimeError(f'VERIFIER_PROTOCOL_HTTP_{response.status_code}')
        return response.json()

    def dispatch(self, action):
        gateway = self.controller.gateway
        if action == 'connect':
            self.number += 1
            self.registry.refuse = False
            invitation = self.controller.invitation
            private, public = generate_ecdsa_p256_keypair()
            self.mobile = {'private': private, 'public': public_key_to_der(public),
                           'uuid': f'acceptance-android-{self.number}', 'sid': invitation['session_id']}
            value = self.mobile
            connected = self.checked(self.client.post(f"/device/v1/pair/{value['sid']}/connect",
                json={'ticket': invitation['ticket'], 'android_uuid': value['uuid'], 'nonce': secrets.token_hex(32)}))
            value['pairing_token'] = connected['pairing_token']
            sas = self.checked(self.client.post(f"/device/v1/pair/{value['sid']}/sas",
                headers={'X-Pairing-Token': value['pairing_token']}, json={'android_pubkey_der_hex': value['public'].hex()}))
            return {'sas': sas['sas'], 'state': sas['state']}
        if action == 'confirm':
            value = self.mobile
            return self.checked(self.client.post(f"/device/v1/pair/{value['sid']}/confirm",
                headers={'X-Pairing-Token': value['pairing_token']}, json={'confirm': True}))
        if action == 'complete':
            value = self.mobile
            response = self.client.post(f"/device/v1/pair/{value['sid']}/complete",
                headers={'X-Pairing-Token': value['pairing_token']},
                json={'android_uuid': value['uuid'], 'android_pubkey_der_hex': value['public'].hex(),
                      'display_name': 'Acceptance Android', 'protocol_version': 1})
            if response.status_code == 200:
                value['session_token'] = response.json()['session_token']
            return {'http_status': response.status_code, 'bound': response.status_code == 200,
                    'registry_count': self.registry.count()}
        if action == 'authenticate':
            value = self.mobile
            challenge = self.checked(self.client.post('/device/v1/auth/challenge',
                json={'device_uuid': value['uuid'], 'protocol_version': 1}))
            message = build_auth_message(protocol_version=1, desktop_uuid=self.identity.desktop_uuid,
                device_uuid=value['uuid'], challenge_id=challenge['challenge_id'],
                challenge=bytes.fromhex(challenge['desktop_challenge']))
            authenticated = self.checked(self.client.post('/device/v1/auth/response',
                json={'device_uuid': value['uuid'], 'challenge_id': challenge['challenge_id'],
                      'signature': sign_challenge(value['private'], message).hex(), 'protocol_version': 1}))
            verified = verify_signature(self.identity.signing_public_key_der, message, bytes.fromhex(authenticated['desktop_signature']))
            value['session_token'] = authenticated['session_token']
            status = self.client.get('/device/v1/status', headers={'Authorization': 'Bearer ' + value['session_token']})
            return {'verified': verified, 'authenticated_read': status.status_code == 200}
        if action == 'revoked_token':
            value = self.mobile
            response = self.client.get('/device/v1/status', headers={'Authorization': 'Bearer ' + value['session_token']})
            return {'rejected': response.status_code == 401}
        if action == 'refuse_binding':
            self.registry.refuse = True
            return {'armed': True}
        if action == 'delay_poll':
            self.delayed.entered.clear()
            self.delayed.release.clear()
            self.delayed.target = '/api/v1/device-link/pairings/' + self.mobile['sid']
            return {'armed': True}
        if action == 'release_poll':
            self.delayed.release.set()
            return {'released': True}
        if action == 'inspect':
            current = gateway.pair_poll(self.mobile['sid']) if self.mobile else {}
            return {'state': current.get('state'), 'registry_count': self.registry.count(),
                    'delayed': self.delayed.entered.is_set(), 'poll_count': self.delayed.poll_count}
        raise RuntimeError('VERIFIER_UNKNOWN_ACTION')


def bridge(driver, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != '/drive' or not secrets.compare_digest(self.headers.get('X-Verify-Token', ''), token):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 256:
                    raise ValueError()
                action = json.loads(self.rfile.read(size))['action']
                result = driver.dispatch(action)
                status = 200
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, AssertionError, sqlite3.DatabaseError):
                result, status = {'error': 'VERIFIER_DRIVER_FAILED'}, 500
            encoded = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def protocol_phase(core_url, driver):
    """Repeatable protocol-only supplement when no sandboxed browser is available."""
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    checks = {}
    with httpx.Client(base_url=core_url, timeout=10) as http:
        http.headers['X-Session-Token'] = http.get('/api/session').json()['token']
        assert http.post('/api/v1/device-link/enable', json={}).status_code == 200
        for order in ('android_first', 'desktop_first'):
            invitation = http.post('/api/v1/device-link/pairings', json={})
            assert invitation.status_code == 200
            sid = invitation.json()['session_id']
            mobile = driver.dispatch('connect')
            assert http.get('/api/v1/device-link/pairings/' + sid).json()['sas'] == mobile['sas']
            if order == 'android_first':
                assert driver.dispatch('confirm')['state'] == 'sas_pending'
                assert http.post('/api/v1/device-link/pairings/' + sid + '/confirm', json={'confirm': True}).json()['state'] == 'confirmed_both'
            else:
                assert http.post('/api/v1/device-link/pairings/' + sid + '/confirm', json={'confirm': True}).json()['state'] == 'sas_pending'
                assert driver.dispatch('confirm')['state'] == 'confirmed_both'
            assert driver.dispatch('complete')['bound']
            assert http.get('/api/v1/device-link/pairings/' + sid).json()['state'] == 'consumed'
            assert len(http.get('/api/v1/devices').json()['bound_devices']) == 1
            authenticated = driver.dispatch('authenticate')
            assert authenticated['verified'] and authenticated['authenticated_read']
            assert http.post('/api/v1/device-link/devices/' + driver.mobile['uuid'] + '/revoke', json={}).status_code == 200
            assert driver.dispatch('revoked_token')['rejected']
            assert http.get('/api/v1/device-link/pairings/' + sid).status_code == 404
            checks[order + '_Core_HTTP_ASGI_SAS_binding_mutual_auth_revoke'] = True

        invitation = http.post('/api/v1/device-link/pairings', json={}).json()
        sid = invitation['session_id']
        driver.dispatch('connect')
        driver.dispatch('confirm')
        http.post('/api/v1/device-link/pairings/' + sid + '/confirm', json={'confirm': True})
        driver.dispatch('refuse_binding')
        refused = driver.dispatch('complete')
        assert refused['http_status'] == 409 and refused['registry_count'] == 0
        assert http.get('/api/v1/device-link/pairings/' + sid).json()['state'] == 'confirmed_both'
        assert http.post('/api/v1/device-link/pairings/' + sid + '/cancel', json={}).status_code == 200
        checks['refused_binding_no_consumed_or_registry_entry'] = True

        invitation = http.post('/api/v1/device-link/pairings', json={}).json()
        sid = invitation['session_id']
        driver.dispatch('connect')
        driver.dispatch('delay_poll')
        route = '/api/v1/device-link/pairings/' + sid
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(httpx.get, core_url + route, headers=dict(http.headers), timeout=15)
            try:
                assert driver.delayed.entered.wait(timeout=10)
                assert http.post(route + '/cancel', json={}).status_code == 200
            finally:
                driver.dispatch('release_poll')
            assert future.result(timeout=10).json()['state'] == 'sas_pending'
        assert http.get(route).json()['state'] == 'cancelled'
        checks['genuine_delayed_Core_reply_after_cancel_without_UI_claim'] = True
    return checks


def source_hashes():
    paths = ('agentguard/api/server.py', 'agentguard/device_link/api.py',
             'agentguard/device_link/gateway.py', 'agentguard/device_link/pairing.py',
             'agentguard/device_link/persistence.py', 'web/src/api/types.ts',
             'web/src/devices/BackendDeviceLinkAdapter.ts', 'web/src/pages/DevicesPage.tsx',
             'scripts/verify_device_pairing.py', 'scripts/verify_device_pairing_browser.cjs')
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--protocol-only', action='store_true', help='Core HTTP and scoped ASGI protocol checks only; no browser acceptance')
    args = parser.parse_args()
    directory = args.report_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = {'result': 'RUNNING', 'scope': 'supplemental actual browser/Core/SQLite and scoped ASGI mobile protocol',
              'excluded': ['physical Android', 'camera', 'real LAN', 'Windows firewall/UAC', 'TLS wire transport', 'models'],
              'checks': {}}
    try:
        before = source_hashes()
        report['source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        report['source_sha256'] = before
        with tempfile.TemporaryDirectory(prefix='asg-device-browser-') as temporary:
            state = Path(temporary)
            database = StateDB(state / 'state.db')
            database.connect()
            registry = AcceptanceBindings(database, single_device=True)
            identity = DesktopIdentityStore(state / 'identity.json').load_or_create()
            database.close()
            gateway = DeviceLinkGateway(identity.desktop_uuid, identity.signing_public_key_der,
                identity.signing_private_key_pem, desktop_tls_spki_fp=identity.tls_spki_fingerprint,
                config=GatewayConfig(single_device=True), device_registry=registry, single_device=True)
            firewall, listener = Firewall(), Listener()
            controller = Controller(Selector(), firewall, listener, gateway=gateway, identity=identity)
            delayed = DelayedReply(create_app(state_db_path=state / 'state.db',
                config={'base_dir': str(state)}, device_link_controller=controller))
            mobile_app = create_device_link_app(gateway=gateway, state_db_path=state / 'state.db', snapshots_dir=state / 'snapshots')
            with ExitStack() as stack:
                client = stack.enter_context(TestClient(mobile_app))
                core = stack.enter_context(Core(delayed))
                driver = Driver(controller, registry, identity, client, delayed)
                if args.protocol_only:
                    report['scope'] = 'supplemental Core HTTP/scoped ASGI protocol only; no browser'
                    report['excluded'].append('browser UI')
                    report['checks'].update(protocol_phase(core.url, driver))
                else:
                    verify_token = secrets.token_hex(32)
                    server, thread = bridge(driver, verify_token)
                    try:
                        fixture = state / 'fixture.json'
                        fixture.write_text(json.dumps({'core_url': core.url,
                            'driver_url': f'http://127.0.0.1:{server.server_address[1]}/drive', 'driver_token': verify_token}))
                        with (directory / 'browser.log').open('w') as log:
                            completed = subprocess.run(['node', str(ROOT / 'scripts/verify_device_pairing_browser.cjs'),
                                str(fixture), str(directory)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                timeout=180, check=False)
                        browser_report = json.loads((directory / 'device-browser.json').read_text())
                        report['browser'] = browser_report
                        if completed.returncode or browser_report.get('result') != 'PASS' or any(browser_report.get('checks', {}).get(key) is not True for key in REQUIRED):
                            raise RuntimeError('VERIFIER_BROWSER_CHECK_FAILED')
                        report['browser'] = browser_report
                    finally:
                        delayed.release.set()
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)
                        if thread.is_alive():
                            raise RuntimeError('VERIFIER_DRIVER_CLEANUP_FAILED')
            with sqlite3.connect(state / 'state.db') as connection:
                report['checks']['sqlite_integrity'] = connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            report['checks']['source_stable_during_run'] = source_hashes() == before
            report['checks']['only_fixture_firewall_listener'] = firewall.applies == 1 and listener.starts == 1
            if not all(report['checks'].values()):
                raise RuntimeError('VERIFIER_FINAL_CHECK_FAILED')
        report['result'] = 'PASS'
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AssertionError, sqlite3.DatabaseError, subprocess.SubprocessError):
        report.update(result='FAIL', reason='DEVICE_PAIRING_ACCEPTANCE_FAILED')
    finally:
        if report['result'] == 'RUNNING':
            report.update(result='FAIL', reason='RUN_INTERRUPTED')
        (directory / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
