"""Work execution intent, receipts and journal; provider I/O stays outside transactions."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agentguard.core.sanitizer import contains_sensitive_data, sanitize_text
from agentguard.discovery.catalog import record_sessions
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.work.service import WorkError, WorkService

ACTIVE = ('QUEUED', 'DISPATCHING', 'RUNNING', 'WAITING_APPROVAL', 'WAITING_INPUT', 'CANCELLING', 'FINALIZING', 'UNCERTAIN')
TERMINAL = ('COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED')
RUN_BUDGET = 32 * 1024 * 1024
GLOBAL_BUDGET = 512 * 1024 * 1024
CONTROL_RESERVE = 1024 * 1024
MAX_EVENT = 256 * 1024
RUN_SUMMARY = '''id,work_id,session_id,scope_id,requirement_version,input_digest,input_summary,
    baseline_id,generation,native_turn_id,state,dispatch_state,reason_code,revision,last_seq,
    journal_bytes,history_status,output_truncated,result_json,file_result_json,created_at,updated_at,completed_at'''


def now():
    return datetime.now(UTC).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def safe(value):
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item) for item in value]
    return value


def reject_sensitive(value):
    if isinstance(value, str) and contains_sensitive_data(value):
        raise WorkError('WORK_CONTENT_SENSITIVE', 422)
    if isinstance(value, dict):
        for item in value.values():
            reject_sensitive(item)
    elif isinstance(value, list):
        for item in value:
            reject_sensitive(item)


from .pending import PendingInstructionMixin


class ExecutionService(PendingInstructionMixin):
    def __init__(self, database, domains, catalog=None, protection=None):
        self.db, self.conn, self.domains = database, database._conn, domains
        self.work, self.catalog, self.protection = WorkService(database), catalog, protection

    def one(self, table, object_id, work_id=None):
        if table not in {'work_sessions', 'work_runs'}:
            raise ValueError('unsupported execution record')
        cursor = self.conn.execute(f'SELECT * FROM {table} WHERE id=?' + (' AND work_id=?' if work_id else ''),
                                   (object_id, work_id) if work_id else (object_id,))
        row = cursor.fetchone()
        if row is None:
            raise WorkError('SESSION_NOT_FOUND' if table == 'work_sessions' else 'RUN_NOT_FOUND', 404)
        return dict(zip((col[0] for col in cursor.description), row))

    @staticmethod
    def ledger(conn, subject, event_type, result, **facts):
        if verify_ledger(conn):
            raise WorkError('EXECUTION_LEDGER_INVALID', 503)
        timestamp = datetime.now(UTC)
        event_id = 'execution-event-' + str(uuid4())
        EvidenceLedger().append(conn, EvidenceEvent(
            schema_version=1, event_id=event_id, recorded_at=timestamp, observed_at=timestamp,
            event_family=EventFamily.SUPERVISION, event_type=event_type, source='core-execution',
            result=result, execution_domain_id=None, supervision_session_id=None,
            transaction_id=None, checkpoint_id=None, subject_ref=subject, evidence_refs=(),
            payload_safe=facts))
        return event_id

    def validate_selection(self, application_id, store_id, scope_id, *, expected=None):
        application = self.conn.execute('SELECT source_id,path,metadata_json FROM discovery_applications WHERE id=?',
                                        (application_id,)).fetchone()
        store = self.conn.execute('SELECT source_id,provider,path,metadata_json FROM discovery_session_stores WHERE id=?',
                                  (store_id,)).fetchone()
        if not application or not store:
            raise WorkError('EXECUTION_SELECTION_NOT_FOUND', 404)
        source_id = self.catalog.resolve(application[0])
        if source_id != self.catalog.resolve(store[0]):
            raise WorkError('EXECUTION_SOURCE_MISMATCH', 422)
        self.catalog._source(source_id)
        metadata = json.loads(application[2])
        if metadata.get('provider') not in {'CODEX', 'KIMI_CODE'} or metadata['provider'] != store[1]:
            raise WorkError('EXECUTION_PROVIDER_UNSUPPORTED', 422)
        entry = self.domains.discovery_query(source_id, {'action': 'application_entry', 'path': application[1]})
        if entry.get('status') != 'AVAILABLE' or not entry.get('identity') or entry['identity'] != metadata.get('identity'):
            raise WorkError('EXECUTION_PROGRAM_IDENTITY_CHANGED')
        observed_store = self.domains.discovery_query(source_id, {'action': 'inspect_session_store', 'provider': store[1], 'path': store[2]})
        store_metadata = json.loads(store[3])
        if (observed_store.get('status') not in {'AVAILABLE', 'EMPTY', 'DEGRADED'} or
                not observed_store.get('identity') or observed_store['identity'] != store_metadata.get('identity')):
            raise WorkError('EXECUTION_STORE_IDENTITY_CHANGED')
        scope = self.protection.scope(scope_id)
        if self.catalog.resolve(scope['source_id']) != source_id or not scope['enabled']:
            raise WorkError('EXECUTION_SCOPE_UNAVAILABLE')
        binding = json.loads(scope['binding_json'])
        validated = self.domains.register(source_id, binding['root'])
        if validated['identity'] != binding['identity']:
            raise WorkError('WORKSPACE_ROOT_IDENTITY_CHANGED')
        identity = {'application': entry['identity'], 'entry_path': entry['entry_path'],
                    'store': observed_store['identity'], 'store_path': store[2],
                    'scope': scope['identity_digest'], 'binding': validated}
        if expected and expected != identity:
            raise WorkError('EXECUTION_IDENTITY_CHANGED')
        return {'provider': store[1], 'source_id': source_id, 'identity': identity, 'scope': scope}

    def create_session(self, work_id, command):
        previous = self.work.receipt('session-create', work_id, command)
        if previous is not None:
            return previous
        selected = self.validate_selection(command.application_id, command.store_id, command.scope_id)
        native_id = None
        if command.mode != 'NEW':
            if not command.binding_id:
                raise WorkError('SESSION_BINDING_REQUIRED', 422)
            row = self.conn.execute('SELECT source_id,store_id,provider,provider_session_id,metadata_json FROM protection_sessions WHERE id=?',
                                    (command.binding_id,)).fetchone()
            if not row or tuple(row[:3]) != (selected['source_id'], command.store_id, selected['provider']):
                raise WorkError('SESSION_BINDING_MISMATCH', 422)
            facts = json.loads(row[4])
            if facts.get('verification_status') == 'HISTORICAL_UNVERIFIED':
                raise WorkError('SESSION_BINDING_UNVERIFIED')
            if facts.get('cwd') != selected['identity']['binding']['root']:
                raise WorkError('SESSION_WORKSPACE_MISMATCH')
            native_id = row[3]
        elif command.binding_id:
            raise WorkError('SESSION_BINDING_UNEXPECTED', 422)

        def apply(conn, timestamp):
            self.work._work(conn, work_id, command.expected_revision)
            task_id = str(command.task_id) if command.task_id else None
            if task_id and not conn.execute('SELECT 1 FROM work_tasks WHERE work_id=? AND id=?', (work_id, task_id)).fetchone():
                raise WorkError('WORK_TASK_NOT_FOUND', 404)
            if conn.execute("SELECT COUNT(*) FROM work_sessions WHERE state <> 'ARCHIVED'").fetchone()[0] >= 64:
                raise WorkError('SESSION_LIMIT', 422)
            session_id = str(uuid4())
            authorization = {'consent': True, 'mode': command.mode, 'read_only': command.read_only,
                             'identity_digest': digest(selected['identity']), 'request_id': str(command.request_id),
                             'approved_at': timestamp}
            try:
                conn.execute('''INSERT INTO work_sessions(id,work_id,task_id,source_id,application_id,store_id,scope_id,provider,
                    binding_id,native_session_id,mode,read_only,identity_json,authorization_json,state,reason_code,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CONNECTING','SESSION_CONNECTION_QUEUED',?,?)''',
                    (session_id, work_id, task_id, selected['source_id'], command.application_id, command.store_id,
                     command.scope_id, selected['provider'], command.binding_id, native_id if command.mode == 'CONTINUE' else None,
                     command.mode, int(command.read_only), encoded(selected['identity']), encoded(authorization), timestamp, timestamp))
            except sqlite3.IntegrityError as exc:
                raise WorkError('SESSION_ALREADY_MANAGED') from exc
            conn.execute("INSERT OR IGNORE INTO protection_work_links VALUES (?,'scope',?,?,'USER_SELECTED_EXECUTION',?)",
                         (work_id, command.scope_id, task_id or '', timestamp))
            self.ledger(conn, session_id, EventType.SESSION_CREATED, 'MANAGED_INTENT', work_id=work_id,
                        scope_id=command.scope_id, agent_kind=selected['provider'], authorization_digest=digest(authorization))
            return {**self.work._bump(conn, work_id, timestamp), 'session_id': session_id,
                    'state': 'CONNECTING', 'session_revision': 1}
        return self.work._command('session-create', work_id, command, apply)

    def session(self, work_id, session_id):
        row = self.one('work_sessions', session_id, work_id)
        return self._session_public(row)

    @staticmethod
    def _session_public(row):
        row['scope_root'] = json.loads(row['identity_json'])['binding']['root']
        for key in ('identity_json', 'authorization_json'):
            row.pop(key)
        row['capabilities'] = json.loads(row.pop('capabilities_json'))
        row['read_only'] = bool(row['read_only'])
        row['session_id'] = row['id']
        return row

    def sessions(self, work_id, limit=50, offset=0):
        self.work._work(self.conn, work_id)
        cursor = self.conn.execute('SELECT * FROM work_sessions WHERE work_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                                   (work_id, limit + 1, offset))
        names = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        return {'items': [self._session_public(dict(zip(names, row))) for row in rows[:limit]], 'has_more': len(rows) > limit}

    def session_ready(self, session_id, native_id, generation, capabilities):
        session = self.one('work_sessions', session_id)
        with self.db.transaction() as conn:
            self.one('work_sessions', session_id)
            record_sessions_value = {'status': 'AVAILABLE', 'store_id': session['store_id'],
                     'provider': session['provider'], 'sessions': [{'session_id': native_id,
                     'provider': session['provider'], 'cwd': json.loads(session['identity_json'])['binding']['root'],
                     'metadata_source': 'MANAGED_PROTOCOL'}]}
            binding_id = record_sessions(self.db, session['source_id'], record_sessions_value)['sessions'][0]['binding_id']
            conn.execute("UPDATE work_sessions SET native_session_id=?,binding_id=?,generation=?,capabilities_json=?,state='READY',reason_code='SESSION_CONNECTED',revision=revision+1,updated_at=? WHERE id=? AND state <> 'ARCHIVED'",
                         (native_id, binding_id, generation, encoded(capabilities), now(), session_id))
            conn.execute("INSERT OR IGNORE INTO protection_work_links VALUES (?,'session',?,?,'MANAGED_PROTOCOL',?)",
                         (session['work_id'], binding_id, session['task_id'] or '', now()))
            self.ledger(conn, session_id, EventType.SESSION_ACTIVATED, 'CONNECTED', work_id=session['work_id'],
                        agent_kind=session['provider'], binding_id=binding_id, generation=generation)

    def session_state(self, session_id, state, reason):
        with self.db.transaction() as conn:
            conn.execute('UPDATE work_sessions SET state=?,reason_code=?,revision=revision+1,updated_at=? WHERE id=?',
                         (state, reason, now(), session_id))
            if state != 'READY':
                for pending_id, in conn.execute("SELECT id FROM work_pending_instructions WHERE session_id=? AND state='PENDING'", (session_id,)).fetchall():
                    self._pending_transition(conn, self._pending_row(pending_id), 'HELD', reason, now())

    def mutate_session(self, work_id, session_id, command, *, archive=False):
        def apply(conn, timestamp):
            row = self.one('work_sessions', session_id, work_id)
            if row['revision'] != command.expected_revision:
                raise WorkError('SESSION_REVISION_CONFLICT')
            if archive and conn.execute('SELECT 1 FROM work_runs WHERE session_id=? AND state IN (' + ','.join('?' * len(ACTIVE)) + ')', (session_id, *ACTIVE)).fetchone():
                raise WorkError('SESSION_BUSY')
            if archive:
                self._stop_pending_and_descendants(conn, session_id, '', timestamp)
            conn.execute('UPDATE work_sessions SET nickname=?,state=?,revision=revision+1,updated_at=? WHERE id=?',
                         (row['nickname'] if archive else command.nickname, 'ARCHIVED' if archive else row['state'], timestamp, session_id))
            return {'session_id': session_id, 'revision': row['revision'] + 1, 'state': 'ARCHIVED' if archive else row['state']}
        return self.work._command('session-archive' if archive else 'session-rename', [work_id, session_id], command, apply)

    def connect(self, work_id, session_id, command):
        previous = self.work.receipt('session-connect', [work_id, session_id], command)
        if previous is not None:
            return previous
        row = self.one('work_sessions', session_id, work_id)
        if row['state'] not in {'DISCONNECTED', 'UNAVAILABLE', 'UNCERTAIN'} or row['state'] == 'UNCERTAIN' and not row['native_session_id']:
            raise WorkError('SESSION_RECONCILIATION_REQUIRED')
        selected = self.validate_selection(row['application_id'], row['store_id'], row['scope_id'])
        old = json.loads(row['identity_json'])
        old['binding']['epoch'] = selected['identity']['binding']['epoch']
        if old != selected['identity']:
            raise WorkError('EXECUTION_IDENTITY_CHANGED')
        def apply(conn, timestamp):
            current = self.one('work_sessions', session_id, work_id)
            if current['revision'] != command.expected_revision or current['state'] != row['state']:
                raise WorkError('SESSION_REVISION_CONFLICT')
            authorization = {'consent': True, 'identity_digest': digest(selected['identity']),
                             'read_only': bool(row['read_only']), 'approved_at': timestamp, 'request_id': str(command.request_id)}
            conn.execute("UPDATE work_sessions SET state='CONNECTING',reason_code='SESSION_CONNECTION_QUEUED',mode=?,identity_json=?,authorization_json=?,revision=revision+1,updated_at=? WHERE id=?",
                         ('CONTINUE' if row['native_session_id'] else 'NEW', encoded(selected['identity']), encoded(authorization), timestamp, session_id))
            self.ledger(conn, session_id, EventType.USER_APPROVED, 'MANAGED_RECONNECT', work_id=work_id, authorization_digest=digest(authorization))
            return {'session_id': session_id, 'revision': row['revision'] + 1, 'state': 'CONNECTING'}
        return self.work._command('session-connect', [work_id, session_id], command, apply)

    def _contexts(self, work_id, refs):
        contexts = []
        versions = []
        for ref in refs:
            if ref.kind == 'artifact':
                value = self.work.artifact(work_id, ref.ref_id, ref.version)
                content = value['body']
            elif ref.kind == 'requirement':
                if ref.ref_id != work_id:
                    raise WorkError('CONTEXT_NOT_IN_WORK', 422)
                content = self.work.requirement(work_id, ref.version)['body']
            elif ref.kind == 'file':
                if not ref.version_id or not ref.file_id:
                    raise WorkError('CONTEXT_FILE_VERSION_REQUIRED', 422)
                if not self.conn.execute("SELECT 1 FROM protection_work_links WHERE work_id=? AND kind='scope' AND object_id=?", (work_id, ref.ref_id)).fetchone():
                    raise WorkError('CONTEXT_NOT_IN_WORK', 422)
                value = self.protection.file_content(ref.ref_id, ref.version_id, ref.file_id)
                try:
                    content = base64.b64decode(value['content_b64'], validate=True).decode('utf-8')
                except (ValueError, UnicodeError, KeyError) as exc:
                    raise WorkError('CONTEXT_FILE_NOT_TEXT', 422) from exc
                if '\x00' in content:
                    raise WorkError('CONTEXT_FILE_NOT_TEXT', 422)
                versions.append(ref.version_id)
            else:
                cursor = self.conn.execute('SELECT body FROM work_reviews WHERE id=? AND work_id=?', (ref.ref_id, work_id))
                review = cursor.fetchone()
                if not review:
                    raise WorkError('CONTEXT_NOT_IN_WORK', 422)
                content = review[0]
            contexts.append({'ref': ref.model_dump(mode='json'), 'body': content})
        return contexts, versions

    def start_run(self, work_id, session_id, command, *, _pending_id=None):
        previous = self.work.receipt('run-start', [work_id, session_id], command)
        if previous is not None:
            return previous
        session = self.one('work_sessions', session_id, work_id)
        if session['state'] != 'READY' or not json.loads(session['capabilities_json']).get('send'):
            raise WorkError('SESSION_NOT_READY')
        self.validate_selection(session['application_id'], session['store_id'], session['scope_id'], expected=json.loads(session['identity_json']))
        if _pending_id is not None:
            self._assert_pending_promotion(_pending_id, session, command)
        elif self.conn.execute("SELECT 1 FROM work_pending_instructions WHERE session_id=? AND state IN ('PENDING','HELD')", (session_id,)).fetchone():
            raise WorkError('PENDING_INSTRUCTION_SLOT_OCCUPIED')
        with self.protection.lock:
            baseline = self.protection.capture(session['scope_id'], force=True)
            scope = self.protection.scope(session['scope_id'])
            if baseline.get('status') not in {'RECORDED', 'UNCHANGED'} or not scope['latest_id'] or scope['status'] != 'PROTECTED':
                raise WorkError('EXECUTION_BASELINE_UNAVAILABLE')
            contexts, context_versions = self._contexts(work_id, command.context_refs)

            def apply(conn, timestamp):
                work = self.work._work(conn, work_id)
                row = self.one('work_sessions', session_id, work_id)
                if row['revision'] != command.expected_revision or row['state'] != 'READY':
                    raise WorkError('SESSION_REVISION_CONFLICT')
                if work['requirement_version'] != command.requirement_version:
                    raise WorkError('WORK_REQUIREMENT_CHANGED')
                if _pending_id is not None:
                    self._assert_pending_promotion(_pending_id, row, command)
                elif conn.execute("SELECT 1 FROM work_pending_instructions WHERE session_id=? AND state IN ('PENDING','HELD')", (session_id,)).fetchone():
                    raise WorkError('PENDING_INSTRUCTION_SLOT_OCCUPIED')
                if conn.execute("SELECT 1 FROM protection_operations WHERE scope_id=? AND kind='RESTORE' AND state IN ('QUEUED','RUNNING','COMPENSATING','NEEDS_ATTENTION')", (row['scope_id'],)).fetchone():
                    raise WorkError('PROTECTION_RECOVERY_BUSY')
                requirement = conn.execute('SELECT body FROM work_requirements WHERE work_id=? AND version=?', (work_id, command.requirement_version)).fetchone()[0]
                task = None
                if row['task_id']:
                    cursor = conn.execute('SELECT id,title,body,assignee,dependencies_json,updated_at FROM work_tasks WHERE work_id=? AND id=?', (work_id, row['task_id']))
                    saved_task = cursor.fetchone()
                    if not saved_task:
                        raise WorkError('WORK_TASK_NOT_FOUND', 404)
                    task = dict(zip((col[0] for col in cursor.description), saved_task))
                    task['depends_on'] = json.loads(task.pop('dependencies_json'))
                    task['dependencies'] = [dict(zip(('id', 'title', 'body', 'updated_at'), r)) for r in conn.execute(
                        'SELECT id,title,body,updated_at FROM work_tasks WHERE work_id=? AND id IN (SELECT value FROM json_each(?))',
                        (work_id, encoded(task['depends_on'])))]
                snapshot = {'input': command.input, 'requirement': requirement, 'task': task,
                            'context': contexts, 'identity': json.loads(row['identity_json']),
                            'authorization': json.loads(row['authorization_json'])}
                reject_sensitive(snapshot)
                if len(encoded(snapshot).encode()) > MAX_EVENT:
                    raise WorkError('EXECUTION_CONTEXT_TOO_LARGE', 422)
                def reserved_usage():
                    return conn.execute('SELECT COALESCE(SUM(journal_bytes+CASE WHEN state IN (' + ','.join('?' * len(ACTIVE)) + ') THEN MAX(0,?-journal_bytes) ELSE 0 END),0) FROM work_runs', (*ACTIVE, RUN_BUDGET)).fetchone()[0]
                if reserved_usage() + RUN_BUDGET > GLOBAL_BUDGET:
                    self.collect()
                if reserved_usage() + RUN_BUDGET > GLOBAL_BUDGET:
                    raise WorkError('EXECUTION_JOURNAL_FULL', 503)
                run_id = str(uuid4())
                try:
                    conn.execute('''INSERT INTO work_runs(id,work_id,session_id,scope_id,requirement_version,input_json,input_digest,input_summary,
                        baseline_id,generation,state,reason_code,journal_bytes,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,'QUEUED','RUN_QUEUED',?,?,?)''',
                        (run_id, work_id, session_id, row['scope_id'], command.requirement_version, encoded(snapshot),
                         digest(snapshot), command.input[:200], scope['latest_id'], row['generation'], len(encoded(snapshot).encode()) + 1024, timestamp, timestamp))
                except sqlite3.IntegrityError as exc:
                    raise WorkError('EXECUTION_WRITER_BUSY') from exc
                for version_id in [scope['latest_id'], *context_versions]:
                    conn.execute("INSERT OR IGNORE INTO protection_version_dependencies VALUES ('run',?,?)", (run_id, version_id))
                self.append(conn, run_id, 'INPUT', {'input': command.input, 'input_digest': digest(snapshot)})
                self.ledger(conn, run_id, EventType.USER_APPROVED, 'RUN_AUTHORIZED', work_id=work_id,
                            session_id=session_id, scope_id=row['scope_id'], input_digest=digest(snapshot), baseline_id=scope['latest_id'])
                if _pending_id is not None:
                    if conn.execute("UPDATE work_pending_instructions SET state='PROMOTED',reason_code='PENDING_PROMOTED',promoted_run_id=?,revision=revision+1,updated_at=? WHERE id=? AND state='PENDING'", (run_id, timestamp, _pending_id)).rowcount != 1:
                        raise WorkError('PENDING_INSTRUCTION_NOT_ACTIVE')
                    conn.execute("DELETE FROM protection_version_dependencies WHERE owner_kind='pending' AND owner_id=?", (_pending_id,))
                conn.execute('UPDATE work_sessions SET revision=revision+1,updated_at=? WHERE id=?', (timestamp, session_id))
                return {'run_id': run_id, 'session_id': session_id, 'revision': row['revision'] + 1, 'state': 'QUEUED', 'dispatch_state': 'NOT_SENT'}
            return self.work._command('run-start', [work_id, session_id], command, apply)

    def append(self, conn, run_id, event_type, payload, *, action_ref=None, response_to=None, observed_at=None):
        payload = safe(payload)
        value = encoded(payload)
        size = len(value.encode()) + 256
        row = self.one('work_runs', run_id)
        if size > MAX_EVENT:
            raise WorkError('EXECUTION_EVENT_TOO_LARGE', 422)
        if event_type in {'TEXT', 'TOOL', 'HISTORY'} and row['journal_bytes'] + size > RUN_BUDGET - CONTROL_RESERVE:
            if row['output_truncated']:
                return None
            event_type, payload = 'OUTPUT_TRUNCATED', {'reason_code': 'EXECUTION_OUTPUT_LIMIT'}
            value, size = encoded(payload), 512
            conn.execute('UPDATE work_runs SET output_truncated=1 WHERE id=?', (run_id,))
        if row['journal_bytes'] + size > RUN_BUDGET:
            raise WorkError('EXECUTION_CONTROL_JOURNAL_FULL', 503)
        seq = row['last_seq'] + 1
        timestamp = now()
        conn.execute('INSERT INTO work_run_events VALUES (?,?,?,?,?,?,?,?,?)',
                     (run_id, seq, event_type, value, digest(payload), observed_at or timestamp, timestamp, action_ref, response_to))
        conn.execute('UPDATE work_runs SET last_seq=?,journal_bytes=journal_bytes+?,updated_at=? WHERE id=?', (seq, size, timestamp, run_id))
        return seq

    def events(self, work_id, run_id, after=0, limit=100):
        run = self.one('work_runs', run_id, work_id)
        rows = self.conn.execute('SELECT seq,type,payload_json,observed_at,recorded_at,action_ref,response_to FROM work_run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (run_id, after, limit + 1)).fetchall()
        return {'items': [{'seq': r[0], 'type': r[1], 'payload': json.loads(r[2]), 'observed_at': r[3],
                           'recorded_at': r[4], 'action_ref': r[5], 'response_to': r[6]} for r in rows[:limit]],
                'has_more': len(rows) > limit, 'last_seq': run['last_seq'], 'history_status': run['history_status'],
                'state': run['state'], 'output_truncated': bool(run['output_truncated'])}

    def execution_summary(self, work_id, limit=20):
        """Work-wide attention without loading prompts, output or provider metadata."""
        conn = self.conn
        work = self.work._work(conn, work_id)
        candidates = '''WITH candidates AS (
            SELECT r.id,r.session_id,r.state,r.requirement_version,r.updated_at,
                s.provider,s.task_id
            FROM work_runs r JOIN work_sessions s ON s.id=r.session_id
            WHERE r.work_id=? AND (r.state IN (''' + ','.join('?' * len(ACTIVE)) + ''')
                OR (r.state IN ('FAILED','INTERRUPTED') AND NOT EXISTS (
                    SELECT 1 FROM work_runs newer WHERE newer.session_id=r.session_id
                    AND (newer.created_at>r.created_at OR
                        (newer.created_at=r.created_at AND newer.id>r.id)))))
        ) '''
        params = (work_id, *ACTIVE)
        counts = dict(conn.execute(candidates + 'SELECT state,COUNT(*) FROM candidates GROUP BY state', params))
        cursor = conn.execute(candidates + '''SELECT * FROM candidates ORDER BY
            CASE state WHEN 'WAITING_INPUT' THEN 0 WHEN 'WAITING_APPROVAL' THEN 1
                WHEN 'UNCERTAIN' THEN 2 WHEN 'FAILED' THEN 3 WHEN 'INTERRUPTED' THEN 3 ELSE 4 END,
            updated_at DESC,id LIMIT ?''', (*params, limit))
        columns = [column[0] for column in cursor.description]
        items = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return {'work_id': work_id, 'requirement_version': work['requirement_version'],
                'work_revision': work['revision'], 'observed_at': now(),
                'counts': counts, 'items': items, 'has_more': sum(counts.values()) > len(items)}

    def run(self, work_id, session_id, run_id, view='full'):
        if view == 'summary':
            cursor = self.conn.execute('SELECT ' + RUN_SUMMARY + ' FROM work_runs WHERE work_id=? AND id=?', (work_id, run_id))
            value = cursor.fetchone()
            if value is None:
                raise WorkError('RUN_NOT_FOUND', 404)
            row = dict(zip((col[0] for col in cursor.description), value))
        else:
            row = self.one('work_runs', run_id, work_id)
        if session_id is not None and row['session_id'] != session_id:
            raise WorkError('RUN_NOT_FOUND', 404)
        row = self._run_public(row, view)
        pending = self.conn.execute("SELECT e.seq,e.type,e.payload_json,e.action_ref FROM work_run_events e WHERE e.run_id=? AND e.type IN ('APPROVAL','QUESTION') AND NOT EXISTS (SELECT 1 FROM work_run_events r WHERE r.run_id=e.run_id AND r.response_to=e.seq) ORDER BY e.seq", (run_id,)).fetchall()
        row['pending_actions'] = [{'event_seq': r[0], 'type': r[1], 'payload': json.loads(r[2]), 'action_ref': r[3]} for r in pending] if row['state'] in ACTIVE else []
        return row

    @staticmethod
    def _run_public(row, view):
        row['run_id'] = row['id']
        row['result'] = json.loads(row.pop('result_json'))
        row['file_result'] = json.loads(row.pop('file_result_json'))
        snapshot = row.pop('input_json', '{}')
        if view == 'full' and row['history_status'] != 'SUMMARY_ONLY':
            row['input'] = json.loads(snapshot)
        row['pending_actions'] = []
        return row

    def runs(self, work_id, session_id, limit=50, offset=0, view='summary'):
        self.one('work_sessions', session_id, work_id)
        cursor = self.conn.execute('SELECT ' + ('*' if view == 'full' else RUN_SUMMARY) + ' FROM work_runs WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (session_id, limit + 1, offset))
        names = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        return {'items': [self._run_public(dict(zip(names, r)), view) for r in rows[:limit]], 'has_more': len(rows) > limit}

    def cancel(self, work_id, session_id, run_id, command):
        actual_session = self.one('work_runs', run_id, work_id)['session_id']
        def apply(conn, timestamp):
            row = self.run(work_id, session_id, run_id, 'summary')
            if command.expected_revision is not None and row['revision'] != command.expected_revision:
                raise WorkError('RUN_REVISION_CONFLICT')
            self._stop_pending_and_descendants(conn, actual_session, run_id, timestamp)
            if row['state'] in TERMINAL or row['state'] == 'FINALIZING':
                return {'run_id': run_id, 'state': row['state']}
            if row['state'] == 'UNCERTAIN':
                raise WorkError('RUN_RECONCILIATION_REQUIRED')
            self.append(conn, run_id, 'CANCEL_REQUESTED', {})
            conn.execute("UPDATE work_runs SET state='CANCELLING',revision=revision+1,reason_code='RUN_CANCEL_REQUESTED' WHERE id=?", (run_id,))
            return {'run_id': run_id, 'state': 'CANCELLING'}
        return self.work._command('run-cancel', [work_id, actual_session, run_id], command, apply)

    def response(self, work_id, session_id, run_id, command):
        session_id = session_id or self.one('work_runs', run_id, work_id)['session_id']
        previous = self.work.receipt('run-response', [work_id, session_id, run_id], command)
        if previous is not None:
            return previous
        session = self.one('work_sessions', session_id, work_id)
        self.validate_selection(session['application_id'], session['store_id'], session['scope_id'], expected=json.loads(session['identity_json']))
        reject_sensitive(command.response)
        if len(encoded(command.response).encode()) > 65536:
            raise WorkError('EXECUTION_RESPONSE_TOO_LARGE', 422)
        def apply(conn, timestamp):
            row = self.run(work_id, session_id, run_id, 'summary')
            if row['state'] not in {'WAITING_APPROVAL', 'WAITING_INPUT'}:
                raise WorkError('RUN_ACTION_NOT_CURRENT')
            action = conn.execute("SELECT payload_json,type,action_ref FROM work_run_events WHERE run_id=? AND seq=? AND type IN ('APPROVAL','QUESTION')", (run_id, command.event_seq)).fetchone()
            if not action or action[2] != command.action_ref or json.loads(action[0]).get('generation') != row['generation']:
                raise WorkError('RUN_ACTION_MISMATCH')
            facts = json.loads(action[0])
            if action[1] == 'APPROVAL' and (set(command.response) != {'decision'} or command.response['decision'] not in facts.get('allowed_decisions', [])):
                raise WorkError('RUN_APPROVAL_DECISION_INVALID', 422)
            if action[1] == 'QUESTION' and (set(command.response) != {'answers'} or any(q.get('secret') for q in facts.get('questions', []))):
                raise WorkError('RUN_QUESTION_RESPONSE_INVALID', 422)
            if conn.execute('SELECT 1 FROM work_run_events WHERE run_id=? AND response_to=?', (run_id, command.event_seq)).fetchone():
                raise WorkError('RUN_ACTION_CONSUMED')
            self.append(conn, run_id, 'RESPONSE_INTENT', {'response': command.response, 'action_ref': command.action_ref}, response_to=command.event_seq)
            self.ledger(conn, run_id, EventType.USER_APPROVED if command.response.get('decision') == 'ALLOW_ONCE' or action[1] == 'QUESTION' else EventType.USER_REJECTED,
                        'PROVIDER_RESPONSE_INTENT', action_ref=command.action_ref, response_digest=digest(command.response))
            return {'run_id': run_id, 'state': row['state'], 'action_ref': command.action_ref, 'event_seq': command.event_seq}
        return self.work._command('run-response', [work_id, session_id, run_id], command, apply)

    def collect(self):
        conn = self.conn
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        candidates = conn.execute('''SELECT id FROM work_runs
            WHERE completed_at<? AND history_status <> 'SUMMARY_ONLY'
            AND state IN ('COMPLETED','FAILED','CANCELLED','INTERRUPTED')
            AND id NOT IN (SELECT run_id FROM work_submissions WHERE run_id IS NOT NULL
                UNION SELECT source_run_id FROM work_artifact_versions WHERE source_run_id IS NOT NULL)''',
            (cutoff,)).fetchall()
        for run_id, in candidates:
            conn.execute("DELETE FROM work_run_events WHERE run_id=? AND type IN ('INPUT','TEXT','TOOL','HISTORY')", (run_id,))
            conn.execute("UPDATE work_runs SET history_status='SUMMARY_ONLY',input_json='{}',journal_bytes=1024+(SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB))+256),0) FROM work_run_events WHERE run_id=?) WHERE id=?", (run_id, run_id))
            conn.execute("DELETE FROM protection_version_dependencies WHERE owner_kind='run' AND owner_id=?", (run_id,))
