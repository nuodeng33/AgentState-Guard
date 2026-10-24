"""Real SQLite/protection/runtime queue regressions; provider is a controlled protocol fixture.

No provider executable or model is called. Normal completion uses provider events
and real file-result finalization; explicit faults are named in their tests.
"""
from __future__ import annotations

import asyncio
import json
import threading
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentguard.api.execution import execution_router
from agentguard.execution import models
from agentguard.execution.models import CancelRun, CreateSession, StartRun
from agentguard.work.models import ReviseRequirement, SaveArtifact, WriteTask
from agentguard.work.service import WorkError
from tests import test_execution_core as execution_core
from tests.test_execution_core import create_session, wait_for


@pytest.fixture
def setup(tmp_path):
    yield from execution_core.setup.__wrapped__(tmp_path)


class QueueProvider:
    def __init__(self, session, on_event, owner):
        self.rpc = self
        self.alive = True
        self.native_id = 'queue-native-' + session['id']
        self.on_event, self.owner = on_event, owner
        self.turn_id = None

    def initialize(self):
        return {'write': True, 'read_only_supported': True, 'send': True}

    def create_session(self, _cwd, **_kwargs):
        self.owner['creates'] += 1
        return {'native_session_id': self.native_id}

    def start_turn(self, _native_id, text, **_kwargs):
        self.owner['sends'] += 1
        self.owner['prompts'].append(text)
        self.turn_id = 'queue-turn-' + str(self.owner['sends'])
        self.on_event({'kind': 'turn.started', 'payload': {'status': 'running'},
                       'native_session_id': self.native_id, 'native_turn_id': self.turn_id})
        if self.owner.get('finish_next') and self.owner['sends'] > 1:
            self.finish()
        return {'native_turn_id': self.turn_id, 'status': 'running'}

    def finish(self, status='completed'):
        if status == 'completed':
            (self.owner['workspace'] / 'A.txt').write_text('queue fixture result ' + self.turn_id, encoding='utf-8')
        self.on_event({'kind': 'turn.completed', 'payload': {'status': status},
                       'native_session_id': self.native_id, 'native_turn_id': self.turn_id})

    def cancel(self, _native_id, _turn_id):
        self.owner['cancels'] += 1
        self.finish('interrupted')

    def close(self):
        self.alive = False


def boot(value, *, task=False):
    runtime, owner = value['runtime'], value['owner']
    owner['prompts'] = []
    providers = []

    def factory(session, event):
        provider = QueueProvider(session, event, owner)
        providers.append(provider)
        return provider

    runtime.provider_factory = factory
    runtime.start()
    wait_for(lambda: runtime.accepting, bool)
    if task:
        with runtime.service() as service:
            work = service.work.get(value['work_id'])
            task_id = service.work.write_task(value['work_id'], WriteTask(
                request_id=uuid4(), expected_revision=work['revision'], title='Assigned task', body='Original task'))['task_id']
            work = service.work.get(value['work_id'])
            created = service.create_session(value['work_id'], CreateSession(
                request_id=uuid4(), expected_revision=work['revision'], task_id=UUID(task_id),
                control_consent=True, **{key: value[key] for key in ('application_id', 'store_id', 'scope_id')}))
        runtime.notify()
        session = wait_for(lambda: session_read(value, created['session_id']), lambda item: item['state'] == 'READY')
    else:
        session = create_session(value)
    with runtime.service() as service:
        predecessor = service.start_run(value['work_id'], session['id'], StartRun(
            request_id=uuid4(), expected_revision=session['revision'], requirement_version=1, input='Predecessor input'))['run_id']
    runtime.notify()
    wait_for(lambda: owner['sends'], lambda count: count == 1)
    return session, predecessor, providers


def session_read(value, session_id):
    with value['runtime'].service() as service:
        return service.session(value['work_id'], session_id)


def run_read(value, session_id, run_id):
    with value['runtime'].service() as service:
        return service.run(value['work_id'], session_id, run_id)


def pending_read(value, session_id, request_id=None):
    with value['runtime'].service() as service:
        return service.pending_instruction(value['work_id'], session_id, request_id=request_id)['item']


def queue(value, session_id, predecessor, *, text='Exact queued input\nSecond line', refs=None):
    session = session_read(value, session_id)
    command = models.QueueInstruction(request_id=uuid4(), expected_revision=session['revision'],
        requirement_version=1, input=text, context_refs=refs or [], predecessor_run_id=UUID(predecessor))
    with value['runtime'].service() as service:
        receipt = service.enqueue_pending(value['work_id'], session_id, command)
    value['runtime'].notify()
    return command, receipt


def assert_only_predecessor(value, session_id):
    with value['runtime'].service() as service:
        assert len(service.runs(value['work_id'], session_id, 50, 0)['items']) == 1
    assert value['owner']['sends'] == 1


def test_normal_completion_promotes_exact_input_once_and_duplicate_receipt_is_stable(setup):
    session, predecessor, providers = boot(setup)
    command, receipt = queue(setup, session['id'], predecessor)
    assert receipt == {'pending_id': receipt['pending_id'], 'session_id': session['id'], 'state': 'PENDING',
                       'revision': 1, 'predecessor_run_id': predecessor, 'request_id': str(command.request_id)}
    pending = pending_read(setup, session['id'])
    assert pending['input'] == command.input
    assert pending['state'] == 'PENDING'
    assert_only_predecessor(setup, session['id'])
    setup['owner']['finish_next'] = True
    providers[0].finish()
    item = wait_for(lambda: pending_read(setup, session['id']), lambda row: row['state'] == 'PROMOTED')
    child = wait_for(lambda: run_read(setup, session['id'], item['promoted_run_id']), lambda row: row['state'] == 'COMPLETED')
    prior = run_read(setup, session['id'], predecessor)
    assert prior['file_result']['status'] == child['file_result']['status'] == 'AVAILABLE'
    assert setup['owner']['sends'] == 2
    assert setup['owner']['prompts'][1].endswith('Current input:\n' + command.input)
    with setup['runtime'].service() as service:
        assert service.enqueue_pending(setup['work_id'], session['id'], command) == receipt
        assert len(service.runs(setup['work_id'], session['id'], 50, 0)['items']) == 2
    setup['runtime'].notify()
    wait_for(lambda: setup['runtime'].jobs, lambda jobs: not jobs)
    assert setup['owner']['sends'] == 2


def test_one_active_slot_rejects_new_uuid_and_reused_uuid_with_changed_body(setup):
    session, predecessor, _ = boot(setup)
    command, receipt = queue(setup, session['id'], predecessor)
    with setup['runtime'].service() as service:
        assert service.enqueue_pending(setup['work_id'], session['id'], command) == receipt
        with pytest.raises(WorkError):
            service.enqueue_pending(setup['work_id'], session['id'],
                                    command.model_copy(update={'request_id': uuid4()}))
        with pytest.raises(WorkError, match='REQUEST_REUSED'):
            service.enqueue_pending(setup['work_id'], session['id'],
                                    command.model_copy(update={'input': 'Different request body'}))
    assert_only_predecessor(setup, session['id'])


def test_cancel_pending_is_cas_idempotent_and_does_not_cancel_predecessor(setup):
    session, predecessor, _ = boot(setup)
    _, receipt = queue(setup, session['id'], predecessor)
    command = models.CancelPendingInstruction(request_id=uuid4(), expected_revision=1)
    with setup['runtime'].service() as service:
        cancelled = service.cancel_pending(setup['work_id'], session['id'], receipt['pending_id'], command)
        assert cancelled['state'] == 'CANCELLED' and cancelled['revision'] == 2
        assert cancelled['request_id'] == str(command.request_id)
        assert service.cancel_pending(setup['work_id'], session['id'], receipt['pending_id'], command) == cancelled
    assert pending_read(setup, session['id'])['state'] == 'CANCELLED'
    assert run_read(setup, session['id'], predecessor)['state'] == 'RUNNING'
    assert setup['owner']['cancels'] == 0


def test_stop_cancels_pending_before_predecessor_can_complete(setup):
    session, predecessor, providers = boot(setup)
    queue(setup, session['id'], predecessor)
    with setup['runtime'].service() as service:
        service.cancel(setup['work_id'], session['id'], predecessor, CancelRun(request_id=uuid4()))
    setup['runtime'].notify()
    wait_for(lambda: run_read(setup, session['id'], predecessor), lambda row: row['state'] == 'CANCELLED')
    assert pending_read(setup, session['id'])['state'] == 'CANCELLED'
    providers[0].finish()
    assert_only_predecessor(setup, session['id'])


def test_stop_terminal_predecessor_after_promotion_cancels_queued_child(setup, monkeypatch):
    session, predecessor, providers = boot(setup)
    queue(setup, session['id'], predecessor)
    runtime = setup['runtime']
    entered, release = threading.Event(), threading.Event()
    original = runtime._dispatch

    def held_dispatch(session_id, run_id):
        if run_id != predecessor:
            entered.set()
            assert release.wait(timeout=8)
        return original(session_id, run_id)

    monkeypatch.setattr(runtime, '_dispatch', held_dispatch)
    providers[0].finish()
    try:
        assert entered.wait(timeout=8), 'Promoted child did not reach controlled dispatch gate'
        item = pending_read(setup, session['id'])
        assert item['state'] == 'PROMOTED'
        with runtime.service() as service:
            service.cancel(setup['work_id'], session['id'], predecessor, CancelRun(request_id=uuid4()))
    finally:
        release.set()
    runtime.notify()
    child = wait_for(lambda: run_read(setup, session['id'], item['promoted_run_id']),
                     lambda row: row['state'] in {'CANCELLED', 'INTERRUPTED'})
    assert child['state'] in {'CANCELLED', 'INTERRUPTED'}
    assert setup['owner']['sends'] == 1


@pytest.mark.parametrize('lifecycle', ['shutdown', 'startup_reconcile'])
def test_shutdown_and_restart_hold_pending_without_replaying(setup, lifecycle):
    session, predecessor, _ = boot(setup)
    queue(setup, session['id'], predecessor)
    runtime = setup['runtime']
    if lifecycle == 'shutdown':
        runtime.close()
    else:
        # Explicit abrupt-control-loop interruption fixture; no synthetic run state.
        runtime.stop_event.set()
        runtime.wake.set()
        runtime.thread.join(timeout=5)
        assert not runtime.thread.is_alive()
        runtime._startup_reconcile()
    assert pending_read(setup, session['id'])['state'] == 'HELD'
    assert setup['owner']['sends'] == 1


@pytest.mark.parametrize('drift', ['requirement', 'task', 'context_same_body_new_version', 'generation'])
def test_semantic_or_generation_drift_holds_pending_instead_of_running(setup, drift):
    session, predecessor, providers = boot(setup, task=drift == 'task')
    refs = []
    artifact_id = None
    if drift == 'context_same_body_new_version':
        with setup['runtime'].service() as service:
            revision = service.work.get(setup['work_id'])['revision']
            artifact_id = service.work.save_artifact(setup['work_id'], SaveArtifact(
                request_id=uuid4(), expected_revision=revision, requirement_version=1,
                title='Context', body='Same body'))['artifact_id']
        refs = [{'kind': 'artifact', 'ref_id': artifact_id}]
    queue(setup, session['id'], predecessor, refs=refs)
    with setup['runtime'].service() as service:
        revision = service.work.get(setup['work_id'])['revision']
        if drift == 'requirement':
            service.work.revise(setup['work_id'], ReviseRequirement(
                request_id=uuid4(), expected_revision=revision, body='Changed requirement'))
        elif drift == 'task':
            service.work.write_task(setup['work_id'], WriteTask(
                request_id=uuid4(), expected_revision=revision, title='Changed task', body='Changed task body'),
                session['task_id'])
        elif drift == 'context_same_body_new_version':
            service.work.save_artifact(setup['work_id'], SaveArtifact(request_id=uuid4(),
                expected_revision=revision, artifact_id=UUID(artifact_id), requirement_version=1,
                title='Context', body='Same body'))
        else:
            # Explicit generation-drift fault, not a forged COMPLETED receipt.
            with service.db.transaction() as conn:
                conn.execute('UPDATE work_sessions SET generation=? WHERE id=?', (str(uuid4()), session['id']))
    providers[0].finish()
    held = wait_for(lambda: pending_read(setup, session['id']), lambda row: row['state'] == 'HELD')
    assert held['reason_code']
    assert held['promoted_run_id'] is None
    assert_only_predecessor(setup, session['id'])


def test_unknown_file_result_does_not_promote(setup, monkeypatch):
    from agentguard.recovery.protection import ProtectionService

    session, predecessor, providers = boot(setup)
    queue(setup, session['id'], predecessor)
    original = ProtectionService.capture

    def unavailable(self, scope_id, *args, **kwargs):
        if scope_id == setup['scope_id']:
            raise RuntimeError('Explicit file-finalization failure fixture')
        return original(self, scope_id, *args, **kwargs)

    monkeypatch.setattr(ProtectionService, 'capture', unavailable)
    providers[0].finish()
    prior = wait_for(lambda: run_read(setup, session['id'], predecessor), lambda row: row['state'] == 'COMPLETED')
    assert prior['file_result']['status'] == 'UNKNOWN'
    wait_for(lambda: pending_read(setup, session['id']), lambda row: row['state'] == 'HELD')
    assert_only_predecessor(setup, session['id'])


def test_async_api_queue_receipts_request_lookup_cancel_and_work_ownership(setup):
    session, predecessor, _ = boot(setup)
    app = FastAPI()
    app.include_router(execution_router(setup['runtime']))
    path = '/api/v1/works/' + setup['work_id'] + '/sessions/' + session['id'] + '/pending-instruction'

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            command = {'request_id': str(uuid4()), 'expected_revision': session_read(setup, session['id'])['revision'],
                       'requirement_version': 1, 'input': 'API next input', 'context_refs': [],
                       'predecessor_run_id': predecessor}
            created = await client.post(path, json=command)
            assert created.status_code == 201, created.text
            assert (await client.post(path, json=command)).json() == created.json()
            read = await client.get(path, params={'request_id': command['request_id']})
            assert read.status_code == 200
            assert read.json()['item']['input'] == command['input']
            assert read.json()['item']['request_id'] == command['request_id']
            wrong = await client.get(path.replace(setup['work_id'], str(uuid4())))
            assert wrong.status_code == 404
            invalid = await client.post(path, json={**command, 'request_id': str(uuid4()), 'authorized': True})
            assert invalid.status_code == 422
            invalid = await client.post(path, json={**command, 'request_id': str(uuid4()), 'predecessor_run_id': 'not-uuid'})
            assert invalid.status_code == 422
            cancelled = await client.post(path + '/' + created.json()['pending_id'] + '/cancel',
                json={'request_id': str(uuid4()), 'expected_revision': 1})
            assert cancelled.status_code == 200 and cancelled.json()['state'] == 'CANCELLED'
            assert (await client.get(path)).json()['item']['state'] == 'CANCELLED'

    asyncio.run(exercise())
    assert_only_predecessor(setup, session['id'])


def test_real_core_pending_routes_require_existing_session_auth(setup):
    from agentguard.api.server import create_app

    app = create_app(state_db_path=setup['db_path'], config={'base_dir': str(setup['workspace'].parent)})
    client = TestClient(app)
    path = '/api/v1/works/' + setup['work_id'] + '/sessions/' + str(uuid4()) + '/pending-instruction'
    assert client.get(path).status_code == 401
    assert client.post(path, json={}).status_code == 401
    assert client.post(path + '/' + str(uuid4()) + '/cancel', json={}).status_code == 401

@pytest.mark.parametrize('invalid', [
    {'predecessor_run_id': 'not-a-uuid'},
    {'expected_revision': True},
    {'input': '   '},
    {'input': '界' * 22000},
    {'authorized': True},
    {'_pending': True},
])
def test_queue_model_rejects_invalid_or_caller_authority_fields(invalid):
    from pydantic import ValidationError

    value = {'request_id': str(uuid4()), 'expected_revision': 1, 'requirement_version': 1,
             'input': 'Exact input', 'context_refs': [], 'predecessor_run_id': str(uuid4())}
    with pytest.raises(ValidationError):
        models.QueueInstruction.model_validate({**value, **invalid})


def test_cancel_model_requires_its_own_pending_revision():
    from pydantic import ValidationError

    for body in ({'request_id': str(uuid4())},
                 {'request_id': str(uuid4()), 'expected_revision': 0},
                 {'request_id': str(uuid4()), 'expected_revision': True}):
        with pytest.raises(ValidationError):
            models.CancelPendingInstruction.model_validate(body)

@pytest.mark.parametrize('terminal', ['FAILED', 'UNCERTAIN'])
def test_failure_holds_pending_even_while_channel_keeps_predecessor_owner(setup, terminal):
    session, predecessor, _ = boot(setup)
    queue(setup, session['id'], predecessor)
    runtime = setup['runtime']
    # Explicit error boundary injection: use the real runtime failure transaction,
    # without fabricating a provider completion or clearing its dispatch owner.
    runtime._fail_run(predecessor, terminal, 'EXPLICIT_PENDING_FAILURE_FIXTURE')
    assert runtime.channels[session['id']]['run_id'] == predecessor
    item = pending_read(setup, session['id'])
    assert item['state'] == 'HELD'
    assert item['reason_code'] == 'EXPLICIT_PENDING_FAILURE_FIXTURE'
    assert_only_predecessor(setup, session['id'])


def file_context(value):
    with value['runtime'].service() as service:
        scope = service.protection.scope(value['scope_id'])
        version = scope['latest_id']
        file_id = next(iter(service.protection._manifest(version)))
    return {'kind': 'file', 'ref_id': value['scope_id'], 'version_id': version, 'file_id': file_id}


def pending_pins(value, pending_id):
    with value['runtime'].journal() as service:
        return service.conn.execute(
            "SELECT version_id FROM protection_version_dependencies WHERE owner_kind='pending' AND owner_id=?",
            (pending_id,)).fetchall()


def test_pending_snapshot_sql_trigger_is_immutable_and_cancel_releases_file_pins(setup):
    import sqlite3

    session, predecessor, _ = boot(setup)
    ref = file_context(setup)
    _, receipt = queue(setup, session['id'], predecessor, refs=[ref])
    pending_id = receipt['pending_id']
    assert pending_pins(setup, pending_id) == [(ref['version_id'],)]
    with setup['runtime'].service() as service:
        before = service._pending_row(pending_id)
        with pytest.raises(sqlite3.IntegrityError, match='WORK_PENDING_INPUT_IMMUTABLE'), service.db.transaction() as conn:
            conn.execute("UPDATE work_pending_instructions SET snapshot_json='{}' WHERE id=?", (pending_id,))
        assert service._pending_row(pending_id)['snapshot_json'] == before['snapshot_json']
        service.cancel_pending(setup['work_id'], session['id'], pending_id,
            models.CancelPendingInstruction(request_id=uuid4(), expected_revision=1))
    assert pending_pins(setup, pending_id) == []
    assert_only_predecessor(setup, session['id'])


def test_promotion_transfers_file_pin_to_new_run_and_releases_pending_pin(setup):
    session, predecessor, providers = boot(setup)
    ref = file_context(setup)
    _, receipt = queue(setup, session['id'], predecessor, refs=[ref])
    assert pending_pins(setup, receipt['pending_id']) == [(ref['version_id'],)]
    providers[0].finish()
    item = wait_for(lambda: pending_read(setup, session['id']), lambda row: row['state'] == 'PROMOTED')
    wait_for(lambda: setup['owner']['sends'], lambda count: count == 2)
    assert pending_pins(setup, receipt['pending_id']) == []
    with setup['runtime'].journal() as service:
        assert service.conn.execute(
            "SELECT 1 FROM protection_version_dependencies WHERE owner_kind='run' AND owner_id=? AND version_id=?",
            (item['promoted_run_id'], ref['version_id'])).fetchone()


def test_promotion_receipt_failure_rolls_back_new_run_consumption_and_pin_transfer(setup):
    session, predecessor, providers = boot(setup)
    ref = file_context(setup)
    _, receipt = queue(setup, session['id'], predecessor, refs=[ref])
    with setup['runtime'].service() as service, service.db.transaction() as conn:
        pending = service._pending_row(receipt['pending_id'])
        # Explicit SQLite write-failure injection at the final idempotency receipt.
        conn.execute("CREATE TRIGGER fail_pending_promotion_receipt BEFORE INSERT ON work_requests "
                     "WHEN NEW.request_id='" + pending['promotion_request_id'] + "' "
                     "BEGIN SELECT RAISE(ABORT,'EXPLICIT_RECEIPT_FAILURE'); END")
    providers[0].finish()
    item = wait_for(lambda: pending_read(setup, session['id']), lambda row: row['state'] == 'HELD')
    assert item['promoted_run_id'] is None
    assert pending_pins(setup, receipt['pending_id']) == [(ref['version_id'],)]
    with setup['runtime'].service() as service:
        assert service.conn.execute('SELECT 1 FROM work_requests WHERE request_id=?',
                                    (pending['promotion_request_id'],)).fetchone() is None
        assert service.conn.execute("SELECT COUNT(*) FROM work_pending_instructions WHERE session_id=?",
                                    (session['id'],)).fetchone()[0] == 1
    assert_only_predecessor(setup, session['id'])


def test_two_concurrent_enqueue_requests_have_one_durable_winner(setup):
    from concurrent.futures import ThreadPoolExecutor

    session, predecessor, _ = boot(setup)
    current = session_read(setup, session['id'])
    barrier = threading.Barrier(2)

    def enqueue_once(index):
        command = models.QueueInstruction(request_id=uuid4(), expected_revision=current['revision'],
            requirement_version=1, input='Concurrent next ' + str(index), predecessor_run_id=UUID(predecessor))
        barrier.wait(timeout=5)
        try:
            with setup['runtime'].service() as service:
                return service.enqueue_pending(setup['work_id'], session['id'], command)
        except WorkError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(enqueue_once, [1, 2]))
    winners = [item for item in results if isinstance(item, dict)]
    assert len(winners) == 1
    assert [item for item in results if isinstance(item, str)] == ['PENDING_INSTRUCTION_SLOT_OCCUPIED']
    with setup['runtime'].service() as service:
        assert service.conn.execute(
            "SELECT COUNT(*) FROM work_pending_instructions WHERE session_id=? AND state IN ('PENDING','HELD')",
            (session['id'],)).fetchone()[0] == 1
    assert_only_predecessor(setup, session['id'])

def test_verified_program_identity_replacement_holds_pending(setup):
    from agentguard.execution.service import encoded

    session, predecessor, providers = boot(setup)
    queue(setup, session['id'], predecessor)
    with setup['runtime'].service() as service, service.db.transaction() as conn:
        # Explicit persisted verified-entry drift fixture, no executable mutation.
        row = conn.execute('SELECT metadata_json FROM discovery_applications WHERE id=?',
                           (setup['application_id'],)).fetchone()
        metadata = json.loads(row[0])
        metadata['identity'] = 'file-stat-sha256:' + '0' * 64
        conn.execute('UPDATE discovery_applications SET metadata_json=? WHERE id=?',
                     (encoded(metadata), setup['application_id']))
    providers[0].finish()
    item = wait_for(lambda: pending_read(setup, session['id']), lambda row: row['state'] == 'HELD')
    assert item['reason_code'] == 'EXECUTION_PROGRAM_IDENTITY_CHANGED'
    assert_only_predecessor(setup, session['id'])


def test_session_disconnect_holds_pending_and_cannot_replay_on_completion(setup):
    session, predecessor, providers = boot(setup)
    queue(setup, session['id'], predecessor)
    with setup['runtime'].service() as service:
        service.session_state(session['id'], 'DISCONNECTED', 'SESSION_CONNECTION_LOST')
    item = pending_read(setup, session['id'])
    assert item['state'] == 'HELD'
    assert item['reason_code'] == 'SESSION_CONNECTION_LOST'
    providers[0].finish()
    assert pending_read(setup, session['id'])['state'] == 'HELD'
    assert_only_predecessor(setup, session['id'])
