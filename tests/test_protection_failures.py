"""Failure-path contracts for the durable protection coordinator."""

import gzip
import hashlib
import json
import os
import threading
import time
from contextlib import closing, contextmanager

import pytest

from agentguard.recovery.protection import (
    ProtectionError,
    ProtectionRuntime,
    ProtectionService,
)
from agentguard.recovery.protection_domains import DomainRegistry


def _domains(state):
    domains = DomainRegistry(state)
    local = {
        "source_id": "local", "kind": "NATIVE", "label": "Local",
        "running": True, "connected": True, "epoch": f"native-{os.name}",
        "capabilities": {"scan": True, "apply": True},
    }
    domains._sources = {"local": local}
    domains.sources = lambda refresh=True: [local]
    return domains


def _service(state, domains, *, fault=None):
    return ProtectionService(state / "state.db", state, domains, fault=fault)


def _queued_restore(service, project):
    scope_id = service.register("local", str(project), "register")["scope_id"]
    baseline = service.scope(scope_id)["baseline_id"]
    file_id = next(e["file_id"] for e in service.version(baseline)["files"] if e["relative_path"] == "a.txt")
    (project / "a.txt").write_text("current", encoding="utf-8")
    assert service.capture(scope_id, force=True)["status"] == "RECORDED"
    preview = service.preview(scope_id, "restore_version", [file_id], version_id=baseline)
    assert preview["status"] == "READY"
    queued = service.queue_restore(preview["preview_id"], preview["digest"], "restore")
    return scope_id, baseline, queued["id"]


def test_restart_fails_old_start_command_without_invoking_source(tmp_path):
    state = tmp_path / "state"
    state.mkdir()

    class Sources:
        calls = 0

        def connect(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("old source command was replayed")

        def close(self):
            pass

    domains = Sources()
    service = _service(state, domains)
    try:
        operation_id = service.source_action("wsl:stopped", "start-and-connect", "start")["id"]
        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_operations SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (operation_id,))
    finally:
        service.close()

    runtime = ProtectionRuntime(state / "state.db", state, domains)
    runtime.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with closing(_service(state, domains)) as service:
                operation = service.operation(operation_id)
            if operation["state"] != "QUEUED":
                break
            time.sleep(0.02)
        assert operation["state"] == "FAILED"
        assert operation["reason_code"] == "PROTECTION_SOURCE_INTERRUPTED"
        assert domains.calls == 0
    finally:
        runtime.close()


def test_restore_must_not_succeed_when_postcapture_exceeds_quota(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    domains = _domains(state)
    service = _service(state, domains)
    try:
        scope_id, _baseline, operation_id = _queued_restore(service, project)
        service.configure(scope_id, "quota", 1, enabled=True, retention_days=30, quota_bytes=1024 * 1024)
        # This unselected change occurs after approval and must not be attributed
        # to recovery, but it can prevent the post-restore snapshot from fitting.
        (project / "unselected.bin").write_bytes(os.urandom(1200 * 1024))
        result = service.execute(operation_id)
        scope = service.scope(scope_id)
        assert scope["status"] == "PAUSED"
        assert scope["reason_code"] == "PROTECTION_QUOTA_EXCEEDED"
        assert result["state"] != "SUCCEEDED", result
    finally:
        service.close()


def test_version_read_rejects_corrupt_manifest_digest(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("baseline", encoding="utf-8")
    service = _service(state, _domains(state))
    try:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        baseline = service.scope(scope_id)["baseline_id"]
        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_versions SET manifest_json='{}' WHERE id=?", (baseline,))
        with pytest.raises(ProtectionError, match="PROTECTION_MANIFEST_INVALID"):
            service.version(baseline)
        with pytest.raises(ProtectionError, match="PROTECTION_MANIFEST_INVALID"):
            service.versions(scope_id)
    finally:
        service.close()


def test_corrupt_target_blob_rejects_before_live_write(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(state, _domains(state))
    try:
        _scope_id, baseline, operation_id = _queued_restore(service, project)
        entry = service.version(baseline)["files"][0]
        service.blobs._blob_path(entry["content_digest"]).write_bytes(b"corrupt gzip")
        result = service.execute(operation_id)
        assert result["state"] == "REJECTED"
        assert result["reason_code"] == "PROTECTION_CONTENT_UNAVAILABLE"
        assert target.read_text(encoding="utf-8") == "current"
    finally:
        service.close()


def test_preview_and_queued_operation_keep_expired_version_available(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(state, _domains(state))
    try:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        target.write_text("middle", encoding="utf-8")
        middle = service.capture(scope_id, force=True)["version_id"]
        target.write_text("latest", encoding="utf-8")
        service.capture(scope_id, force=True)
        entry = service.version(middle)["files"][0]
        blob_path = service.blobs._blob_path(entry["content_digest"])
        preview = service.preview(scope_id, "restore_version", [entry["file_id"]], version_id=middle)
        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_versions SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (middle,))
        service.configure(scope_id, "retention", 1, enabled=True, retention_days=1, quota_bytes=2 * 1024 * 1024 * 1024)

        service.collect(scope_id)
        assert service.version(middle)["available"] == 1
        assert blob_path.is_file()
        queued = service.queue_restore(preview["preview_id"], preview["digest"], "restore")
        service.collect(scope_id)
        assert service.version(middle)["available"] == 1
        assert blob_path.is_file()

        target.write_text("external edit", encoding="utf-8")
        assert service.execute(queued["id"])["state"] == "REJECTED"
        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_operations SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (queued["id"],))
        # The first collection releases the completed operation dependency;
        # the next pass can expire the now-unreferenced version.
        service.collect(scope_id)
        service.collect(scope_id)
        assert service.version(middle)["available"] == 0
        assert not blob_path.exists()
    finally:
        service.close()


def test_retention_reclaims_blob_and_quota_for_next_capture(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.bin"
    contents = [hashlib.shake_256(label).digest(32768 if label == b"expired" else 512) for label in (b"baseline", b"expired", b"latest", b"next")]
    target.write_bytes(contents[0])
    service = _service(state, _domains(state))
    try:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        target.write_bytes(contents[1])
        expired = service.capture(scope_id, force=True)["version_id"]
        target.write_bytes(contents[2])
        service.capture(scope_id, force=True)
        sha = service.version(expired)["files"][0]["content_digest"]
        blob_path = service.blobs._blob_path(sha)
        compressed_size = service.conn.execute("SELECT compressed_size FROM blobs WHERE sha256=?", (sha,)).fetchone()[0]
        before = service._usage(scope_id)
        quota = before - compressed_size + len(gzip.compress(contents[3])) + 12000
        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_versions SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (expired,))
        service.configure(scope_id, "retention", 1, enabled=True, retention_days=1, quota_bytes=quota)

        before_collect = service.space(scope_id)
        collected = service.collect(scope_id)
        assert service.version(expired)["available"] == 0
        assert not blob_path.exists()
        assert service.conn.execute("SELECT 1 FROM blobs WHERE sha256=?", (sha,)).fetchone() is None
        assert collected["storage_bytes"] < before_collect["logical_retained_bytes"] - compressed_size
        assert service.space(scope_id)["metadata_bytes"] < before_collect["metadata_bytes"]
        target.write_bytes(contents[3])
        assert service.capture(scope_id, force=True)["status"] == "RECORDED"
        assert service._usage(scope_id) <= quota
    finally:
        service.close()


def test_undo_operation_preview_survives_retention_without_exposing_content(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(state, _domains(state))
    try:
        scope_id, baseline, operation_id = _queued_restore(service, project)
        changed = service.scope(scope_id)["latest_id"]
        changed_entry = service.version(changed)["files"][0]
        assert service.execute(operation_id)["state"] == "SUCCEEDED"
        assert target.read_text(encoding="utf-8") == "baseline"
        target.write_text("unrelated current", encoding="utf-8")
        assert service.capture(scope_id, force=True)["status"] == "RECORDED"
        unrelated_preview = service.preview(scope_id, "restore_version", [changed_entry["file_id"]], version_id=baseline)
        unrelated = service.queue_restore(unrelated_preview["preview_id"], unrelated_preview["digest"], "unrelated-restore")
        assert service.execute(unrelated["id"])["state"] == "SUCCEEDED"
        assert target.read_text(encoding="utf-8") == "baseline"

        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_versions SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (changed,))
            conn.execute("UPDATE protection_operations SET created_at='2000-01-01T00:00:00+00:00' WHERE id IN (?,?)", (operation_id, unrelated["id"]))
            conn.execute("UPDATE protection_scopes SET last_collected_at=0 WHERE id=?", (scope_id,))
        service.configure(scope_id, "undo-retention", 1, enabled=True, retention_days=1,
                          quota_bytes=2 * 1024 * 1024 * 1024)
        file_id = changed_entry["file_id"]
        preview = service.preview(scope_id, "undo_operation", [file_id], operation_id=operation_id)

        assert preview["status"] == "READY"
        assert service.scope(scope_id)["last_collected_at"] > 0
        assert service.conn.execute(
            "SELECT 1 FROM protection_blob_refs WHERE owner_kind='operation' AND owner_id=?",
            (unrelated["id"],),
        ).fetchone() is None
        assert service.conn.execute(
            "SELECT 1 FROM protection_blob_refs WHERE owner_kind='operation' AND owner_id=?",
            (operation_id,),
        ).fetchone() is not None
        assert "content_b64" not in json.dumps(preview["selection"])
        saved_selection = service.conn.execute(
            "SELECT selection_json FROM protection_previews WHERE id=?", (preview["preview_id"],)
        ).fetchone()[0]
        assert "content_b64" not in saved_selection
        assert service.conn.execute(
            "SELECT 1 FROM protection_blob_refs WHERE owner_kind='preview' AND owner_id=? AND sha256=?",
            (preview["preview_id"], changed_entry["content_digest"]),
        ).fetchone()
        assert service.blobs.get(changed_entry["content_digest"]) == b"current"
        assert service.version(baseline)["available"] == 1
        # The source operation is protected only during this preview's preflight.
        # Its independent preview references take over before ordinary collection.
        service.collect(scope_id)
        assert service.conn.execute(
            "SELECT 1 FROM protection_blob_refs WHERE owner_kind='operation' AND owner_id=?",
            (operation_id,),
        ).fetchone() is None
        assert service.blobs.get(changed_entry["content_digest"]) == b"current"
        undo = service.queue_restore(preview["preview_id"], preview["digest"], "undo-after-source-retention")
        assert service.execute(undo["id"])["state"] == "SUCCEEDED"
        assert target.read_text(encoding="utf-8") == "current"
    finally:
        service.close()


def test_scope_and_batch_link_receipts_remain_counted_after_unlink(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(state, _domains(state))
    try:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        target.write_text("current", encoding="utf-8")
        assert service.capture(scope_id, force=True)["status"] == "RECORDED"
        batch_id = service.changes(scope_id)["items"][0]["id"]
        work_id = "00000000-0000-4000-8000-000000000016"
        with service.db.transaction() as conn:
            conn.execute(
                "INSERT INTO works(id,title,owner,revision,requirement_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (work_id, "Receipt ownership", "local", 1, 1, "2026-01-01", "2026-01-01"),
            )
        before = service.scope(scope_id)["metadata_bytes"]
        receipts = (
            ("link-scope", service.link(work_id, "scope", scope_id, None, "link-scope", 1)),
            ("link-batch", service.link(work_id, "batch", batch_id, None, "link-batch", 2)),
            ("unlink-batch", service.unlink(work_id, "batch", batch_id, None, "unlink-batch", 3)),
            ("unlink-scope", service.unlink(work_id, "scope", scope_id, None, "unlink-scope", 4)),
        )
        assert all(receipt["scope_id"] == scope_id for _, receipt in receipts)
        request_rows = service.conn.execute(
            "SELECT id,digest,response_json,scope_id FROM protection_requests WHERE id IN (?,?,?,?)",
            tuple(request_id for request_id, _ in receipts),
        ).fetchall()
        assert len(request_rows) == 4
        assert all(row[3] == scope_id for row in request_rows)
        assert all(json.loads(row[2])["scope_id"] == scope_id for row in request_rows)
        retained_request_bytes = sum(32 + sum(len(str(value).encode()) for value in row)
                                     for row in request_rows)
        assert service.scope(scope_id)["metadata_bytes"] - before == retained_request_bytes
    finally:
        service.close()


def test_reconcile_no_progress_keeps_journal_stable_and_reports_overquota(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("baseline", encoding="utf-8")
    domains = _domains(state)
    service = _service(state, domains)
    try:
        scope_id, _baseline, operation_id = _queued_restore(service, project)
        service._step_state(operation_id, "000000", "INTENT", {"state": "INTENT"})
        with service.db.transaction() as conn:
            conn.execute(
                "UPDATE protection_operations SET state='NEEDS_ATTENTION',reason_code='PROTECTION_RECONCILE_UNAVAILABLE' WHERE id=?",
                (operation_id,),
            )

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("offline")

        monkeypatch.setattr(domains, "reconcile", unavailable)

        def journal_counts():
            return (
                service.conn.execute("SELECT count(*) FROM protection_step_events WHERE operation_id=?", (operation_id,)).fetchone()[0],
                service.conn.execute("SELECT count(*) FROM evidence_ledger_events WHERE transaction_id=?", (operation_id,)).fetchone()[0],
            )

        before = journal_counts()
        assert before[0] > 0
        for _ in range(3):
            assert service.reconcile(operation_id)["state"] == "NEEDS_ATTENTION"
        assert journal_counts() == before
        assert service.operation(operation_id)["steps"][0]["state"] == "INTENT"

        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_scopes SET quota_bytes=1 WHERE id=?", (scope_id,))
        assert service.reconcile(operation_id)["state"] == "NEEDS_ATTENTION"
        assert journal_counts() == before
        scope = service.scope(scope_id)
        assert scope["status"] == "PAUSED"
        assert scope["reason_code"] == "PROTECTION_QUOTA_EXCEEDED"
        assert service.conn.execute(
            "SELECT detail_json FROM protection_step_events WHERE operation_id=? AND step_id='000000'",
            (operation_id,),
        ).fetchone()
    finally:
        service.close()


def test_reconcile_does_not_intervene_while_live_worker_holds_lease(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    domains = _domains(state)
    service = _service(state, domains)
    try:
        _scope_id, _baseline, operation_id = _queued_restore(service, project)
    finally:
        service.close()

    entered, release = threading.Event(), threading.Event()

    def pause_after_intent(point, _operation_id, _step_id=None):
        if point == "after_intent":
            entered.set()
            assert release.wait(5), "worker remained paused"

    runtime = ProtectionRuntime(state / "state.db", state, domains)

    @contextmanager
    def worker_service():
        worker = _service(state, domains, fault=pause_after_intent)
        # RLock is process-local. A separate Core has a distinct lock, while the
        # runtime still owns the actual OS worker lease in this test.
        worker.lock = threading.RLock()
        try:
            yield worker
        finally:
            worker.close()

    runtime.service = worker_service
    runtime.start()
    concurrent_state = None
    try:
        assert entered.wait(5), runtime.last_error
        with closing(_service(state, domains)) as observer:
            try:
                concurrent = observer.reconcile(operation_id)
            except ProtectionError:
                concurrent_state = "REJECTED"
            else:
                concurrent_state = concurrent["state"]
    finally:
        release.set()
        runtime.close()
    with closing(_service(state, domains)) as observer:
        assert observer.operation(operation_id)["state"] == "SUCCEEDED"
    assert concurrent_state in {"RUNNING", "REJECTED"}, concurrent_state
