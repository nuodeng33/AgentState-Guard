"""Kill a real recovery process; verify journal recovery without graceful cleanup."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psutil
import pytest

from agentguard.recovery.protection import ProtectionService
from agentguard.recovery.protection_domains import DomainRegistry
from agentguard.recovery.protection_runtime import ProtectionRuntime

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name('protection_crash_worker.py')


@contextmanager
def service(state):
    domains = DomainRegistry(state)
    instance = ProtectionService(state / 'state.db', state, domains)
    try:
        yield instance
    finally:
        instance.close()
        domains.close()


def files(project):
    return {name: (project / name).read_bytes() if (project / name).exists() else None
            for name in ('A.txt', 'B.txt', 'C.txt')}


def until(read, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if predicate(result):
            return result
        time.sleep(.02)
    raise AssertionError('PROCESS_CRASH_CHECK_TIMEOUT')


@pytest.mark.parametrize(('point', 'automatic', 'external_edit'), [
    ('after_intent', False, False), ('after_apply', False, False),
    ('before_complete', False, False), ('after_compensation', False, False),
    ('after_apply', True, False), ('after_apply', False, True),
])
def test_forced_process_death_recovers_durable_native_operation(tmp_path, point, automatic, external_edit):
    state, project = tmp_path / 'state', tmp_path / 'project'
    state.mkdir()
    project.mkdir()
    (project / 'A.txt').write_bytes(b'original-A\n')
    (project / 'B.txt').write_bytes(b'original-B\n')
    with service(state) as initial:
        scope = initial.register('local', str(project), str(uuid4()))['scope_id']
        baseline = initial.scope(scope)['baseline_id']
        ids = [row['file_id'] for row in initial.version(baseline)['files']]
        (project / 'A.txt').write_bytes(b'changed-A\n')
        (project / 'B.txt').unlink()
        (project / 'C.txt').write_bytes(b'new-C\n')
        initial.capture(scope, force=True)
        preview = initial.preview(scope, 'restore_version', ids, version_id=baseline)
        command = (preview['preview_id'], preview['digest'], str(uuid4()))
        queued = initial.queue_restore(*command)
    current = files(project)
    marker = tmp_path / 'paused.json'
    with (tmp_path / 'worker.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, str(WORKER), str(state), queued['id'], point, str(marker)],
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        worker_process = None
        try:
            def paused():
                assert child.poll() is None, (tmp_path / 'worker.log').read_text()
                return json.loads(marker.read_text()) if marker.exists() else None
            receipt = until(paused, bool)
            launcher = psutil.Process(child.pid)
            owned = {p.pid: p for p in [launcher, *launcher.children(recursive=True)]}
            assert receipt['pid'] in owned
            worker_process = owned[receipt['pid']]
            assert worker_process.create_time() >= launcher.create_time()
            assert receipt['operation_id'] == queued['id'] and receipt['point'] == point
            with service(state) as observer:
                interrupted = observer.operation(queued['id'])
            assert interrupted['state'] == ('COMPENSATING' if point == 'after_compensation' else 'RUNNING')
            step = next((row for row in interrupted['steps'] if row['step_id'] == receipt['step_id']), None)
            if point == 'after_intent':
                assert step['state'] == 'INTENT' and files(project) == current
            elif point == 'after_apply':
                assert step['state'] == 'INTENT' and files(project) != current
                assert (project / step['relative_path']).read_bytes() == b'original-' + step['relative_path'][0].encode() + b'\n'
            elif point == 'before_complete':
                assert all(row['state'] == 'VERIFIED' for row in interrupted['steps'])
                assert files(project) == {'A.txt': b'original-A\n', 'B.txt': b'original-B\n', 'C.txt': b'new-C\n'}
            else:
                assert step['state'] == 'COMPENSATION_INTENT'
                assert files(project)[step['relative_path']] == current[step['relative_path']]
            worker_process.kill()
            if worker_process.pid != child.pid:
                worker_process.wait(timeout=10)
            child.wait(timeout=10)
            assert child.returncode != 0
            if os.name != 'nt':
                assert child.returncode == -signal.SIGKILL
        finally:
            if worker_process is not None and worker_process.is_running():
                worker_process.kill()
                worker_process.wait(timeout=10)
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    if external_edit:
        (project / step['relative_path']).write_bytes(b'external-user-edit\n')
        current = files(project)
    expected = 'NEEDS_ATTENTION' if external_edit else 'COMPENSATED'
    if automatic:
        domains = DomainRegistry(state)
        runtime = ProtectionRuntime(state / 'state.db', state, domains)
        try:
            runtime.start()
            def persisted_state():
                with sqlite3.connect(state / 'state.db') as db:
                    return db.execute('SELECT state FROM protection_operations WHERE id=?', (queued['id'],)).fetchone()[0]
            assert until(persisted_state, lambda value: value not in {'RUNNING', 'COMPENSATING'}) == expected
        finally:
            runtime.close()
            domains.close()
    with service(state) as recovered:
        if external_edit:
            binding = json.loads(recovered.scope(scope)['binding_json'])
            native_receipt = recovered.domains.reconcile(binding, queued['id'], step['step_id'])
            assert native_receipt['state'] == 'NEEDS_ATTENTION'
            assert native_receipt['live_matches_target'] is False
        result = recovered.reconcile(queued['id'])
        assert result['state'] == expected
        if external_edit:
            assert result['reason_code'] == 'PROTECTION_STEP_UNCERTAIN'
        assert files(project) == current
        events = recovered.conn.execute('SELECT COUNT(*) FROM evidence_ledger_events').fetchone()[0]
        steps = recovered.conn.execute('SELECT COUNT(*) FROM protection_step_events').fetchone()[0]
        assert recovered.reconcile(queued['id'])['state'] == expected
        assert recovered.queue_restore(*command)['id'] == queued['id']
        assert recovered.conn.execute('SELECT COUNT(*) FROM protection_operations').fetchone()[0] == 1
        assert recovered.conn.execute('SELECT COUNT(*) FROM evidence_ledger_events').fetchone()[0] == events
        assert recovered.conn.execute('SELECT COUNT(*) FROM protection_step_events').fetchone()[0] == steps
        assert recovered.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert files(project) == current
    with service(state) as reopened:
        assert reopened.operation(queued['id'])['state'] == expected
        assert files(project) == current
