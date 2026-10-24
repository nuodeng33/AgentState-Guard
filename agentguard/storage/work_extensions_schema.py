"""Additive pending-instruction and display-preference persistence."""

WORK_EXTENSIONS_SQL = """
CREATE TABLE work_pending_instructions (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES works(id),
    session_id TEXT NOT NULL REFERENCES work_sessions(id),
    predecessor_run_id TEXT NOT NULL REFERENCES work_runs(id),
    request_id TEXT NOT NULL UNIQUE,
    promotion_request_id TEXT NOT NULL UNIQUE,
    requirement_version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING','HELD','PROMOTED','CANCELLED')),
    reason_code TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    promoted_run_id TEXT REFERENCES work_runs(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id,requirement_version) REFERENCES work_requirements(work_id,version),
    CHECK((state='PROMOTED' AND promoted_run_id IS NOT NULL) OR
          (state<>'PROMOTED' AND promoted_run_id IS NULL))
);
CREATE UNIQUE INDEX work_pending_one_slot ON work_pending_instructions(session_id)
    WHERE state IN ('PENDING','HELD');
CREATE INDEX work_pending_work ON work_pending_instructions(work_id,created_at DESC);
CREATE TRIGGER work_pending_input_immutable BEFORE UPDATE OF
    work_id,session_id,predecessor_run_id,request_id,promotion_request_id,
    requirement_version,snapshot_json,snapshot_digest ON work_pending_instructions
WHEN OLD.work_id<>NEW.work_id OR OLD.session_id<>NEW.session_id OR
     OLD.predecessor_run_id<>NEW.predecessor_run_id OR OLD.request_id<>NEW.request_id OR
     OLD.promotion_request_id<>NEW.promotion_request_id OR
     OLD.requirement_version<>NEW.requirement_version OR
     OLD.snapshot_json<>NEW.snapshot_json OR OLD.snapshot_digest<>NEW.snapshot_digest
BEGIN SELECT RAISE(ABORT,'WORK_PENDING_INPUT_IMMUTABLE'); END;

CREATE TABLE work_agent_appearances (
    work_id TEXT NOT NULL REFERENCES works(id),
    agent_id TEXT NOT NULL,
    color TEXT CHECK(color IS NULL OR color IN
        ('#7cc6bc','#b9a6f4','#8fb8e8','#e3a5bf','#d9c08a','#a8c98f')),
    revision INTEGER NOT NULL CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id,agent_id)
);
"""

WORK_EXTENSIONS_DOWN_SQL = """
DROP TABLE IF EXISTS work_agent_appearances;
DROP TRIGGER IF EXISTS work_pending_input_immutable;
DROP TABLE IF EXISTS work_pending_instructions;
"""
