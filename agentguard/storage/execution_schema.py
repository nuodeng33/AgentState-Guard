"""Additive managed execution schema; receipts remain in work_requests."""

EXECUTION_SQL = """
CREATE TABLE work_sessions (
    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, task_id TEXT,
    source_id TEXT NOT NULL, application_id TEXT NOT NULL, store_id TEXT NOT NULL,
    scope_id TEXT NOT NULL, provider TEXT NOT NULL, binding_id TEXT,
    native_session_id TEXT, mode TEXT NOT NULL, read_only INTEGER NOT NULL,
    identity_json TEXT NOT NULL, authorization_json TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '{}', nickname TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL, reason_code TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
    generation TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX work_sessions_work ON work_sessions(work_id,created_at DESC,id DESC);
CREATE UNIQUE INDEX work_sessions_native ON work_sessions(source_id,store_id,provider,native_session_id)
    WHERE native_session_id IS NOT NULL AND state <> 'ARCHIVED';
CREATE TABLE work_runs (
    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, session_id TEXT NOT NULL,
    scope_id TEXT NOT NULL, requirement_version INTEGER NOT NULL,
    input_json TEXT NOT NULL, input_digest TEXT NOT NULL,
    input_summary TEXT NOT NULL DEFAULT '',
    baseline_id TEXT NOT NULL, generation TEXT, native_turn_id TEXT,
    state TEXT NOT NULL, dispatch_state TEXT NOT NULL DEFAULT 'NOT_SENT',
    reason_code TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
    last_seq INTEGER NOT NULL DEFAULT 0, journal_bytes INTEGER NOT NULL DEFAULT 0,
    history_status TEXT NOT NULL DEFAULT 'FULL', output_truncated INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NOT NULL DEFAULT '{}', file_result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX work_runs_session ON work_runs(session_id,created_at DESC,id DESC);
CREATE INDEX work_runs_work ON work_runs(work_id,created_at DESC,id DESC);
CREATE UNIQUE INDEX work_runs_active_session ON work_runs(session_id)
    WHERE state IN ('QUEUED','DISPATCHING','RUNNING','WAITING_APPROVAL','WAITING_INPUT','CANCELLING','FINALIZING','UNCERTAIN');
CREATE UNIQUE INDEX work_runs_active_scope ON work_runs(scope_id)
    WHERE state IN ('QUEUED','DISPATCHING','RUNNING','WAITING_APPROVAL','WAITING_INPUT','CANCELLING','FINALIZING','UNCERTAIN');
CREATE TRIGGER work_runs_input_immutable BEFORE UPDATE OF input_json,input_digest,requirement_version,baseline_id ON work_runs
WHEN OLD.input_digest <> NEW.input_digest OR OLD.requirement_version <> NEW.requirement_version OR OLD.baseline_id <> NEW.baseline_id
    OR (OLD.input_json <> NEW.input_json AND NOT (NEW.history_status='SUMMARY_ONLY' AND NEW.input_json='{}' AND NEW.completed_at IS NOT NULL))
BEGIN SELECT RAISE(ABORT,'WORK_RUN_INPUT_IMMUTABLE'); END;
CREATE TABLE work_run_events (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL,
    observed_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
    action_ref TEXT, response_to INTEGER,
    PRIMARY KEY(run_id,seq), UNIQUE(run_id,action_ref), UNIQUE(run_id,response_to)
);
CREATE INDEX work_events_pending ON work_run_events(run_id,type,seq);
CREATE TRIGGER work_run_events_no_update BEFORE UPDATE ON work_run_events
BEGIN SELECT RAISE(ABORT,'WORK_RUN_EVENT_IMMUTABLE'); END;
"""

EXECUTION_DOWN_SQL = """
DROP TRIGGER IF EXISTS work_run_events_no_update;
DROP TABLE IF EXISTS work_run_events;
DROP TRIGGER IF EXISTS work_runs_input_immutable;
DROP TABLE IF EXISTS work_runs;
DROP TABLE IF EXISTS work_sessions;
"""
