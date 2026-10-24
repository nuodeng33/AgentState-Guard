"""One durable explicit next instruction; promotion grants no new authority."""

import json
import sqlite3
from uuid import UUID, uuid4

from agentguard.evidence.models import EventType
from agentguard.work.service import WorkError

from .models import ContextRef, StartRun


class PendingInstructionMixin:
    def _pending_row(self, pending_id, work_id=None, session_id=None):
        cursor = self.conn.execute('SELECT * FROM work_pending_instructions WHERE id=?', (pending_id,))
        row = cursor.fetchone()
        if row is None:
            raise WorkError('PENDING_INSTRUCTION_NOT_FOUND', 404)
        row = dict(zip((column[0] for column in cursor.description), row))
        if work_id is not None and row['work_id'] != work_id or session_id is not None and row['session_id'] != session_id:
            raise WorkError('PENDING_INSTRUCTION_NOT_FOUND', 404)
        return row

    def pending_instruction(self, work_id, session_id, request_id=None):
        self.one('work_sessions', session_id, work_id)
        if request_id is not None:
            row = self.conn.execute('SELECT id FROM work_pending_instructions WHERE work_id=? AND session_id=? AND request_id=?',
                                    (work_id, session_id, str(request_id))).fetchone()
        else:
            row = self.conn.execute("SELECT id FROM work_pending_instructions WHERE work_id=? AND session_id=? ORDER BY CASE WHEN state IN ('PENDING','HELD') THEN 0 ELSE 1 END,created_at DESC,rowid DESC LIMIT 1", (work_id, session_id)).fetchone()
        if row is None:
            return {'item': None}
        result = self._pending_row(row[0])
        snapshot = json.loads(result.pop('snapshot_json'))
        result.pop('snapshot_digest')
        result.pop('promotion_request_id')
        result.update(input=snapshot['input'], context_refs=snapshot['context_refs'])
        return {'item': result}

    def _pending_semantics(self, session, requirement_version, refs):
        """Transaction-safe metadata comparison, including exact resolved versions."""
        work_id = session['work_id']
        work = self.work._work(self.conn, work_id)
        if work['requirement_version'] != requirement_version:
            raise WorkError('WORK_REQUIREMENT_CHANGED')
        requirement = self.conn.execute('SELECT body FROM work_requirements WHERE work_id=? AND version=?',
                                        (work_id, requirement_version)).fetchone()
        task = None
        if session['task_id']:
            cursor = self.conn.execute('SELECT id,title,body,assignee,dependencies_json,updated_at FROM work_tasks WHERE work_id=? AND id=?', (work_id, session['task_id']))
            row = cursor.fetchone()
            if row is None:
                raise WorkError('WORK_TASK_NOT_FOUND', 404)
            task = dict(zip((c[0] for c in cursor.description), row))
            task['dependencies'] = [list(r) for r in self.conn.execute('SELECT id,title,body,assignee,dependencies_json,updated_at FROM work_tasks WHERE work_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id', (work_id, task['dependencies_json']))]
        versions = []
        resolved = []
        for value in refs:
            ref = dict(value)
            if ref['kind'] == 'artifact':
                row = self.conn.execute('SELECT latest_version FROM work_artifacts WHERE work_id=? AND id=?', (work_id, ref['ref_id'])).fetchone()
                if row is None:
                    raise WorkError('WORK_ARTIFACT_NOT_FOUND', 404)
                ref['version'] = ref.get('version') or row[0]
                item = self.conn.execute('SELECT title,body,requirement_version FROM work_artifact_versions WHERE artifact_id=? AND version=?', (ref['ref_id'], ref['version'])).fetchone()
                if item is None:
                    raise WorkError('WORK_ARTIFACT_NOT_FOUND', 404)
                versions.append([ref, list(item)])
            elif ref['kind'] == 'requirement':
                if ref['ref_id'] != work_id:
                    raise WorkError('CONTEXT_NOT_IN_WORK', 422)
                ref['version'] = ref.get('version') or work['requirement_version']
                item = self.conn.execute('SELECT body FROM work_requirements WHERE work_id=? AND version=?', (work_id, ref['version'])).fetchone()
                if item is None:
                    raise WorkError('WORK_REQUIREMENT_NOT_FOUND', 404)
                versions.append([ref, item[0]])
            elif ref['kind'] == 'review':
                item = self.conn.execute('SELECT body FROM work_reviews WHERE id=? AND work_id=?', (ref['ref_id'], work_id)).fetchone()
                if item is None:
                    raise WorkError('CONTEXT_NOT_IN_WORK', 422)
                versions.append([ref, item[0]])
            else:
                item = self.conn.execute('SELECT digest,available FROM protection_versions WHERE id=? AND scope_id=?', (ref.get('version_id'), ref['ref_id'])).fetchone()
                if item is None or not item[1]:
                    raise WorkError('CONTEXT_FILE_VERSION_UNAVAILABLE')
                versions.append([ref, list(item)])
            resolved.append(ref)
        metadata = {key: session[key] for key in ('id', 'source_id', 'application_id', 'store_id', 'scope_id', 'provider', 'task_id', 'generation', 'native_session_id', 'read_only', 'identity_json', 'authorization_json', 'capabilities_json')}
        return {'session': metadata, 'requirement_version': requirement_version,
                'requirement': requirement[0], 'task': task, 'context_versions': versions}, resolved

    def enqueue_pending(self, work_id, session_id, command):
        from .service import ACTIVE, MAX_EVENT, digest, encoded, reject_sensitive
        previous = self.work.receipt('pending-enqueue', [work_id, session_id], command)
        if previous is not None:
            return previous
        session = self.one('work_sessions', session_id, work_id)
        if session['state'] != 'READY' or not json.loads(session['capabilities_json']).get('send'):
            raise WorkError('SESSION_NOT_READY')
        self.validate_selection(session['application_id'], session['store_id'], session['scope_id'], expected=json.loads(session['identity_json']))
        refs = [ref.model_dump(mode='json', exclude_none=True) for ref in command.context_refs]
        semantics, resolved = self._pending_semantics(session, command.requirement_version, refs)
        contexts, pinned = self._contexts(work_id, [ContextRef.model_validate(ref) for ref in resolved])
        snapshot = {'input': command.input, 'context_refs': refs, 'resolved_context_refs': resolved,
                    'contexts': contexts, 'semantics': semantics, 'context_version_ids': pinned}
        reject_sensitive(snapshot)
        if len(encoded(snapshot).encode()) > MAX_EVENT:
            raise WorkError('EXECUTION_CONTEXT_TOO_LARGE', 422)

        def apply(conn, timestamp):
            current = self.one('work_sessions', session_id, work_id)
            if current['revision'] != command.expected_revision or current['state'] != 'READY':
                raise WorkError('SESSION_REVISION_CONFLICT')
            latest, _ = self._pending_semantics(current, command.requirement_version, refs)
            if latest != semantics:
                raise WorkError('PENDING_CONTEXT_CHANGED')
            predecessor = self.one('work_runs', str(command.predecessor_run_id), work_id)
            if predecessor['session_id'] != session_id or predecessor['generation'] != current['generation'] or predecessor['state'] not in set(ACTIVE) - {'CANCELLING', 'UNCERTAIN'}:
                raise WorkError('PENDING_PREDECESSOR_NOT_ACTIVE')
            if conn.execute("SELECT 1 FROM work_pending_instructions WHERE session_id=? AND state IN ('PENDING','HELD')", (session_id,)).fetchone():
                raise WorkError('PENDING_INSTRUCTION_SLOT_OCCUPIED')
            pending_id = str(uuid4())
            try:
                conn.execute('''INSERT INTO work_pending_instructions(id,work_id,session_id,predecessor_run_id,request_id,promotion_request_id,
                    requirement_version,snapshot_json,snapshot_digest,state,reason_code,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,'PENDING','PENDING_PREDECESSOR_RUNNING',?,?)''',
                    (pending_id, work_id, session_id, predecessor['id'], str(command.request_id), str(uuid4()), command.requirement_version,
                     encoded(snapshot), digest(snapshot), timestamp, timestamp))
            except sqlite3.IntegrityError as error:
                raise WorkError('PENDING_INSTRUCTION_SLOT_OCCUPIED') from error
            for version in pinned:
                conn.execute("INSERT OR IGNORE INTO protection_version_dependencies VALUES('pending',?,?)", (pending_id, version))
            self.ledger(conn, pending_id, EventType.USER_APPROVED, 'PENDING_INSTRUCTION_AUTHORIZED',
                        work_id=work_id, session_id=session_id, predecessor_run_id=predecessor['id'], input_digest=digest(snapshot))
            return {'pending_id': pending_id, 'session_id': session_id, 'state': 'PENDING', 'revision': 1,
                    'predecessor_run_id': predecessor['id'], 'request_id': str(command.request_id)}
        return self.work._command('pending-enqueue', [work_id, session_id], command, apply)

    def _pending_transition(self, conn, row, state, reason, timestamp):
        changed = conn.execute('UPDATE work_pending_instructions SET state=?,reason_code=?,revision=revision+1,updated_at=? WHERE id=? AND state IN (\'PENDING\',\'HELD\')', (state, reason, timestamp, row['id'])).rowcount
        if changed:
            self.ledger(conn, row['id'], EventType.USER_REJECTED if state == 'CANCELLED' else EventType.SESSION_FAILED,
                        state, reason_code=reason, previous_reason=row['reason_code'], work_id=row['work_id'], session_id=row['session_id'])
            if state == 'CANCELLED':
                conn.execute("DELETE FROM protection_version_dependencies WHERE owner_kind='pending' AND owner_id=?", (row['id'],))

    def cancel_pending(self, work_id, session_id, pending_id, command):
        def apply(conn, timestamp):
            self.one('work_sessions', session_id, work_id)
            row = self._pending_row(pending_id, work_id, session_id)
            if row['revision'] != command.expected_revision:
                raise WorkError('PENDING_REVISION_CONFLICT')
            if row['state'] not in {'PENDING', 'HELD'}:
                raise WorkError('PENDING_INSTRUCTION_NOT_ACTIVE')
            self._pending_transition(conn, row, 'CANCELLED', 'PENDING_CANCELLED_BY_USER', timestamp)
            return {'pending_id': pending_id, 'session_id': session_id, 'state': 'CANCELLED', 'revision': row['revision'] + 1,
                    'predecessor_run_id': row['predecessor_run_id'], 'request_id': str(command.request_id)}
        return self.work._command('pending-cancel', [work_id, session_id, pending_id], command, apply)

    def hold_pending(self, session_id, reason):
        from .service import now
        with self.db.transaction() as conn:
            for pending_id, in conn.execute("SELECT id FROM work_pending_instructions WHERE session_id=? AND state='PENDING'", (session_id,)).fetchall():
                self._pending_transition(conn, self._pending_row(pending_id), 'HELD', reason, now())

    def _assert_pending_promotion(self, pending_id, session, command):
        from .service import digest
        row = self._pending_row(pending_id, session['work_id'], session['id'])
        if row['state'] != 'PENDING' or str(command.request_id) != row['promotion_request_id']:
            raise WorkError('PENDING_INSTRUCTION_NOT_ACTIVE')
        snapshot = json.loads(row['snapshot_json'])
        if digest(snapshot) != row['snapshot_digest'] or command.input != snapshot['input']:
            raise WorkError('PENDING_INPUT_CHANGED')
        semantics, resolved = self._pending_semantics(session, row['requirement_version'], snapshot['context_refs'])
        if semantics != snapshot['semantics'] or resolved != snapshot['resolved_context_refs']:
            raise WorkError('PENDING_CONTEXT_CHANGED')
        predecessor = self.one('work_runs', row['predecessor_run_id'], row['work_id'])
        file_result = json.loads(predecessor['file_result_json'])
        if (predecessor['session_id'] != session['id'] or predecessor['state'] != 'COMPLETED'
                or predecessor['dispatch_state'] != 'ACCEPTED'
                or json.loads(predecessor['result_json']).get('status') != 'completed'
                or file_result.get('status') != 'AVAILABLE'):
            raise WorkError('PENDING_PREDECESSOR_NOT_COMPLETED')
        event = self.conn.execute("SELECT payload_json,payload_digest FROM work_run_events WHERE run_id=? AND type='RUN_COMPLETED' ORDER BY seq DESC LIMIT 1", (predecessor['id'],)).fetchone()
        if (event is None or digest(json.loads(event[0])) != event[1]
                or json.loads(event[0]).get('state') != 'COMPLETED'
                or json.loads(event[0]).get('file_result') != file_result):
            raise WorkError('PENDING_FINALIZATION_UNVERIFIED')
        version = self.conn.execute('SELECT available FROM protection_versions WHERE id=? AND scope_id=?',
                                    (file_result.get('version_id'), predecessor['scope_id'])).fetchone()
        if version is None or not version[0]:
            raise WorkError('PENDING_FINALIZATION_VERSION_UNAVAILABLE')
        return row

    def promote_pending(self, pending_id):
        from .service import TERMINAL
        row = self._pending_row(pending_id)
        if row['state'] != 'PENDING':
            return None
        predecessor = self.one('work_runs', row['predecessor_run_id'], row['work_id'])
        if predecessor['state'] not in TERMINAL and predecessor['state'] != 'UNCERTAIN':
            return None
        try:
            session = self.one('work_sessions', row['session_id'], row['work_id'])
            snapshot = json.loads(row['snapshot_json'])
            command = StartRun(request_id=UUID(row['promotion_request_id']), expected_revision=session['revision'],
                requirement_version=row['requirement_version'], input=snapshot['input'],
                context_refs=snapshot['resolved_context_refs'])
            self._assert_pending_promotion(pending_id, session, command)
            return self.start_run(row['work_id'], row['session_id'], command, _pending_id=pending_id)
        except (WorkError, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.hold_pending(row['session_id'], getattr(error, 'code', 'PENDING_PROMOTION_UNAVAILABLE'))
            return None

    def _stop_pending_and_descendants(self, conn, session_id, predecessor_id, timestamp):
        """Stop wins even when promotion committed its child immediately before it."""
        from .service import ACTIVE
        for pending_id, in conn.execute("SELECT id FROM work_pending_instructions WHERE session_id=? AND state IN ('PENDING','HELD')", (session_id,)).fetchall():
            self._pending_transition(conn, self._pending_row(pending_id), 'CANCELLED', 'PENDING_CANCELLED_BY_STOP', timestamp)
        children = conn.execute('''WITH RECURSIVE descendants(id) AS (
            SELECT promoted_run_id FROM work_pending_instructions WHERE predecessor_run_id=? AND session_id=? AND state='PROMOTED'
            UNION SELECT p.promoted_run_id FROM work_pending_instructions p JOIN descendants d ON p.predecessor_run_id=d.id
                WHERE p.session_id=? AND p.state='PROMOTED') SELECT id FROM descendants''', (predecessor_id, session_id, session_id)).fetchall()
        for child_id, in children:
            child = self.one('work_runs', child_id)
            if child['state'] not in ACTIVE or child['state'] == 'FINALIZING':
                continue
            if child['state'] == 'UNCERTAIN':
                continue
            self.append(conn, child_id, 'CANCEL_REQUESTED', {'reason_code': 'PREDECESSOR_STOP_REQUESTED'})
            state = 'CANCELLED' if child['dispatch_state'] == 'NOT_SENT' else 'CANCELLING'
            conn.execute('UPDATE work_runs SET state=?,reason_code=?,revision=revision+1,completed_at=? WHERE id=?',
                         (state, 'PREDECESSOR_STOP_REQUESTED', timestamp if state == 'CANCELLED' else None, child_id))
