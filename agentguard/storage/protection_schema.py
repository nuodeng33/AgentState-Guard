"""Additive schema for the existing Core's continuously protected directories."""

PROTECTION_SQL = """
CREATE TABLE protection_scopes (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, root_key TEXT NOT NULL,
    identity_digest TEXT NOT NULL, binding_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL, reason_code TEXT NOT NULL,
    observed_at TEXT, baseline_id TEXT, latest_id TEXT,
    pending_digest TEXT, pending_since REAL,
    retention_days INTEGER NOT NULL DEFAULT 30,
    quota_bytes INTEGER NOT NULL DEFAULT 2147483648,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, root_key, identity_digest)
);
CREATE TABLE protection_versions (
    id TEXT PRIMARY KEY, scope_id TEXT NOT NULL REFERENCES protection_scopes(id),
    parent_id TEXT, created_at TEXT NOT NULL, manifest_json TEXT NOT NULL,
    digest TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1, kind TEXT NOT NULL, operation_id TEXT,
    sample_started_at TEXT, sample_completed_at TEXT
);
CREATE INDEX protection_versions_scope ON protection_versions(scope_id, created_at);
CREATE TABLE protection_changes (
    id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, before_id TEXT NOT NULL,
    after_id TEXT NOT NULL, created_at TEXT NOT NULL, attribution TEXT NOT NULL,
    operation_id TEXT, entries_json TEXT NOT NULL
);
CREATE INDEX protection_changes_scope ON protection_changes(scope_id, created_at);
CREATE TABLE protection_blob_refs (
    owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, scope_id TEXT NOT NULL,
    sha256 TEXT NOT NULL, PRIMARY KEY(owner_kind, owner_id, sha256)
);
CREATE INDEX protection_blob_refs_sha ON protection_blob_refs(sha256);
CREATE TABLE protection_version_dependencies (
    owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, version_id TEXT NOT NULL,
    PRIMARY KEY(owner_kind, owner_id, version_id)
);
CREATE TABLE protection_previews (
    id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, mode TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, digest TEXT NOT NULL,
    epoch TEXT NOT NULL, selection_json TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE protection_operations (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope_id TEXT, preview_id TEXT,
    request_id TEXT NOT NULL UNIQUE, request_digest TEXT NOT NULL,
    state TEXT NOT NULL, reason_code TEXT NOT NULL, result_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE protection_operation_storage (
    operation_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'RESERVED'
);
CREATE TABLE protection_steps (
    operation_id TEXT NOT NULL, step_id TEXT NOT NULL, relative_path TEXT NOT NULL,
    before_json TEXT, after_json TEXT, state TEXT NOT NULL,
    receipt_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(operation_id, step_id)
);
CREATE TABLE protection_step_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL, step_id TEXT NOT NULL,
    state TEXT NOT NULL, recorded_at TEXT NOT NULL, detail_json TEXT NOT NULL
);
CREATE TRIGGER protection_events_no_update BEFORE UPDATE ON protection_step_events
BEGIN SELECT RAISE(ABORT, 'PROTECTION_JOURNAL_IMMUTABLE'); END;
CREATE TRIGGER protection_events_no_delete BEFORE DELETE ON protection_step_events
BEGIN SELECT RAISE(ABORT, 'PROTECTION_JOURNAL_IMMUTABLE'); END;
CREATE TABLE protection_requests (
    id TEXT PRIMARY KEY, digest TEXT NOT NULL, response_json TEXT NOT NULL
);
CREATE TABLE protection_work_links (
    work_id TEXT NOT NULL REFERENCES works(id), kind TEXT NOT NULL,
    object_id TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
    basis TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(work_id, kind, object_id, task_id)
);
CREATE TABLE protection_sessions (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, provider TEXT NOT NULL,
    provider_session_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(source_id, provider, provider_session_id)
);
CREATE TABLE protection_sources (
    id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, observed_at TEXT NOT NULL
);
"""

PROTECTION_DOWN_SQL = """
DROP TABLE IF EXISTS protection_sources;
DROP TABLE IF EXISTS protection_sessions;
DROP TABLE IF EXISTS protection_work_links;
DROP TABLE IF EXISTS protection_requests;
DROP TRIGGER IF EXISTS protection_events_no_delete;
DROP TRIGGER IF EXISTS protection_events_no_update;
DROP TABLE IF EXISTS protection_step_events;
DROP TABLE IF EXISTS protection_steps;
DROP TABLE IF EXISTS protection_operations;
DROP TABLE IF EXISTS protection_operation_storage;
DROP TABLE IF EXISTS protection_previews;
DROP TABLE IF EXISTS protection_blob_refs;
DROP TABLE IF EXISTS protection_version_dependencies;
DROP TABLE IF EXISTS protection_changes;
DROP TABLE IF EXISTS protection_versions;
DROP TABLE IF EXISTS protection_scopes;
"""

# Migration 15 above is immutable. Logical accounting measures retained field
# bytes (UTF-8 for text), with a small fixed row allowance, not SQLite pages.
_OWNED_METADATA = {
    "protection_versions": ("{r}.scope_id", "id scope_id parent_id created_at manifest_json digest pinned available kind operation_id sample_started_at sample_completed_at summary_json metadata_available"),
    "protection_changes": ("{r}.scope_id", "id scope_id before_id after_id created_at attribution operation_id entries_json summary_json metadata_available"),
    "protection_previews": ("{r}.scope_id", "id scope_id mode created_at expires_at digest epoch selection_json status"),
    "protection_operations": ("{r}.scope_id", "id kind scope_id preview_id request_id request_digest state reason_code result_json created_at updated_at"),
    "protection_steps": ("(SELECT scope_id FROM protection_operations WHERE id={r}.operation_id)", "operation_id step_id relative_path before_json after_json state receipt_json"),
    "protection_step_events": ("(SELECT scope_id FROM protection_operations WHERE id={r}.operation_id)", "sequence operation_id step_id state recorded_at detail_json"),
    "protection_blob_refs": ("{r}.scope_id", "owner_kind owner_id scope_id sha256"),
    "protection_version_dependencies": ("(SELECT scope_id FROM protection_versions WHERE id={r}.version_id)", "owner_kind owner_id version_id"),
    "protection_operation_storage": ("{r}.scope_id", "operation_id scope_id size_bytes state"),
    "protection_requests": ("{r}.scope_id", "id digest response_json scope_id"),
    "protection_work_links": ("CASE {r}.kind WHEN 'scope' THEN {r}.object_id WHEN 'batch' THEN (SELECT scope_id FROM protection_changes WHERE id={r}.object_id) END", "work_id kind object_id task_id basis created_at"),
    "evidence_ledger_events": ("{r}.subject_ref", "sequence event_id schema_version recorded_at observed_at event_family event_type source result execution_domain_id supervision_session_id transaction_id checkpoint_id subject_ref evidence_refs_json payload_safe_json payload_digest prev_hash curr_hash"),
}


def _field_bytes(columns, alias):
    return "32+" + "+".join(f"COALESCE(length(CAST({alias}.{c} AS BLOB)),0)" for c in columns.split())


PROTECTION_OPTIMIZATION_SQL = """
ALTER TABLE protection_scopes ADD COLUMN metadata_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE protection_scopes ADD COLUMN last_collected_at REAL;
ALTER TABLE protection_versions ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE protection_versions ADD COLUMN metadata_available INTEGER NOT NULL DEFAULT 1;
ALTER TABLE protection_changes ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE protection_changes ADD COLUMN metadata_available INTEGER NOT NULL DEFAULT 1;
ALTER TABLE protection_requests ADD COLUMN scope_id TEXT;
UPDATE protection_requests SET scope_id=json_extract(response_json,'$.scope_id');
UPDATE protection_versions SET summary_json=json_object(
    'coverage',json_object(
        'restorable',(SELECT count(*) FROM json_each(manifest_json) WHERE json_extract(value,'$.category')='restorable'),
        'excluded',(SELECT count(*) FROM json_each(manifest_json) WHERE json_extract(value,'$.category')='excluded'),
        'audit_only',(SELECT count(*) FROM json_each(manifest_json) WHERE json_extract(value,'$.category')='audit_only'),
        'unreachable',(SELECT count(*) FROM json_each(manifest_json) WHERE json_extract(value,'$.category')='unreachable')),
    'file_count',(SELECT count(*) FROM json_each(manifest_json)),
    'change_count',COALESCE((SELECT json_array_length(entries_json) FROM protection_changes WHERE after_id=protection_versions.id),0));
UPDATE protection_changes SET summary_json=json_object('change_count',json_array_length(entries_json));
CREATE INDEX protection_refs_scope ON protection_blob_refs(scope_id,sha256);
CREATE INDEX protection_dependencies_version ON protection_version_dependencies(version_id);
CREATE INDEX protection_changes_after ON protection_changes(after_id);
CREATE INDEX protection_versions_page ON protection_versions(scope_id,created_at DESC,id DESC);
CREATE INDEX protection_changes_page ON protection_changes(scope_id,created_at DESC,id DESC);
CREATE INDEX protection_operations_page ON protection_operations(created_at DESC,id DESC);
CREATE INDEX protection_events_operation ON protection_step_events(operation_id,sequence);
CREATE INDEX protection_sessions_candidates ON protection_sessions(observed_at DESC,id DESC) WHERE json_extract(metadata_json,'$.cwd') IS NOT NULL;
"""

PROTECTION_OPTIMIZATION_DOWN_SQL = """
DROP INDEX protection_refs_scope;
DROP INDEX protection_dependencies_version;
DROP INDEX protection_changes_after;
DROP INDEX protection_versions_page;
DROP INDEX protection_changes_page;
DROP INDEX protection_operations_page;
DROP INDEX protection_events_operation;
DROP INDEX protection_sessions_candidates;
"""

for _table, (_owner, _columns) in _OWNED_METADATA.items():
    # Cache deltas in the same transaction as the authoritative rows. Reads and
    # unchanged captures never rescan the accumulated JSON history for quota.
    PROTECTION_OPTIMIZATION_SQL += f"""
UPDATE protection_scopes SET metadata_bytes=metadata_bytes+COALESCE(
    (SELECT SUM({_field_bytes(_columns, 'r')}) FROM {_table} r
     WHERE {_owner.format(r='r')}=protection_scopes.id),0);
"""
    for _action in ("INSERT", "UPDATE", "DELETE"):
        _name = f"protection_account_{_table}_{_action.lower()}"
        _body = ""
        if _action != "INSERT":
            _body += f"UPDATE protection_scopes SET metadata_bytes=metadata_bytes-({_field_bytes(_columns, 'OLD')}) WHERE id={_owner.format(r='OLD')};"
        if _action != "DELETE":
            _body += f"UPDATE protection_scopes SET metadata_bytes=metadata_bytes+({_field_bytes(_columns, 'NEW')}) WHERE id={_owner.format(r='NEW')};"
        PROTECTION_OPTIMIZATION_SQL += f"CREATE TRIGGER {_name} AFTER {_action} ON {_table} BEGIN {_body} END;\n"
        PROTECTION_OPTIMIZATION_DOWN_SQL += f"DROP TRIGGER {_name};\n"

PROTECTION_OPTIMIZATION_DOWN_SQL += """
ALTER TABLE protection_requests DROP COLUMN scope_id;
ALTER TABLE protection_changes DROP COLUMN metadata_available;
ALTER TABLE protection_changes DROP COLUMN summary_json;
ALTER TABLE protection_versions DROP COLUMN metadata_available;
ALTER TABLE protection_versions DROP COLUMN summary_json;
ALTER TABLE protection_scopes DROP COLUMN last_collected_at;
ALTER TABLE protection_scopes DROP COLUMN metadata_bytes;
"""
