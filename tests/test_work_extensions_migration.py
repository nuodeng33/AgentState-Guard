"""The approved additive migration preserves all existing records and receipts."""

import json
import sqlite3
from uuid import uuid4

from agentguard.evidence.ledger import verify_ledger
from agentguard.evidence.models import EventType
from agentguard.execution.service import ExecutionService
from agentguard.storage.db import StateDB
from agentguard.storage.migrations import MigrationEngine
from agentguard.work.models import CreateWork
from agentguard.work.service import WorkService


def test_schema19_to20_keeps_every_old_table_row_and_ledger_hash(tmp_path):
    path = tmp_path / 'migration-copy.db'
    connection = sqlite3.connect(path)
    connection.execute('''CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT,
        applied_at TEXT,checksum TEXT,duration_ms INTEGER)''')
    connection.commit()
    engine = MigrationEngine(path)
    assert engine.migrate(connection, target=19)['errors'] == []
    database = StateDB(path)
    database._conn = connection
    command = CreateWork(request_id=uuid4(), title='Preserve exact Work', owner='Test owner', requirement='Keep old records')
    work = WorkService(database).create(command)
    with database.transaction() as conn:
        ExecutionService.ledger(conn, work['work_id'], EventType.USER_APPROVED, 'MIGRATION_FIXTURE', preserved=True)
        conn.execute("INSERT INTO checkpoints(label,created_at,snapshot_path,hash_sha256,file_count) VALUES('old-checkpoint','2026-10-24','snapshot','fixture-hash',1)")
    assert verify_ledger(connection) == []
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name<>'schema_migrations'")]
    def rows(table):
        return sorted(json.dumps(list(row), sort_keys=True, default=str) for row in connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
    before = {name: rows(name) for name in tables}
    old_migrations = connection.execute('SELECT * FROM schema_migrations ORDER BY version').fetchall()
    result = engine.migrate(connection)
    assert result['errors'] == [] and len(result['applied']) == 1
    assert engine.current_version(connection) == 20
    assert {name: rows(name) for name in tables} == before
    assert connection.execute('SELECT * FROM schema_migrations WHERE version<=19 ORDER BY version').fetchall() == old_migrations
    assert verify_ledger(connection) == []
    assert WorkService(database).create(command) == work
    assert connection.execute('SELECT count(*) FROM work_pending_instructions').fetchone()[0] == 0
    assert connection.execute('SELECT count(*) FROM work_agent_appearances').fetchone()[0] == 0
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    database.close()
