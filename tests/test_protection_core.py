"""Real native-directory contracts for continuous protection and recovery."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.recovery.protection import ProtectionError, ProtectionService
from agentguard.recovery.protection_domains import DomainRegistry


def _service(tmp_path, *, fault=None):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    domains = DomainRegistry(state)
    local = {
        "source_id": "local", "kind": "NATIVE", "label": "Local",
        "running": True, "connected": True, "epoch": f"native-{os.name}",
        "capabilities": {"scan": True, "apply": True},
    }
    domains._sources = {"local": local}
    domains.sources = lambda refresh=True: [local]
    return ProtectionService(state / "state.db", state, domains, fault=fault)


def _enroll(service, project, request_id="register-1"):
    result = service.register("local", str(project), request_id)
    scope_id = result["scope_id"]
    baseline = service.scope(scope_id)["baseline_id"]
    assert baseline
    return scope_id, baseline


def _file_ids(service, version_id):
    return {entry["relative_path"]: entry["file_id"] for entry in service.version(version_id)["files"]}


def _queue_version_restore(service, scope_id, version_id, file_id, request_id):
    preview = service.preview(scope_id, "restore_version", [file_id], version_id=version_id)
    assert preview["status"] == "READY"
    return service.queue_restore(preview["preview_id"], preview["digest"], request_id)


def test_selected_batch_restore_and_undo_leave_unselected_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    a, b, c = project / "a.txt", project / "b.txt", project / "c.txt"
    a.write_text("A before", encoding="utf-8")
    b.write_text("B before", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        a.write_text("A after", encoding="utf-8")
        b.unlink()
        c.write_text("C new", encoding="utf-8")
        recorded = service.capture(scope_id, force=True)
        assert recorded["status"] == "RECORDED"
        batch = service.changes(scope_id)["items"][0]
        assert {entry["kind"] for entry in batch["entries"]} == {"CREATED", "MODIFIED", "DELETED"}
        ids = _file_ids(service, baseline)
        preview = service.preview(scope_id, "undo_batch", [ids["a.txt"], ids["b.txt"]], batch_id=batch["id"])
        assert preview["status"] == "READY"
        queued = service.queue_restore(preview["preview_id"], preview["digest"], "restore-1")
        restored = service.execute(queued["id"])
        assert restored["state"] == "SUCCEEDED"
        assert a.read_text(encoding="utf-8") == "A before"
        assert b.read_text(encoding="utf-8") == "B before"
        assert c.read_text(encoding="utf-8") == "C new"

        undo = service.preview(scope_id, "undo_operation", [ids["a.txt"], ids["b.txt"]], operation_id=queued["id"])
        assert undo["status"] == "READY"
        undo_op = service.queue_restore(undo["preview_id"], undo["digest"], "undo-1")
        assert service.execute(undo_op["id"])["state"] == "SUCCEEDED"
        assert a.read_text(encoding="utf-8") == "A after"
        assert not b.exists()
        assert c.read_text(encoding="utf-8") == "C new"
    finally:
        service.close()


def test_preview_rejects_later_edit_and_old_batch_conflict(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        file_id = _file_ids(service, baseline)["a.txt"]
        target.write_text("first edit", encoding="utf-8")
        service.capture(scope_id, force=True)
        old_batch = service.changes(scope_id)["items"][0]["id"]
        preview = service.preview(scope_id, "undo_batch", [file_id], batch_id=old_batch)
        target.write_text("second edit", encoding="utf-8")
        queued = service.queue_restore(preview["preview_id"], preview["digest"], "stale-restore")
        assert service.execute(queued["id"])["state"] == "REJECTED"
        assert target.read_text(encoding="utf-8") == "second edit"
        service.capture(scope_id, force=True)
        conflict = service.preview(scope_id, "undo_batch", [file_id], batch_id=old_batch)
        assert conflict["status"] == "CONFLICT"
        assert conflict["conflicts"] == [file_id]
    finally:
        service.close()


def test_incomplete_scan_does_not_record_delete(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        target.unlink()
        original_scan = service.domains.scan

        def partial(binding):
            result = original_scan(binding)
            result["complete"] = False
            result["reason_code"] = "WORKSPACE_SCAN_ENTRY_LIMIT_REACHED"
            return result

        service.domains.scan = partial
        result = service.capture(scope_id, force=True)
        assert result["status"] == "DEGRADED"
        assert service.scope(scope_id)["latest_id"] == baseline
        assert service.changes(scope_id)["items"] == []
    finally:
        service.close()


@pytest.mark.parametrize("point", ["after_intent", "after_apply", "before_complete"])
def test_crash_reconcile_compensates_native_file(tmp_path, point):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")

    def crash(at, _op, _step=None):
        if at == point:
            raise SystemExit("simulated process crash")

    service = _service(tmp_path, fault=crash)
    scope_id, baseline = _enroll(service, project)
    file_id = _file_ids(service, baseline)["a.txt"]
    target.write_text("current", encoding="utf-8")
    service.capture(scope_id, force=True)
    queued = _queue_version_restore(service, scope_id, baseline, file_id, f"crash-{point}")
    with pytest.raises(SystemExit, match="simulated process crash"):
        service.execute(queued["id"])
    service.close()

    reopened = _service(tmp_path)
    try:
        result = reopened.reconcile(queued["id"])
        assert result["state"] == "COMPENSATED"
        assert target.read_text(encoding="utf-8") == "current"
    finally:
        reopened.close()


def test_runtime_failure_compensates_native_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")

    def fail(at, _op, _step=None):
        if at == "after_apply":
            raise RuntimeError("simulated worker failure")

    service = _service(tmp_path, fault=fail)
    try:
        scope_id, baseline = _enroll(service, project)
        file_id = _file_ids(service, baseline)["a.txt"]
        target.write_text("current", encoding="utf-8")
        service.capture(scope_id, force=True)
        queued = _queue_version_restore(service, scope_id, baseline, file_id, "runtime-failure")
        result = service.execute(queued["id"])
        assert result["state"] == "COMPENSATED"
        assert target.read_text(encoding="utf-8") == "current"
    finally:
        service.close()


def test_work_can_link_two_independent_scopes(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.txt").write_text("first", encoding="utf-8")
    (second / "b.txt").write_text("second", encoding="utf-8")
    service = _service(tmp_path)
    try:
        first_scope, _ = _enroll(service, first, "register-first")
        second_scope, _ = _enroll(service, second, "register-second")
        work_id = "00000000-0000-4000-8000-000000000001"
        with service.db.transaction() as conn:
            conn.execute(
                "INSERT INTO works(id,title,owner,revision,requirement_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (work_id, "Two projects", "local", 1, 1, "2026-01-01", "2026-01-01"),
            )
        service.link(work_id, "scope", first_scope, None, "link-first", 1)
        service.link(work_id, "scope", second_scope, None, "link-second", 2)
        links = service.links(work_id)["items"]
        assert {link["object_id"] for link in links} == {first_scope, second_scope}
        assert all(link["kind"] == "scope" for link in links)
    finally:
        service.close()


def test_native_protection_http_restore_flow(tmp_path):
    state = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    app = create_app(
        state_db_path=state / "state.db",
        config={"base_dir": str(state)},
        product_startup_discovery=False,
    )
    client = TestClient(app)
    headers = {"X-Session-Token": client.get("/api/session").json()["token"]}
    registered = client.post(
        "/api/v1/protection/scopes",
        json={"source_id": "local", "path": str(project), "request_id": "http-register"},
        headers=headers,
    )
    assert registered.status_code == 201, registered.text
    scope_id = registered.json()["scope_id"]
    versions = client.get(f"/api/v1/protection/scopes/{scope_id}/versions", headers=headers)
    assert versions.status_code == 200
    baseline = versions.json()["items"][0]
    file_id = baseline["files"][0]["file_id"]
    target.write_text("current", encoding="utf-8")
    preview = client.post(
        "/api/v1/recovery/previews",
        json={"scope_id": scope_id, "mode": "restore_version", "file_ids": [file_id], "version_id": baseline["id"]},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "READY"
    queued = client.post(
        "/api/v1/recovery/operations",
        json={"preview_id": preview.json()["preview_id"], "digest": preview.json()["digest"], "request_id": "http-restore"},
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    operation_id = queued.json()["id"]
    applied = client.post(
        f"/api/v1/recovery/operations/{operation_id}/reconcile", json={}, headers=headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "SUCCEEDED"
    assert target.read_text(encoding="utf-8") == "baseline"


def test_quota_rejection_preserves_baseline_and_records_no_change(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.bin"
    target.write_bytes(b"small baseline")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        original_refs = service.conn.execute(
            "SELECT COUNT(*) FROM protection_blob_refs WHERE scope_id=?", (scope_id,)
        ).fetchone()[0]
        service.configure(
            scope_id, "tight-quota", 1, enabled=True, retention_days=30,
            quota_bytes=service._usage(scope_id),
        )
        target.write_bytes(os.urandom(2048))
        rejected = service.capture(scope_id, force=True)
        assert rejected["status"] == "PAUSED"
        assert rejected["reason_code"] == "PROTECTION_QUOTA_EXCEEDED"
        scope = service.scope(scope_id)
        assert scope["baseline_id"] == baseline
        assert scope["latest_id"] == baseline
        assert len(service.versions(scope_id)["items"]) == 1
        assert service.changes(scope_id)["items"] == []
        assert service.conn.execute(
            "SELECT COUNT(*) FROM protection_blob_refs WHERE scope_id=?", (scope_id,)
        ).fetchone()[0] == original_refs
    finally:
        service.close()


def test_retention_preserves_pinned_current_baseline_and_active_operation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        ids = []
        for text in ("pinned", "expired", "latest"):
            target.write_text(text, encoding="utf-8")
            ids.append(service.capture(scope_id, force=True)["version_id"])
        pinned, expired, latest = ids
        service.pin(scope_id, pinned, True)
        file_id = _file_ids(service, baseline)["a.txt"]
        queued = _queue_version_restore(service, scope_id, baseline, file_id, "retention-pending")
        with service.db.transaction() as conn:
            conn.execute(
                "UPDATE protection_versions SET created_at='2000-01-01T00:00:00+00:00' WHERE id IN (?,?,?)",
                (baseline, pinned, expired),
            )
        service.configure(
            scope_id, "retention-one-day", 1, enabled=True,
            retention_days=1, quota_bytes=2 * 1024 * 1024 * 1024,
        )
        service.collect(scope_id)
        assert service.version(baseline)["available"] == 1
        assert service.version(pinned)["available"] == 1
        assert service.version(latest)["available"] == 1
        assert service.version(expired)["available"] == 0
        assert service.conn.execute(
            "SELECT COUNT(*) FROM protection_blob_refs WHERE owner_kind='operation' AND owner_id=?",
            (queued["id"],),
        ).fetchone()[0] > 0
        assert service.operation(queued["id"])["state"] == "QUEUED"
    finally:
        service.close()


def test_unselected_edit_during_restore_is_not_attributed_to_recovery(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    selected, unselected = project / "a.txt", project / "c.txt"
    selected.write_text("baseline A", encoding="utf-8")
    unselected.write_text("baseline C", encoding="utf-8")

    def edit_unselected(point, _operation_id, _step_id=None):
        if point == "before_complete":
            unselected.write_text("external C", encoding="utf-8")

    service = _service(tmp_path, fault=edit_unselected)
    try:
        scope_id, baseline = _enroll(service, project)
        file_id = _file_ids(service, baseline)["a.txt"]
        selected.write_text("current A", encoding="utf-8")
        service.capture(scope_id, force=True)
        queued = _queue_version_restore(service, scope_id, baseline, file_id, "attribution-restore")
        assert service.execute(queued["id"])["state"] == "SUCCEEDED"
        batch = service.changes(scope_id)["items"][0]
        entries = {entry["relative_path"]: entry for entry in batch["entries"]}
        assert entries["a.txt"]["attribution"] == "ASG_RECOVERY"
        assert entries["c.txt"]["attribution"] == "UNATTRIBUTED"
        assert batch["attribution"] == "UNATTRIBUTED"
        assert selected.read_text(encoding="utf-8") == "baseline A"
        assert unselected.read_text(encoding="utf-8") == "external C"
    finally:
        service.close()


def test_crash_after_inverse_apply_reconciles_to_compensated(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")

    def fail_then_crash(point, _operation_id, _step_id=None):
        if point == "after_apply":
            raise RuntimeError("trigger compensation")
        if point == "after_compensation":
            raise SystemExit("crash during compensation")

    service = _service(tmp_path, fault=fail_then_crash)
    scope_id, baseline = _enroll(service, project)
    file_id = _file_ids(service, baseline)["a.txt"]
    target.write_text("current", encoding="utf-8")
    service.capture(scope_id, force=True)
    queued = _queue_version_restore(service, scope_id, baseline, file_id, "inverse-crash")
    with pytest.raises(SystemExit, match="crash during compensation"):
        service.execute(queued["id"])
    service.close()

    reopened = _service(tmp_path)
    try:
        reconciled = reopened.reconcile(queued["id"])
        assert reconciled["state"] == "COMPENSATED"
        assert target.read_text(encoding="utf-8") == "current"
    finally:
        reopened.close()


def test_work_session_link_rejects_observation_older_than_120_seconds(tmp_path):
    service = _service(tmp_path)
    try:
        work_id = "00000000-0000-4000-8000-000000000002"
        observed = datetime.now(UTC)
        with service.db.transaction() as conn:
            conn.execute(
                "INSERT INTO works(id,title,owner,revision,requirement_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (work_id, "Session evidence", "local", 1, 1, observed.isoformat(), observed.isoformat()),
            )
            for session_id, age in (("stale", 121), ("current", 119)):
                conn.execute(
                    "INSERT INTO protection_sessions(id,source_id,provider,provider_session_id,metadata_json,observed_at) VALUES (?,?,?,?,?,?)",
                    (session_id, "local", "TEST", session_id, "{}", (observed - timedelta(seconds=age)).isoformat()),
                )
        with pytest.raises(ProtectionError, match="PROTECTION_SESSION_NOT_CURRENT"):
            service.link(work_id, "session", "stale", None, "link-stale", 1)
        assert service.conn.execute("SELECT revision FROM works WHERE id=?", (work_id,)).fetchone()[0] == 1
        assert service.links(work_id)["items"] == []
        linked = service.link(work_id, "session", "current", None, "link-current", 1)
        assert linked["revision"] == 2
        assert service.links(work_id)["items"][0]["object_id"] == "current"
    finally:
        service.close()
