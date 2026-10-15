"""Additive catalog migration; discovery facts do not confer execution authority."""

DISCOVERY_CATALOG_SQL = """
CREATE TABLE discovery_applications (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, path TEXT NOT NULL,
    metadata_json TEXT NOT NULL, registered INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1, observed_at TEXT NOT NULL,
    UNIQUE(source_id,path)
);
CREATE TABLE discovery_session_stores (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, provider TEXT NOT NULL,
    path TEXT NOT NULL, metadata_json TEXT NOT NULL,
    registered INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
    observed_at TEXT NOT NULL
);
CREATE INDEX discovery_applications_source ON discovery_applications(source_id,id);
CREATE INDEX discovery_stores_source ON discovery_session_stores(source_id,id);
DROP INDEX IF EXISTS protection_sessions_candidates;
ALTER TABLE protection_sessions RENAME TO protection_sessions_legacy;
CREATE TABLE protection_sessions (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, provider TEXT NOT NULL,
    provider_session_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL, store_id TEXT NOT NULL DEFAULT 'legacy-unverified',
    UNIQUE(source_id,store_id,provider,provider_session_id)
);
INSERT INTO protection_sessions
    SELECT id,source_id,provider,provider_session_id,
           json_set(metadata_json,'$.history_status','LEGACY_UNVERIFIED'),
           observed_at,'legacy:' || source_id
    FROM protection_sessions_legacy;
DROP TABLE protection_sessions_legacy;
CREATE INDEX protection_sessions_candidates ON protection_sessions(observed_at DESC,id DESC)
    WHERE json_extract(metadata_json,'$.cwd') IS NOT NULL;
"""

DISCOVERY_CATALOG_DOWN_SQL = """
DROP INDEX IF EXISTS protection_sessions_candidates;
ALTER TABLE protection_sessions RENAME TO protection_sessions_catalog;
CREATE TABLE protection_sessions (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, provider TEXT NOT NULL,
    provider_session_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL, UNIQUE(source_id,provider,provider_session_id)
);
INSERT INTO protection_sessions
    SELECT id,source_id,provider,provider_session_id,metadata_json,observed_at
    FROM protection_sessions_catalog;
DROP TABLE protection_sessions_catalog;
CREATE INDEX protection_sessions_candidates ON protection_sessions(observed_at DESC,id DESC)
    WHERE json_extract(metadata_json,'$.cwd') IS NOT NULL;
DROP TABLE discovery_applications;
DROP TABLE discovery_session_stores;
"""
