"""Explicit output promotion, fixed delivery versions and human review decisions."""

import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentguard.evidence.models import EventType
from agentguard.execution.service import TERMINAL, digest, encoded, reject_sensitive
from agentguard.work.models import Command
from agentguard.work.service import WorkError


class SaveRunArtifact(Command):
    expected_revision: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=100000)
    event_seqs: list[int] = Field(default_factory=list, max_length=1000)


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: UUID
    version: int = Field(strict=True, ge=1)


class FileRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope_id: str = Field(min_length=1, max_length=96)
    version_id: str = Field(min_length=1, max_length=96)


class CreateSubmission(Command):
    expected_revision: int = Field(strict=True, ge=1)
    requirement_version: int = Field(strict=True, ge=1)
    run_id: UUID | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list, max_length=40)
    file_refs: list[FileRef] = Field(default_factory=list, max_length=40)
    responsibility: str = Field(min_length=1, max_length=120)


class ReviewSubmission(Command):
    expected_revision: int = Field(strict=True, ge=1)
    decision: Literal['CHANGES_REQUESTED', 'APPROVED']
    body: str = Field(min_length=1, max_length=65536)
    reviewer: str = Field(min_length=1, max_length=120)


class AcceptSubmission(Command):
    expected_revision: int = Field(strict=True, ge=1)
    decision: Literal['ACCEPTED', 'REJECTED']
    basis: str = Field(min_length=1, max_length=65536)
    owner: str = Field(min_length=1, max_length=120)


class DeliveryService:
    def __init__(self, execution):
        self.execution = execution
        self.work = execution.work
        self.conn, self.db = execution.conn, execution.db

    def save_output(self, work_id, session_id, run_id, command):
        def apply(conn, timestamp):
            self.work._work(conn, work_id, command.expected_revision)
            run = self.execution.run(work_id, session_id, run_id, 'summary')
            if run['state'] not in TERMINAL or run['history_status'] == 'SUMMARY_ONLY':
                raise WorkError('RUN_OUTPUT_UNAVAILABLE')
            selected = command.event_seqs
            if len(selected) != len(set(selected)) or any(type(seq) is not int or seq < 1 for seq in selected):
                raise WorkError('RUN_OUTPUT_SELECTION_INVALID', 422)
            events = conn.execute("SELECT seq,payload_json,payload_digest FROM work_run_events WHERE run_id=? AND type='TEXT'" +
                (' AND seq IN (SELECT value FROM json_each(?))' if selected else '') + ' ORDER BY seq',
                (run_id, encoded(selected)) if selected else (run_id,)).fetchall()
            if not events or selected and len(events) != len(selected):
                raise WorkError('RUN_OUTPUT_UNAVAILABLE')
            body = command.body if command.body is not None else ''.join(json.loads(event[1])['text'] for event in events)
            if not body or len(body.encode()) > 100000:
                raise WorkError('RUN_ARTIFACT_TOO_LARGE', 422)
            reject_sensitive(body)
            provenance = [{'seq': e[0], 'digest': e[2]} for e in events]
            artifact_id, version = self.work._store_artifact(conn, work_id, None, command.title, body,
                run['requirement_version'], timestamp, source='USER_EDITED_RUN_OUTPUT' if command.body is not None else 'AGENT_RUN',
                source_run_id=run_id, source_events=provenance)
            return {**self.work._bump(conn, work_id, timestamp), 'artifact_id': artifact_id, 'version': version, 'run_id': run_id}
        return self.work._command('run-artifact', [work_id, session_id, run_id], command, apply)

    def submit(self, work_id, command):
        def apply(conn, timestamp):
            work = self.work._work(conn, work_id, command.expected_revision)
            if work['requirement_version'] != command.requirement_version:
                raise WorkError('WORK_REQUIREMENT_CHANGED')
            if not command.artifact_refs and not command.file_refs:
                raise WorkError('SUBMISSION_EMPTY', 422)
            run_id = str(command.run_id) if command.run_id else None
            if run_id:
                run = self.execution.one('work_runs', run_id, work_id)
                if run['state'] not in TERMINAL or run['requirement_version'] != command.requirement_version:
                    raise WorkError('SUBMISSION_RUN_MISMATCH', 422)
            artifacts, files = [], []
            for ref in command.artifact_refs:
                artifact = conn.execute('''SELECT v.requirement_version FROM work_artifact_versions v JOIN work_artifacts a
                    ON a.id=v.artifact_id WHERE a.work_id=? AND a.id=? AND v.version=?''',
                    (work_id, str(ref.artifact_id), ref.version)).fetchone()
                if not artifact:
                    raise WorkError('SUBMISSION_ARTIFACT_NOT_FOUND', 404)
                if artifact[0] != command.requirement_version:
                    raise WorkError('SUBMISSION_REQUIREMENT_MISMATCH')
                artifacts.append(ref.model_dump(mode='json'))
            for ref in command.file_refs:
                if not conn.execute("SELECT 1 FROM protection_work_links WHERE work_id=? AND kind='scope' AND object_id=?", (work_id, ref.scope_id)).fetchone():
                    raise WorkError('SUBMISSION_SCOPE_NOT_IN_WORK', 422)
                version = conn.execute('SELECT scope_id,available,metadata_available,digest FROM protection_versions WHERE id=?', (ref.version_id,)).fetchone()
                if not version or version[0] != ref.scope_id or not version[1] or not version[2]:
                    raise WorkError('SUBMISSION_VERSION_UNAVAILABLE')
                files.append({**ref.model_dump(mode='json'), 'digest': version[3]})
            refs = {'artifacts': artifacts, 'files': files}
            if any(len(items) != len({encoded(item) for item in items}) for items in refs.values()):
                raise WorkError('SUBMISSION_REFERENCE_DUPLICATE', 422)
            submission_id = str(uuid4())
            submission_digest = digest([work_id, command.requirement_version, run_id, refs, command.responsibility])
            conn.execute('INSERT INTO work_submissions VALUES (?,?,?,?,?,?,?,?)',
                         (submission_id, work_id, command.requirement_version, run_id, command.responsibility, encoded(refs), submission_digest, timestamp))
            version_ids = {ref['version_id'] for ref in files}
            if run_id:
                version_ids.update(r[0] for r in conn.execute("SELECT version_id FROM protection_version_dependencies WHERE owner_kind='run' AND owner_id=?", (run_id,)))
            for version_id in version_ids:
                conn.execute("INSERT OR IGNORE INTO protection_version_dependencies VALUES ('submission',?,?)", (submission_id, version_id))
            self.execution.ledger(conn, submission_id, EventType.SESSION_COMPLETED, 'DELIVERY_SUBMITTED',
                                  work_id=work_id, submission_digest=submission_digest, requirement_version=command.requirement_version)
            return {**self.work._bump(conn, work_id, timestamp), 'submission_id': submission_id, 'digest': submission_digest}
        return self.work._command('submission', work_id, command, apply)

    def _submission(self, work_id, submission_id):
        cursor = self.conn.execute('SELECT * FROM work_submissions WHERE work_id=? AND id=?', (work_id, submission_id))
        row = cursor.fetchone()
        if not row:
            raise WorkError('SUBMISSION_NOT_FOUND', 404)
        return dict(zip((col[0] for col in cursor.description), row))

    def decide(self, work_id, submission_id, command, *, acceptance=False):
        def apply(conn, timestamp):
            work = self.work._work(conn, work_id, command.expected_revision)
            submission = self._submission(work_id, submission_id)
            if acceptance:
                if command.owner != work['owner']:
                    raise WorkError('WORK_OWNER_MISMATCH', 422)
                if submission['requirement_version'] != work['requirement_version']:
                    raise WorkError('WORK_REQUIREMENT_CHANGED')
                if command.decision == 'ACCEPTED':
                    for ref in json.loads(submission['refs_json'])['files']:
                        version = conn.execute('SELECT available,digest FROM protection_versions WHERE id=?', (ref['version_id'],)).fetchone()
                        if not version or not version[0] or version[1] != ref['digest']:
                            raise WorkError('SUBMISSION_VERSION_UNAVAILABLE')
                decision_id = str(uuid4())
                conn.execute('INSERT INTO work_acceptances VALUES (?,?,?,?,?,?,?)',
                             (decision_id, work_id, submission_id, command.owner, command.decision, command.basis, timestamp))
                self.execution.ledger(conn, decision_id, EventType.USER_APPROVED if command.decision == 'ACCEPTED' else EventType.USER_REJECTED,
                                      'DELIVERY_' + command.decision, work_id=work_id, submission_id=submission_id,
                                      submission_digest=submission['digest'], requirement_version=submission['requirement_version'])
            else:
                decision_id = str(uuid4())
                conn.execute('INSERT INTO work_reviews VALUES (?,?,?,?,?,?,?)',
                             (decision_id, work_id, submission_id, command.reviewer, command.decision, command.body, timestamp))
            return {**self.work._bump(conn, work_id, timestamp), 'acceptance_id' if acceptance else 'review_id': decision_id,
                    'submission_id': submission_id, 'decision': command.decision}
        return self.work._command('acceptance' if acceptance else 'review', [work_id, submission_id], command, apply)

    def submissions(self, work_id, limit=50, offset=0):
        work = self.work._work(self.conn, work_id)
        cursor = self.conn.execute('SELECT * FROM work_submissions WHERE work_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (work_id, limit + 1, offset))
        names = [col[0] for col in cursor.description]
        items = [dict(zip(names, row)) for row in cursor.fetchall()]
        ids = encoded([item['id'] for item in items[:limit]])
        decisions = {}
        for table, key in (('work_reviews', 'reviews'), ('work_acceptances', 'acceptances')):
            cursor = self.conn.execute(f'SELECT * FROM {table} WHERE submission_id IN (SELECT value FROM json_each(?)) ORDER BY created_at,id', (ids,))
            columns = [col[0] for col in cursor.description]
            for row in cursor:
                record = dict(zip(columns, row))
                decisions.setdefault(record['submission_id'], {}).setdefault(key, []).append(record)
        for item in items[:limit]:
            item['refs'] = json.loads(item.pop('refs_json'))
            item['current_requirement'] = item['requirement_version'] == work['requirement_version']
            item.update(reviews=[], acceptances=[])
            item.update(decisions.get(item['id'], {}))
        return {'items': items[:limit], 'has_more': len(items) > limit}
