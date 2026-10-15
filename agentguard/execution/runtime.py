"""Core-owned provider connections, bounded event batching and durable dispatch."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agentguard.recovery.protection import recovery_lease
from agentguard.storage.db import StateDB
from agentguard.work.service import WorkError

from .service import ACTIVE, TERMINAL, ExecutionService, digest, encoded, now


class ExecutionRuntime:
    def __init__(self, db_path, domains, catalog_context, protection, *, provider_factory=None):
        self.db_path, self.domains, self.catalog_context = Path(db_path), domains, catalog_context
        self.protection = protection
        self.provider_factory = provider_factory
        self.channels = {}
        self.lock = threading.RLock()
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='asg-execution')
        self.jobs = set()
        self.last_error = None
        self.accepting = False
        self.last_check = self.last_collect = 0.0

    @contextmanager
    def service(self):
        with self.catalog_context() as catalog, self.protection.service() as protection:
            catalog.db._conn.execute('PRAGMA synchronous=FULL')
            yield ExecutionService(catalog.db, self.domains, catalog, protection)

    @contextmanager
    def journal(self):
        database = StateDB(self.db_path)
        try:
            database.connect()
            database._conn.execute('PRAGMA synchronous=FULL')
            yield ExecutionService(database, self.domains)
        finally:
            database.close()

    def notify(self):
        self.wake.set()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name='asg-execution-control', daemon=True)
        self.thread.start()

    def _submit(self, key, function, *args):
        with self.lock:
            if key in self.jobs or self.stop_event.is_set():
                return
            self.jobs.add(key)
        def perform():
            try:
                function(*args)
            finally:
                with self.lock:
                    self.jobs.discard(key)
                self.wake.set()
        self.pool.submit(perform)

    def _run(self):
        try:
            with recovery_lease(self.db_path, name='execution', busy_code='EXECUTION_WORKER_BUSY'):
                self._startup_reconcile()
                self.accepting = True
                while not self.stop_event.is_set():
                    try:
                        self._tick()
                        self.last_error = None
                    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                        self.last_error = getattr(exc, 'reason_code', getattr(exc, 'code', 'EXECUTION_WORKER_UNAVAILABLE'))
                    self.wake.wait(0.1)
                    self.wake.clear()
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self.last_error = getattr(exc, 'reason_code', 'EXECUTION_WORKER_UNAVAILABLE')
        finally:
            self.accepting = False

    def _startup_reconcile(self):
        finalizing = []
        with self.journal() as service, service.db.transaction() as conn:
            for run_id, dispatch_state, state, result_json, session_id in conn.execute('SELECT id,dispatch_state,state,result_json,session_id FROM work_runs WHERE state IN (' + ','.join('?' * len(ACTIVE)) + ')', ACTIVE).fetchall():
                result_value = json.loads(result_json)
                if state == 'FINALIZING' and result_value.get('status') in {'completed', 'interrupted', 'failed'}:
                    receipt = conn.execute("SELECT payload_digest FROM work_run_events WHERE run_id=? AND type IN ('PROVIDER_RESULT','RECONCILIATION') ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
                    if receipt and (receipt[0] == digest(result_value) or result_value.get('source') == 'NATIVE_STATUS_RECONCILIATION'):
                        finalizing.append((session_id, run_id))
                        continue
                result = 'INTERRUPTED' if dispatch_state == 'NOT_SENT' else 'UNCERTAIN'
                service.append(conn, run_id, 'RECONCILIATION', {'reason_code': 'CORE_RESTARTED', 'dispatch_state': dispatch_state, 'previous_state': state})
                conn.execute('UPDATE work_runs SET state=?,reason_code=?,revision=revision+1,updated_at=?,completed_at=? WHERE id=?',
                             (result, 'RUN_NOT_DISPATCHED' if result == 'INTERRUPTED' else 'RUN_RECONCILIATION_REQUIRED', now(), now() if result == 'INTERRUPTED' else None, run_id))
            conn.execute("UPDATE work_sessions SET state='DISCONNECTED',reason_code='CORE_RESTARTED',generation=NULL,revision=revision+1,updated_at=? WHERE state IN ('READY','CONNECTING')", (now(),))
        for session_id, run_id in finalizing:
            self._submit('finalize:' + run_id, self._finalize, session_id, run_id)

    def _tick(self):
        with self.journal() as service:
            for session_id, in service.conn.execute("SELECT id FROM work_sessions WHERE state='CONNECTING' ORDER BY created_at LIMIT 8").fetchall():
                self._submit('session:' + session_id, self._connect, session_id)
            for run_id, session_id, state in service.conn.execute("SELECT id,session_id,state FROM work_runs WHERE state IN ('QUEUED','CANCELLING') ORDER BY created_at LIMIT 8").fetchall():
                self._submit('run:' + run_id if state == 'QUEUED' else 'cancel:' + run_id,
                             self._dispatch if state == 'QUEUED' else self._cancel, session_id, run_id)
        with self.lock:
            for channel in list(self.channels.values()):
                self._flush(channel)
        current = time.monotonic()
        if current - self.last_check >= 2:
            self.last_check = current
            self._check_channels()
        if current - self.last_collect >= 300:
            self.last_collect = current
            with self.journal() as service, service.db.transaction():
                service.collect()

    def _build_provider(self, session, on_message):
        if self.provider_factory:
            return self.provider_factory(session, on_message)
        from .protocol import RpcProcess
        from .providers import CodexProvider, KimiProvider
        launch = self.domains.execution_launch(json.loads(session['identity_json']), session['provider'])
        process = RpcProcess(**launch, on_message=on_message)
        return (CodexProvider if session['provider'] == 'CODEX' else KimiProvider)(process, on_event=on_message)

    def _connect(self, session_id):
        channel = None
        try:
            with self.service() as service:
                session = service.one('work_sessions', session_id)
                if session['state'] != 'CONNECTING':
                    return
                service.validate_selection(session['application_id'], session['store_id'], session['scope_id'], expected=json.loads(session['identity_json']))
                with self.lock:
                    if len(self.channels) >= 8:
                        raise WorkError('EXECUTION_CONNECTION_LIMIT', 503)
                    generation = str(uuid4())
                    channel = {'session_id': session_id, 'generation': generation, 'run_id': None,
                               'text': [], 'text_bytes': 0, 'text_item': None, 'text_at': None,
                               'provider': None, 'session': session, 'pending_terminal': [], 'cancel_sent': False,
                               'output_items': set()}
                    self.channels[session_id] = channel
                provider = self._build_provider(session, lambda raw: self._message(session_id, generation, raw))
                channel['provider'] = provider
                capabilities = provider.initialize()
                if not capabilities.get('read_only_supported' if session['read_only'] else 'write'):
                    raise WorkError(capabilities.get('reason_code', 'EXECUTION_POLICY_UNSUPPORTED'), 422)
                native_id = session['native_session_id']
                if session['binding_id']:
                    native_id = service.conn.execute('SELECT provider_session_id FROM protection_sessions WHERE id=?', (session['binding_id'],)).fetchone()[0]
                channel['create_dispatched'] = True
                created = provider.create_session(json.loads(session['identity_json'])['binding']['root'],
                    mode='RESUME' if session['mode'] == 'CONTINUE' else session['mode'], native_session_id=native_id, read_only=bool(session['read_only']))
                native_id = created.get('native_session_id')
                if not native_id:
                    raise WorkError('SESSION_NATIVE_ID_MISSING')
                channel['native_session_id'] = native_id
                with self.lock:
                    if self.stop_event.is_set() or self.channels.get(session_id) is not channel:
                        raise WorkError('SESSION_CONNECTION_INTERRUPTED')
                    service.session_ready(session_id, native_id, generation, {**capabilities, 'send': True, 'stream': True,
                        'responses': True, 'cancel': True, 'ownership': 'ASG_MANAGED'})
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            reason = getattr(exc, 'reason_code', getattr(exc, 'code', 'SESSION_CONNECTION_FAILED'))
            with self.journal() as service:
                service.session_state(session_id, 'UNCERTAIN' if channel and channel.get('create_dispatched') else 'UNAVAILABLE', reason)
            if channel:
                self._close_channel(channel)

    @staticmethod
    def _prompt(snapshot):
        parts = ['Work requirement:\n' + snapshot['requirement']]
        if snapshot['task']:
            parts.append('Selected task:\n' + snapshot['task']['title'] + '\n' + snapshot['task']['body'])
            for task in snapshot['task']['dependencies']:
                parts.append('Dependency snapshot:\n' + task['title'] + '\n' + task['body'])
        for context in snapshot['context']:
            parts.append('Selected context ' + encoded(context['ref']) + ':\n' + context['body'])
        parts.append('Current input:\n' + snapshot['input'])
        return '\n\n'.join(parts)

    def _dispatch(self, session_id, run_id):
        try:
            with self.service() as service:
                session, run = service.one('work_sessions', session_id), service.one('work_runs', run_id)
                if run['state'] != 'QUEUED':
                    return
                service.validate_selection(session['application_id'], session['store_id'], session['scope_id'], expected=json.loads(session['identity_json']))
                with self.lock:
                    channel = self.channels.get(session_id)
                    if not channel or channel['generation'] != run['generation'] or not channel['provider']:
                        raise WorkError('SESSION_CONNECTION_LOST')
                    channel['run_id'] = run_id
                    channel['cancel_sent'] = False
                    channel['output_items'].clear()
                    with service.db.transaction() as conn:
                        row = service.one('work_runs', run_id)
                        if row['state'] != 'QUEUED':
                            return
                        service.append(conn, run_id, 'DISPATCH_INTENT', {'generation': channel['generation']})
                        conn.execute("UPDATE work_runs SET state='DISPATCHING',dispatch_state='SENDING',reason_code='RUN_DISPATCHING',revision=revision+1 WHERE id=?", (run_id,))
                snapshot = json.loads(run['input_json'])
                result = channel['provider'].start_turn(session['native_session_id'], self._prompt(snapshot),
                    cwd=snapshot['identity']['binding']['root'], read_only=bool(session['read_only']))
                with self.lock, service.db.transaction() as conn:
                    current = service.one('work_runs', run_id)
                    if current['state'] in TERMINAL or current['state'] == 'FINALIZING':
                        return
                    turn_id = result.get('native_turn_id')
                    service.append(conn, run_id, 'DISPATCH_ACCEPTED', {'native_turn_id': turn_id})
                    conn.execute("UPDATE work_runs SET native_turn_id=COALESCE(native_turn_id,?),dispatch_state='ACCEPTED',state=CASE WHEN state='DISPATCHING' THEN 'RUNNING' ELSE state END,reason_code='RUN_ACCEPTED',revision=revision+1 WHERE id=?", (turn_id, run_id))
                if result.get('status') in {'completed', 'interrupted', 'failed'}:
                    self._terminal(channel, result)
                for event in channel['pending_terminal']:
                    self._event(channel, event)
                channel['pending_terminal'].clear()
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            with self.journal() as service:
                row = service.one('work_runs', run_id)
            if row['state'] not in TERMINAL and row['state'] != 'FINALIZING':
                self._fail_run(run_id, 'FAILED' if row['dispatch_state'] == 'NOT_SENT' else 'UNCERTAIN',
                               getattr(exc, 'reason_code', getattr(exc, 'code', 'RUN_DISPATCH_FAILED')))

    def _message(self, session_id, generation, raw):
        try:
            with self.lock:
                channel = self.channels.get(session_id)
                if not channel or channel['generation'] != generation or not channel['provider']:
                    return
                events = [raw] if 'kind' in raw else channel['provider'].normalize(raw)
                for event in events:
                    self._event(channel, event)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            with self.lock:
                channel = self.channels.get(session_id)
            if channel and channel['run_id']:
                self._fail_run(channel['run_id'], 'UNCERTAIN', 'EXECUTION_EVENT_PERSISTENCE_FAILED')
                self._close_channel(channel)

    def _event(self, channel, event):
        run_id = channel['run_id']
        if not run_id:
            return
        kind, payload = event['kind'], event.get('payload', {})
        if event.get('native_session_id') not in {None, channel.get('native_session_id')}:
            return
        if kind.endswith('.replayed') or payload.get('replay') is True:
            return
        with self.journal() as service:
            run = service.one('work_runs', run_id)
            if run['state'] in TERMINAL or run['state'] in {'FINALIZING', 'UNCERTAIN'}:
                return
            turn_id = event.get('native_turn_id')
            if run['native_turn_id'] and turn_id and run['native_turn_id'] != turn_id:
                return
            if kind == 'turn.started' and turn_id and not run['native_turn_id']:
                with service.db.transaction() as conn:
                    conn.execute("UPDATE work_runs SET native_turn_id=?,dispatch_state='ACCEPTED',state=CASE WHEN state='DISPATCHING' THEN 'RUNNING' ELSE state END WHERE id=?", (turn_id, run_id))
            if kind == 'turn.completed':
                if channel['session']['provider'] == 'CODEX' and not run['native_turn_id']:
                    if len(channel['pending_terminal']) < 8:
                        channel['pending_terminal'].append(event)
                    return
                self._terminal(channel, payload)
                return
            if kind == 'output.delta':
                text = payload.get('text', payload.get('delta', ''))
                if not isinstance(text, str) or not text:
                    return
                item = payload.get('item_id')
                if channel['text_item'] != item or channel['text_bytes'] + len(text.encode()) > 128 * 1024:
                    self._flush(channel)
                if not channel['text']:
                    channel['text_at'] = now()
                channel['text'].append(text)
                channel['text_bytes'] += len(text.encode())
                channel['text_item'] = item
                channel['output_items'].add(item)
                return
            self._flush(channel)
            if kind in {'approval.requested', 'input.requested'}:
                action_ref = str(uuid4())
                event_type = 'APPROVAL' if kind == 'approval.requested' else 'QUESTION'
                payload = {**payload, 'generation': channel['generation'], 'native_request_id': event.get('native_request_id')}
                with service.db.transaction() as conn:
                    service.append(conn, run_id, event_type, payload, action_ref=action_ref)
                    conn.execute('UPDATE work_runs SET state=?,revision=revision+1,reason_code=? WHERE id=?',
                                 ('WAITING_APPROVAL' if event_type == 'APPROVAL' else 'WAITING_INPUT', 'RUN_' + event_type + '_REQUIRED', run_id))
            elif kind == 'connection.failed':
                self._fail_run(run_id, 'UNCERTAIN', payload.get('reason_code', 'SESSION_CONNECTION_LOST'))
            elif kind == 'output.completed':
                if payload.get('item_id') not in channel['output_items'] and payload.get('text'):
                    with service.db.transaction() as conn:
                        service.append(conn, run_id, 'TEXT', {'text': payload['text'], 'item_id': payload.get('item_id')})
            else:
                with service.db.transaction() as conn:
                    service.append(conn, run_id, 'TOOL' if kind.startswith('tool.') else 'STATUS', {'kind': kind, **payload})

    def _flush(self, channel):
        if not channel['text']:
            return
        text = ''.join(channel['text'])
        with self.journal() as service, service.db.transaction() as conn:
            service.append(conn, channel['run_id'], 'TEXT', {'text': text, 'item_id': channel['text_item']}, observed_at=channel['text_at'])
        channel['text'], channel['text_bytes'], channel['text_item'], channel['text_at'] = [], 0, None, None

    def _terminal(self, channel, result):
        self._flush(channel)
        run_id = channel['run_id']
        with self.journal() as service, service.db.transaction() as conn:
            row = service.one('work_runs', run_id)
            if row['state'] in TERMINAL or row['state'] in {'FINALIZING', 'UNCERTAIN'}:
                return
            service.append(conn, run_id, 'PROVIDER_RESULT', result)
            conn.execute("UPDATE work_runs SET state='FINALIZING',dispatch_state='ACCEPTED',result_json=?,reason_code='RUN_FILES_PENDING',revision=revision+1 WHERE id=?", (encoded(result), run_id))
        self._submit('finalize:' + run_id, self._finalize, channel['session_id'], run_id)

    def _finalize(self, session_id, run_id):
        with self.service() as service:
            row = service.one('work_runs', run_id)
            file_result = {'status': 'UNKNOWN', 'reason_code': 'EXECUTION_FILE_CAPTURE_FAILED', 'attribution': 'UNATTRIBUTED'}
            with service.protection.lock:
                try:
                    capture = service.protection.capture(row['scope_id'], force=True)
                    scope = service.protection.scope(row['scope_id'])
                    file_result = {**capture, 'status': 'AVAILABLE' if capture['status'] in {'RECORDED', 'UNCHANGED'} else 'UNKNOWN',
                                   'version_id': scope['latest_id'], 'baseline_id': row['baseline_id'], 'attribution': 'UNATTRIBUTED'}
                except (OSError, RuntimeError, ValueError, sqlite3.Error):
                    pass
                result = json.loads(row['result_json'])
                state = {'completed': 'COMPLETED', 'interrupted': 'CANCELLED', 'failed': 'FAILED'}.get(result.get('status'), 'FAILED')
                with service.db.transaction() as conn:
                    if service.one('work_runs', run_id)['state'] != 'FINALIZING':
                        return
                    if file_result.get('version_id'):
                        conn.execute("INSERT OR IGNORE INTO protection_version_dependencies VALUES ('run',?,?)", (run_id, file_result['version_id']))
                        conn.execute("INSERT OR IGNORE INTO protection_work_links SELECT ?,'batch',id,?,'OBSERVED_DURING_MANAGED_RUN',? FROM protection_changes WHERE scope_id=? AND after_id=?", (row['work_id'], service.one('work_sessions', session_id)['task_id'] or '', now(), row['scope_id'], file_result['version_id']))
                    service.append(conn, run_id, 'RUN_COMPLETED', {'state': state, 'file_result': file_result})
                    conn.execute('UPDATE work_runs SET state=?,file_result_json=?,reason_code=?,revision=revision+1,completed_at=?,updated_at=? WHERE id=?',
                                 (state, encoded(file_result), 'RUN_' + state, now(), now(), run_id))
                    from agentguard.evidence.models import EventType
                    service.ledger(conn, run_id, EventType.SESSION_COMPLETED if state == 'COMPLETED' else EventType.SESSION_FAILED,
                                   state, provider_result=result.get('status'), file_status=file_result['status'], work_id=row['work_id'])
            with self.lock:
                channel = self.channels.get(session_id)
                if channel and channel['run_id'] == run_id:
                    channel['run_id'] = None

    def _fail_run(self, run_id, state, reason):
        with self.journal() as service, service.db.transaction() as conn:
            row = service.one('work_runs', run_id)
            if row['state'] in TERMINAL or row['state'] == 'FINALIZING':
                return
            service.append(conn, run_id, 'RUN_UNCERTAIN' if state == 'UNCERTAIN' else 'RUN_FAILED', {'reason_code': reason})
            conn.execute('UPDATE work_runs SET state=?,reason_code=?,revision=revision+1,updated_at=?,completed_at=? WHERE id=?',
                         (state, reason, now(), None if state == 'UNCERTAIN' else now(), run_id))

    def reconcile(self, work_id, run_id, command):
        with self.journal() as service:
            run = service.one('work_runs', run_id, work_id)
            previous = service.work.receipt('run-reconcile', [work_id, run_id], command)
            if previous is not None:
                return previous
            if run['state'] != 'UNCERTAIN':
                raise WorkError('RUN_NOT_UNCERTAIN')
            if command.expected_revision is not None and command.expected_revision != run['revision']:
                raise WorkError('RUN_REVISION_CONFLICT')
            session = service.one('work_sessions', run['session_id'], work_id)
        with self.lock:
            channel = self.channels.get(run['session_id'])
        if not channel or not channel['provider'] or not run['native_turn_id'] or session['provider'] != 'CODEX':
            raise WorkError('RUN_RECONCILIATION_UNAVAILABLE')
        with self.service() as service:
            service.validate_selection(session['application_id'], session['store_id'], session['scope_id'], expected=json.loads(session['identity_json']))
        turn = None
        cursor = None
        for _ in range(10):
            params = {'threadId': session['native_session_id'], 'itemsView': 'notLoaded', 'limit': 100, 'sortDirection': 'desc'}
            if cursor:
                params['cursor'] = cursor
            try:
                result = channel['provider'].rpc.request('thread/turns/list', params, timeout=10)
            except RuntimeError:
                result = {'thread': channel['provider'].rpc.request('thread/read', {'threadId': session['native_session_id'], 'includeTurns': True}, timeout=10).get('thread', {})}
            turns = result.get('data', result.get('thread', {}).get('turns', []))
            turn = next((item for item in turns if isinstance(item, dict) and item.get('id') == run['native_turn_id']), None)
            cursor = result.get('nextCursor')
            if turn or not cursor:
                break
        status = turn.get('status') if turn else 'unknown'
        result_state = {'completed': 'COMPLETED', 'interrupted': 'CANCELLED', 'failed': 'FAILED'}.get(status, 'UNCERTAIN')
        with self.journal() as service:
            def apply(conn, timestamp):
                current = service.one('work_runs', run_id, work_id)
                if current['state'] != 'UNCERTAIN':
                    raise WorkError('RUN_REVISION_CONFLICT')
                service.append(conn, run_id, 'RECONCILIATION', {'native_turn_id': run['native_turn_id'], 'status': status, 'history_replay': False})
                if result_state != 'UNCERTAIN':
                    conn.execute("UPDATE work_runs SET state='FINALIZING',result_json=?,revision=revision+1,reason_code='RUN_RECONCILED_FILES_PENDING' WHERE id=?", (encoded({'status': status, 'source': 'NATIVE_STATUS_RECONCILIATION'}), run_id))
                return {'run_id': run_id, 'state': 'FINALIZING' if result_state != 'UNCERTAIN' else 'UNCERTAIN', 'native_status': status}
            receipt = service.work._command('run-reconcile', [work_id, run_id], command, apply)
        if result_state != 'UNCERTAIN':
            self._submit('finalize:' + run_id, self._finalize, run['session_id'], run_id)
        return receipt

    def _cancel(self, session_id, run_id):
        with self.lock:
            channel = self.channels.get(session_id)
            if channel and channel['cancel_sent']:
                return
        with self.journal() as service:
            run = service.one('work_runs', run_id)
        if run['state'] != 'CANCELLING':
            return
        if run['dispatch_state'] == 'NOT_SENT':
            with self.journal() as service, service.db.transaction() as conn:
                service.append(conn, run_id, 'RUN_CANCELLED', {'reason_code': 'CANCELLED_BEFORE_DISPATCH'})
                conn.execute("UPDATE work_runs SET state='CANCELLED',reason_code='CANCELLED_BEFORE_DISPATCH',revision=revision+1,completed_at=? WHERE id=? AND dispatch_state='NOT_SENT'", (now(), run_id))
            return
        try:
            if not channel or channel['run_id'] != run_id:
                raise WorkError('SESSION_CONNECTION_LOST')
            if channel['session']['provider'] == 'CODEX' and not run['native_turn_id']:
                return
            channel['cancel_sent'] = True
            channel['provider'].cancel(channel['native_session_id'], run['native_turn_id'])
            with self.journal() as service, service.db.transaction() as conn:
                service.append(conn, run_id, 'CANCEL_SENT', {})
        except (OSError, RuntimeError, ValueError):
            self._fail_run(run_id, 'UNCERTAIN', 'RUN_CANCEL_UNCONFIRMED')

    def send_response(self, session_id, run_id, command):
        try:
            with self.service() as service:
                session = service.one('work_sessions', session_id)
                service.validate_selection(session['application_id'], session['store_id'], session['scope_id'], expected=json.loads(session['identity_json']))
                row = service.conn.execute('SELECT payload_json FROM work_run_events WHERE run_id=? AND seq=? AND action_ref=?', (run_id, command.event_seq, command.action_ref)).fetchone()
                action = json.loads(row[0])
                with self.lock:
                    channel = self.channels.get(session_id)
                    if not channel or channel['generation'] != action['generation'] or channel['run_id'] != run_id:
                        raise WorkError('RUN_ACTION_CONNECTION_EXPIRED')
                with service.db.transaction() as conn:
                    if conn.execute("SELECT 1 FROM work_run_events WHERE run_id=? AND type='RESPONSE_DISPATCH_INTENT' AND json_extract(payload_json,'$.action_ref')=?", (run_id, command.action_ref)).fetchone():
                        return
                    service.append(conn, run_id, 'RESPONSE_DISPATCH_INTENT', {'action_ref': command.action_ref})
                channel['provider'].reply(action['native_request_id'], command.response)
                with service.db.transaction() as conn:
                    service.append(conn, run_id, 'RESPONSE_SENT', {'action_ref': command.action_ref})
                    pending = conn.execute("""SELECT e.type FROM work_run_events e WHERE e.run_id=? AND e.type IN ('APPROVAL','QUESTION')
                        AND NOT EXISTS (SELECT 1 FROM work_run_events s WHERE s.run_id=e.run_id AND s.type='RESPONSE_SENT'
                        AND json_extract(s.payload_json,'$.action_ref')=e.action_ref)""", (run_id,)).fetchall()
                    next_state = 'WAITING_APPROVAL' if any(r[0] == 'APPROVAL' for r in pending) else 'WAITING_INPUT' if pending else 'RUNNING'
                    conn.execute("UPDATE work_runs SET state=?,revision=revision+1 WHERE id=? AND state IN ('WAITING_APPROVAL','WAITING_INPUT')", (next_state, run_id))
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            self._fail_run(run_id, 'UNCERTAIN', 'RUN_RESPONSE_UNCONFIRMED')

    def _check_channels(self):
        with self.lock:
            channels = list(self.channels.values())
        for channel in channels:
            if not channel['provider']:
                continue
            try:
                identity = json.loads(channel['session']['identity_json'])
                with self.domains._lock:
                    source, _ = self.domains._bound(identity['binding'])
                if not source['connected'] or not channel['provider'].rpc.alive:
                    raise WorkError('SESSION_CONNECTION_LOST')
            except (OSError, RuntimeError, ValueError):
                with self.lock:
                    # Preserve already observed output before retiring the channel.
                    # If persistence fails, leave it attached for the next tick.
                    self._flush(channel)
                    if channel['run_id']:
                        self._fail_run(channel['run_id'], 'UNCERTAIN', 'SESSION_CONNECTION_LOST')
                with self.journal() as service:
                    service.session_state(channel['session_id'], 'DISCONNECTED', 'SESSION_CONNECTION_LOST')
                self._close_channel(channel)

    def _close_channel(self, channel):
        with self.lock:
            if self.channels.get(channel['session_id']) is channel:
                self.channels.pop(channel['session_id'], None)
        if channel['provider']:
            channel['provider'].rpc.close()

    def close_session(self, session_id):
        with self.lock:
            channel = self.channels.get(session_id)
        if channel:
            self._close_channel(channel)

    def close(self):
        self.accepting = False
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
        with self.lock:
            channels = list(self.channels.values())
        for channel in channels:
            if channel['run_id']:
                with self.journal() as service, service.db.transaction() as conn:
                    row = service.one('work_runs', channel['run_id'])
                    if row['state'] in ACTIVE and row['state'] not in {'UNCERTAIN', 'FINALIZING'}:
                        service.append(conn, row['id'], 'CANCEL_REQUESTED', {'reason_code': 'CORE_SHUTDOWN'})
                        conn.execute("UPDATE work_runs SET state='CANCELLING',revision=revision+1 WHERE id=?", (row['id'],))
                self._cancel(channel['session_id'], channel['run_id'])
                self._fail_run(channel['run_id'], 'UNCERTAIN', 'CORE_SHUTDOWN_UNCONFIRMED')
            self._close_channel(channel)
        self.pool.shutdown(wait=True, cancel_futures=True)
