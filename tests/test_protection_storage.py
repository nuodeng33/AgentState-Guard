"""Integrity and concurrency checks for protected workspace storage."""

import gzip
import os
import sqlite3

import pytest

from agentguard.recovery.workspace_permissions import PosixPermissionBackend
from agentguard.recovery.workspace_policy import scan_workspace
from agentguard.storage.blob import BlobStore
from agentguard.storage.gc import execute_gc, plan_gc
from agentguard.storage.migrations import MigrationEngine


@pytest.fixture
def store_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)"
    )
    conn.commit()
    MigrationEngine(tmp_path / "state.db").migrate(conn)
    store = BlobStore(tmp_path / "blobs")
    yield store, conn
    conn.close()


def test_blob_tamper_and_missing_dedup_are_repaired(store_db):
    store, conn = store_db
    payload = b"protected bytes"
    digest = store.put(payload, conn)
    path = store._blob_path(digest)
    path.write_bytes(gzip.compress(b"tampered bytes"))
    assert store.get(digest) is None
    assert store.put(payload, conn) == digest
    assert store.get(digest) == payload
    path.unlink()
    assert store.put(payload, conn) == digest
    assert store.get(digest) == payload
    assert conn.execute("SELECT ref_count FROM blobs WHERE sha256 = ?", (digest,)).fetchone()[0] == 3


def test_verified_dedup_skips_compression_but_corruption_is_repaired(store_db, monkeypatch):
    store, conn = store_db
    payload = b"repeat the same protected content"
    digest = store.put(payload, conn)
    compress = gzip.compress
    compressed_inputs = []

    def record_compression(data):
        compressed_inputs.append(data)
        return compress(data)

    monkeypatch.setattr("agentguard.storage.blob.gzip.compress", record_compression)
    assert store.put(payload, conn) == digest
    assert compressed_inputs == []
    assert conn.execute("SELECT ref_count FROM blobs WHERE sha256 = ?", (digest,)).fetchone()[0] == 2

    store._blob_path(digest).write_bytes(compress(b"corrupt content"))
    assert store.put(payload, conn) == digest
    assert compressed_inputs == [payload]
    assert store.get(digest) == payload
    assert conn.execute("SELECT ref_count FROM blobs WHERE sha256 = ?", (digest,)).fetchone()[0] == 3


def test_blob_short_writes_and_invalid_paths(store_db, monkeypatch):
    store, conn = store_db
    original_write = os.write

    def partial_write(fd, data):
        return original_write(fd, data[:3])

    monkeypatch.setattr("agentguard.storage.blob.os.write", partial_write)
    digest = store.put(b"write the complete compressed payload", conn)
    assert store.get(digest) == b"write the complete compressed payload"
    assert store.get("../" + digest) is None
    assert store.remove_from_disk("../" + digest) is False
    with pytest.raises(ValueError, match="INVALID_BLOB_DIGEST"):
        store._blob_path("../" + digest)


def test_blob_decompression_is_bounded(store_db, monkeypatch):
    store, conn = store_db
    payload = b"z" * 1024
    digest = store.put(payload, conn)
    monkeypatch.setattr(store, "MAX_BLOB_BYTES", 100)
    assert store.get(digest) is None


def test_failed_blob_repair_does_not_increment_reference(store_db, monkeypatch):
    store, conn = store_db
    digest = store.put(b"repair me", conn)
    store._blob_path(digest).unlink()

    def no_progress(_fd, _data):
        return 0

    monkeypatch.setattr("agentguard.storage.blob.os.write", no_progress)
    with pytest.raises(OSError, match="BLOB_SHORT_WRITE"):
        store.put(b"repair me", conn)
    assert conn.execute("SELECT ref_count FROM blobs WHERE sha256 = ?", (digest,)).fetchone()[0] == 1


@pytest.mark.parametrize("replacement", [b"BBBB", b"BBBBB"])
def test_scan_rejects_concurrent_rewrite(tmp_path, monkeypatch, replacement):
    import agentguard.recovery.workspace_policy as policy

    path = tmp_path / "content.txt"
    path.write_bytes(b"AAAA")
    original_open = policy.os.open
    changed = False

    def rewrite_at_open(name, flags, *args, **kwargs):
        nonlocal changed
        if not changed and os.fspath(name) == os.fspath(path):
            changed = True
            path.write_bytes(replacement)
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(policy.os, "open", rewrite_at_open)
    scan = scan_workspace(tmp_path, permission_backend=PosixPermissionBackend())
    assert changed
    assert scan.entries[0].category == "unreachable"
    assert scan.entries[0].content is None


def test_stale_gc_plan_cannot_delete_new_protection_reference(store_db):
    store, conn = store_db
    digest = store.put(b"keep this", conn)
    store.release(digest, conn)
    conn.commit()
    plan = plan_gc(conn, store)
    assert digest in plan.blobs_to_remove
    conn.execute(
        "INSERT INTO protection_blob_refs(owner_kind,owner_id,scope_id,sha256) VALUES (?,?,?,?)",
        ("version", "v1", "scope", digest),
    )
    conn.commit()
    result = execute_gc(conn, store, plan, dry_run=False)
    assert result["blobs_removed"] == 0
    assert store.get(digest) == b"keep this"
    assert conn.execute("SELECT 1 FROM blobs WHERE sha256 = ?", (digest,)).fetchone()


def test_gc_rejects_checkpoint_path_escape(store_db, tmp_path):
    store, conn = store_db
    outside = tmp_path.parent / "outside-gc.txt"
    outside.write_text("keep", encoding="utf-8")
    conn.execute(
        "INSERT INTO checkpoints(label,created_at,snapshot_path,hash_sha256,file_count) VALUES (?,?,?,?,?)",
        ("old", "2000-01-01T00:00:00Z", "../outside-gc.txt", "0" * 64, 0),
    )
    conn.commit()
    plan = plan_gc(conn, store, keep_last_n=0, keep_daily=0, keep_weekly=0, keep_monthly=0)
    with pytest.raises(ValueError, match="GC_SNAPSHOT_PATH_INVALID"):
        execute_gc(conn, store, plan, dry_run=False)
    assert outside.read_text(encoding="utf-8") == "keep"
    assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 1


def test_stale_gc_plan_preserves_new_pending_checkpoint_dependency(store_db):
    store, conn = store_db
    conn.execute(
        "INSERT INTO checkpoints(label,created_at,snapshot_path,hash_sha256,file_count) VALUES (?,?,?,?,?)",
        ("old", "2000-01-01T00:00:00Z", "snapshots/snapshot-000001.dat", "0" * 64, 0),
    )
    cp_id = conn.execute("SELECT id FROM checkpoints").fetchone()[0]
    conn.commit()
    plan = plan_gc(conn, store, keep_last_n=0, keep_daily=0, keep_weekly=0, keep_monthly=0)
    assert cp_id in {item["id"] for item in plan.checkpoints_to_delete}
    conn.execute(
        "INSERT INTO transactions(status,created_at,pre_checkpoint) VALUES (?,?,?)",
        ("planned", "2026-01-01T00:00:00Z", cp_id),
    )
    conn.commit()
    result = execute_gc(conn, store, plan, dry_run=False)
    assert result["checkpoints_deleted"] == 0
    assert conn.execute("SELECT 1 FROM checkpoints WHERE id = ?", (cp_id,)).fetchone()


def test_retain_verified_reads_full_content_once_and_reference_update_rolls_back(store_db, monkeypatch):
    store, conn = store_db
    payload = b"retain existing verified content"
    sha = store.put(payload, conn)
    conn.commit()
    original_get = store.get
    reads = []

    def counted_get(value):
        reads.append(value)
        return original_get(value)

    def no_recompression(_data):
        raise AssertionError("retaining historical bytes must not recompress them")

    monkeypatch.setattr(store, "get", counted_get)
    monkeypatch.setattr("agentguard.storage.blob.gzip.compress", no_recompression)
    conn.execute("BEGIN")
    assert store.retain_verified(sha, conn) == payload
    assert reads == [sha]
    assert conn.execute("SELECT ref_count FROM blobs WHERE sha256=?", (sha,)).fetchone()[0] == 2
    conn.rollback()
    assert conn.execute("SELECT ref_count FROM blobs WHERE sha256=?", (sha,)).fetchone()[0] == 1


@pytest.mark.parametrize("damage", ["missing", "wrong-hash", "bad-gzip", "over-limit", "missing-metadata"])
def test_retain_verified_does_not_admit_missing_or_invalid_storage(store_db, monkeypatch, damage):
    store, conn = store_db
    sha = store.put(b"retain this only if verified", conn)
    path = store._blob_path(sha)
    if damage == "missing":
        path.unlink()
    elif damage == "wrong-hash":
        path.write_bytes(gzip.compress(b"changed"))
    elif damage == "bad-gzip":
        path.write_bytes(b"corrupt gzip")
    elif damage == "over-limit":
        monkeypatch.setattr(store, "MAX_BLOB_BYTES", 2)
    else:
        conn.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
    before = conn.execute("SELECT sha256,ref_count FROM blobs").fetchall()
    assert store.retain_verified(sha, conn) is None
    assert conn.execute("SELECT sha256,ref_count FROM blobs").fetchall() == before
    assert store.retain_verified("../invalid-digest", conn) is None


def test_retained_verified_blob_survives_a_preexisting_gc_plan(store_db):
    store, conn = store_db
    payload = b"keep a newly retained blob"
    sha = store.put(payload, conn)
    store.release(sha, conn)
    conn.commit()
    plan = plan_gc(conn, store)
    assert sha in plan.blobs_to_remove
    assert store.retain_verified(sha, conn) == payload
    conn.commit()
    assert execute_gc(conn, store, plan, dry_run=False)["blobs_removed"] == 0
    assert store.get(sha) == payload
