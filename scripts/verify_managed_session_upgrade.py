"""Back up a live database and verify additive migrations on a separate copy."""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentguard.evidence.ledger import verify_ledger
from agentguard.storage.db import StateDB


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def signature(conn, table, columns, where=''):
    rows = conn.execute('SELECT ' + ','.join(map(quote, columns)) + ' FROM ' + quote(table) + where).fetchall()
    serialized = sorted(json.dumps(row, ensure_ascii=True, default=lambda value: {'bytes': value.hex()}) for row in rows)
    return {'rows': len(rows), 'digest': hashlib.sha256('\n'.join(serialized).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    source = args.database.resolve(strict=True)
    destination = Path(__file__).resolve().parents[1] / 'reports' / 'managed-session-20260930'
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    backup = destination / ('state-before-managed-' + stamp + '.db')
    migrated = destination / ('state-migration-check-' + stamp + '.db')
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(backup) as saved:
        original.backup(saved)
    with sqlite3.connect(backup) as saved, sqlite3.connect(migrated) as copied:
        saved.backup(copied)
        before_version = saved.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
        columns = {table: [row[1] for row in saved.execute('PRAGMA table_info(' + quote(table) + ')')]
                   for table, in saved.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        before = {table: signature(saved, table, names) for table, names in columns.items()}
    db = StateDB(migrated)
    db.connect()
    try:
        after = {table: signature(db._conn, table, names,
                    ' WHERE version<=' + str(before_version) if table == 'schema_migrations' else '')
                 for table, names in columns.items()}
        preserved = before == after
        integrity = db._conn.execute('PRAGMA integrity_check').fetchone()[0]
        ledger_errors = verify_ledger(db._conn)
        report = {'result': 'PASS' if preserved and integrity == 'ok' and not ledger_errors else 'FAIL',
                  'source': str(source), 'backup': str(backup), 'migrated_copy': str(migrated),
                  'schema_before': before_version, 'schema_after': db._conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0],
                  'all_existing_rows_preserved': preserved, 'integrity': integrity, 'ledger_errors': ledger_errors,
                  'original_tables': before}
    finally:
        db.close()
    (destination / 'migration-check.json').write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'original_tables'}, ensure_ascii=True))
    return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
