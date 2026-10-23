"""Real SQLite/files/API with controlled provider events and fault injection."""

import json
import sys
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentguard.api.execution import execution_router
from agentguard.discovery.application_probe import probe_entry
from agentguard.discovery.catalog import DiscoveryCatalog
from agentguard.discovery.session_metadata import inspect_store
from agentguard.evidence.ledger import verify_ledger
from agentguard.execution.models import CreateSession, StartRun
from agentguard.execution.runtime import ExecutionRuntime
from agentguard.recovery.protection import ProtectionError
from agentguard.recovery.protection_domains import DomainRegistry
from agentguard.recovery.protection_runtime import ProtectionRuntime
from agentguard.storage.db import StateDB
from agentguard.work.models import CreateWork
from agentguard.work.service import WorkError, WorkService


class Discovery:
    def sources(self):
        return {'items': [{'source_id': 'local', 'kind': 'NATIVE', 'state': 'OBSERVED',
                           'enabled': True, 'freshness': 'FRESH'}]}


class Provider:
    def __init__(self, session, on_event, owner):
        self.rpc = self
        self.alive = True
        self.on_event, self.owner = on_event, owner
        self.native_id = 'native-' + session['id']

    def initialize(self):
        return {'write': True, 'read_only_supported': True, 'send': True}

    def create_session(self, cwd, **kwargs):
        self.owner['creates'] += 1
        return {'native_session_id': self.native_id}

    def start_turn(self, native_id, text, **kwargs):
        self.owner['sends'] += 1
        if self.owner.get('lost_ack'):
            raise RuntimeError('provider response lost')
        self.on_event({'kind': 'turn.started', 'payload': {'status': 'running'}, 'native_session_id': native_id,
                       'native_turn_id': 'turn-1'})
        if self.owner.get('wait'):
            return {'native_turn_id': 'turn-1', 'status': 'running'}
        (self.owner['workspace'] / 'A.txt').write_text('new contents', encoding='utf-8')
        for _ in range(2):
            self.on_event({'kind': 'output.delta', 'payload': {'text': 'same', 'item_id': 'output-1'},
                           'native_session_id': native_id, 'native_turn_id': 'turn-1'})
        self.on_event({'kind': 'turn.completed', 'payload': {'status': 'completed'}, 'native_session_id': native_id,
                       'native_turn_id': 'turn-1'})
        return {'native_turn_id': 'turn-1', 'status': 'running'}

    def cancel(self, native_id, turn_id):
        self.owner['cancels'] += 1
        self.on_event({'kind': 'turn.completed', 'payload': {'status': 'interrupted'},
                       'native_session_id': native_id, 'native_turn_id': turn_id})

    def reply(self, native_request_id, response):
        self.owner['replies'] += 1

    def close(self):
        self.alive = False


@pytest.fixture
def setup(tmp_path):
    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    db_path = state_dir / 'state.db'
    workspace = tmp_path / 'project'
    workspace.mkdir()
    (workspace / 'A.txt').write_text('old contents', encoding='utf-8')
    store_root = tmp_path / 'provider'
    (store_root / 'sessions').mkdir(parents=True)
    domains = DomainRegistry(state_dir)
    protection = ProtectionRuntime(db_path, state_dir, domains)

    @contextmanager
    def catalog_context():
        db = StateDB(db_path)
        db.connect()
        try:
            yield DiscoveryCatalog(db, domains, Discovery())
        finally:
            db.close()

    with catalog_context() as catalog, protection.service() as protected:
        created = WorkService(catalog.db).create(CreateWork(request_id=uuid4(), title='Test work', owner='Owner', requirement='Implement A'))
        entry = probe_entry(sys.executable)
        # The protocol fixture controls this verified local executable, not a real model.
        application_id = catalog._save_application('local', entry, registered=True)
        with catalog.db.transaction() as conn:
            conn.execute("UPDATE discovery_applications SET metadata_json=json_set(metadata_json,'$.provider','CODEX') WHERE id=?", (application_id,))
        store_id = catalog._save_store('local', 'CODEX', str(store_root), inspect_store('CODEX', store_root), registered=True)
        scope_id = protected.register('local', str(workspace), str(uuid4()))['scope_id']
    owner = {'creates': 0, 'sends': 0, 'cancels': 0, 'replies': 0, 'workspace': workspace}
    runtime = ExecutionRuntime(db_path, domains, catalog_context, protection,
        provider_factory=lambda session, event: Provider(session, event, owner))
    yield {'runtime': runtime, 'owner': owner, 'work_id': created['work_id'], 'scope_id': scope_id,
           'application_id': application_id, 'store_id': store_id, 'workspace': workspace, 'db_path': db_path}
    runtime.close()
    protection.close()


def wait_for(read, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if predicate(result):
            return result
        time.sleep(0.025)
    raise AssertionError(result)


def create_session(setup):
    command = CreateSession(request_id=uuid4(), expected_revision=1, control_consent=True,
                            **{k: setup[k] for k in ('application_id', 'store_id', 'scope_id')})
    with setup['runtime'].service() as service:
        result = service.create_session(setup['work_id'], command)
        assert service.create_session(setup['work_id'], command) == result
    setup['runtime'].notify()
    def read():
        with setup['runtime'].service() as service:
            return service.session(setup['work_id'], result['session_id'])
    return wait_for(read, lambda row: row['state'] == 'READY')


def test_api_real_files_receipts_stream_and_file_evidence(setup):
    runtime = setup['runtime']
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    app = FastAPI()
    app.include_router(execution_router(runtime))
    client = TestClient(app)
    root = '/api/v1/works/' + setup['work_id'] + '/sessions'
    create = {'request_id': str(uuid4()), 'expected_revision': 1, 'control_consent': True,
              **{k: setup[k] for k in ('application_id', 'store_id', 'scope_id')}}
    first = client.post(root, json=create)
    assert first.status_code == 201, first.text
    session_id = first.json()['session_id']
    session_root = root + '/' + session_id
    session = wait_for(lambda: client.get(session_root).json(), lambda row: row['state'] == 'READY')
    assert client.post(root, json=create).json() == first.json()
    command = {'request_id': str(uuid4()), 'expected_revision': session['revision'],
               'requirement_version': 1, 'input': 'Update A', 'context_refs': []}
    started = client.post(session_root + '/runs', json=command)
    assert started.status_code == 201, started.text
    run_id = started.json()['run_id']
    run_root = session_root + '/runs/' + run_id
    run = wait_for(lambda: client.get(run_root).json(), lambda row: row['state'] == 'COMPLETED')
    assert client.post(session_root + '/runs', json=command).json() == started.json()
    assert setup['owner']['sends'] == setup['owner']['creates'] == 1
    assert run['file_result']['status'] == 'AVAILABLE'
    assert run['file_result']['attribution'] == 'UNATTRIBUTED'
    assert setup['workspace'].joinpath('A.txt').read_text(encoding='utf-8') == 'new contents'
    events = client.get(run_root + '/events').json()
    assert ''.join(e['payload']['text'] for e in events['items'] if e['type'] == 'TEXT') == 'samesame'
    with client.stream('GET', run_root + '/events?stream=true&after=1') as response:
        streamed = response.read().decode()
    assert 'id: 1\n' not in streamed
    assert 'event: done' in streamed
    changed = {**command, 'input': 'Different input'}
    assert client.post(session_root + '/runs', json=changed).json()['reason_code'] == 'WORK_REQUEST_REUSED'
    with runtime.journal() as service:
        assert verify_ledger(service.conn) == []


def test_unknown_dispatch_is_not_retried_and_reserves_scope(setup):
    runtime, owner = setup['runtime'], setup['owner']
    owner['lost_ack'] = True
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    session = create_session(setup)
    command = StartRun(request_id=uuid4(), expected_revision=session['revision'], requirement_version=1, input='Do work')
    with runtime.service() as service:
        run_id = service.start_run(setup['work_id'], session['id'], command)['run_id']
    runtime.notify()
    def read():
        with runtime.journal() as service:
            return service.one('work_runs', run_id)
    wait_for(read, lambda row: row['state'] == 'UNCERTAIN')
    runtime.notify()
    time.sleep(0.2)
    assert owner['sends'] == 1
    with runtime.service() as service:
        assert service.start_run(setup['work_id'], session['id'], command)['run_id'] == run_id
        current = service.session(setup['work_id'], session['id'])
        with pytest.raises(WorkError, match='EXECUTION_WRITER_BUSY'):
            service.start_run(setup['work_id'], session['id'], StartRun(request_id=uuid4(), expected_revision=current['revision'], requirement_version=1, input='second'))


def test_requirement_and_task_input_stays_fixed_during_execution(setup):
    runtime = setup['runtime']
    setup['owner']['wait'] = True
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    session = create_session(setup)
    with runtime.service() as service:
        command = StartRun(request_id=uuid4(), expected_revision=session['revision'], requirement_version=1, input='Do work')
        run_id = service.start_run(setup['work_id'], session['id'], command)['run_id']
    runtime.notify()
    wait_for(lambda: setup['owner']['sends'], lambda n: n == 1)
    with runtime.journal() as service, service.db.transaction() as conn:
        conn.execute('UPDATE works SET requirement_version=2 WHERE id=?', (setup['work_id'],))
        snapshot = json.loads(service.one('work_runs', run_id)['input_json'])
        assert snapshot['requirement'] == 'Implement A'
        assert service.one('work_runs', run_id)['requirement_version'] == 1


def test_program_or_scope_replacement_blocks_execution(setup):
    runtime = setup['runtime']
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    session = create_session(setup)
    original = setup['workspace']
    original.rename(original.with_name('old-project'))
    original.mkdir()
    with runtime.service() as service, pytest.raises(WorkError, match='WORKSPACE_ROOT_IDENTITY_CHANGED'):
        service.start_run(setup['work_id'], session['id'], StartRun(request_id=uuid4(), expected_revision=session['revision'], requirement_version=1, input='Do work'))
    assert setup['owner']['sends'] == 0


def test_restore_cannot_queue_while_writer_is_active(setup):
    runtime = setup['runtime']
    setup['owner']['wait'] = True
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    session = create_session(setup)
    with runtime.service() as service:
        scope = service.protection.scope(setup['scope_id'])
        file_id = next(iter(service.protection._manifest(scope['latest_id'])))
        preview = service.protection.preview(setup['scope_id'], 'restore_version', [file_id], version_id=scope['latest_id'])
        service.start_run(setup['work_id'], session['id'], StartRun(request_id=uuid4(), expected_revision=session['revision'], requirement_version=1, input='Do work'))
        with pytest.raises(ProtectionError, match='PROTECTION_MANAGED_WRITER_BUSY'):
            service.protection.queue_restore(preview['preview_id'], preview['digest'], str(uuid4()))


def test_restart_keeps_unknown_execution_and_does_not_send(setup):
    runtime = setup['runtime']
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    session = create_session(setup)
    runtime.stop_event.set()
    runtime.wake.set()
    runtime.thread.join(timeout=3)
    with runtime.service() as service:
        run_id = service.start_run(setup['work_id'], session['id'], StartRun(request_id=uuid4(), expected_revision=session['revision'], requirement_version=1, input='Do work'))['run_id']
        with service.db.transaction() as conn:
            conn.execute("UPDATE work_runs SET state='DISPATCHING',dispatch_state='SENDING' WHERE id=?", (run_id,))
    runtime._startup_reconcile()
    with runtime.journal() as service:
        assert service.one('work_runs', run_id)['state'] == 'UNCERTAIN'
    assert setup['owner']['sends'] == 0


def test_new_session_can_bootstrap_verified_store_without_history(setup):
    store = setup['workspace'].parent / 'provider'
    (store / 'sessions').rmdir()
    setup['runtime'].start()
    wait_for(lambda: setup['runtime'].accepting, bool)
    result = create_session(setup)
    assert result['state'] == 'READY'
    assert setup['owner']['creates'] == 1
    assert not (store / 'sessions').exists()  # Core metadata probes do not create provider files.


@pytest.mark.parametrize('change', ['missing', 'replacement', 'invalid-history'])
def test_fresh_store_support_does_not_bypass_changed_or_unsafe_identity(setup, change):
    store = setup['workspace'].parent / 'provider'
    if change == 'missing':
        (store / 'sessions').rmdir()
        store.rmdir()
    elif change == 'replacement':
        store.rename(store.with_name('old-provider'))
        (store / 'sessions').mkdir(parents=True)
    else:
        (store / 'sessions').rmdir()
        (store / 'sessions').write_text('not a directory')
    from agentguard.work.service import WorkError
    with pytest.raises(WorkError, match='EXECUTION_STORE_IDENTITY_CHANGED'):
        create_session(setup)
    assert setup['owner']['creates'] == 0
