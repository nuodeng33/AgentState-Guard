"""Immutable, version-bound submissions and human decisions in the Work domain."""

DELIVERY_SQL = """
ALTER TABLE work_artifact_versions ADD COLUMN source TEXT NOT NULL DEFAULT 'MANUAL';
ALTER TABLE work_artifact_versions ADD COLUMN source_run_id TEXT;
ALTER TABLE work_artifact_versions ADD COLUMN source_events_json TEXT NOT NULL DEFAULT '[]';
CREATE TABLE work_submissions (
    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, requirement_version INTEGER NOT NULL,
    run_id TEXT, responsibility TEXT NOT NULL, refs_json TEXT NOT NULL,
    digest TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX work_submissions_work ON work_submissions(work_id,created_at DESC,id DESC);
CREATE INDEX work_submissions_run ON work_submissions(run_id);
CREATE TABLE work_reviews (
    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, submission_id TEXT NOT NULL,
    reviewer TEXT NOT NULL, decision TEXT NOT NULL, body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX work_reviews_submission ON work_reviews(submission_id,created_at DESC,id DESC);
CREATE TABLE work_acceptances (
    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, submission_id TEXT NOT NULL,
    owner TEXT NOT NULL, decision TEXT NOT NULL, basis TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX work_acceptances_submission ON work_acceptances(submission_id,created_at DESC,id DESC);
"""

DELIVERY_DOWN_SQL = """
DROP TABLE IF EXISTS work_acceptances;
DROP TABLE IF EXISTS work_reviews;
DROP TABLE IF EXISTS work_submissions;
ALTER TABLE work_artifact_versions DROP COLUMN source_events_json;
ALTER TABLE work_artifact_versions DROP COLUMN source_run_id;
ALTER TABLE work_artifact_versions DROP COLUMN source;
"""

for _table in ('work_submissions', 'work_reviews', 'work_acceptances'):
    for _action in ('UPDATE', 'DELETE'):
        _name = _table + '_no_' + _action.lower()
        DELIVERY_SQL += f"CREATE TRIGGER {_name} BEFORE {_action} ON {_table} BEGIN SELECT RAISE(ABORT,'WORK_DELIVERY_IMMUTABLE'); END;\n"
        DELIVERY_DOWN_SQL = f'DROP TRIGGER IF EXISTS {_name};\n' + DELIVERY_DOWN_SQL
