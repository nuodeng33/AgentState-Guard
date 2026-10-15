"""Retention and query contracts for bounded protection metadata."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.recovery.protection import digest, encoded
from agentguard.storage.migrations import MigrationEngine
from tests.test_protection_core import (
    _enroll,
    _file_ids,
    _queue_version_restore,
    _service,
)


def _capture_text(service, scope_id: str, target: Path, text: str) -> str:
    target.write_text(text, encoding="utf-8")
    recorded = service.capture(scope_id, force=True)
    assert recorded["status"] == "RECORDED"
    return recorded["version_id"]


def _age_versions(service, *version_ids: str) -> None:
    with service.db.transaction() as conn:
        conn.executemany(
            "UPDATE protection_versions SET created_at='2000-01-01T00:00:00+00:00' "
            "WHERE id=?",
            [(version_id,) for version_id in version_ids],
        )


def _logical_row_bytes(values) -> int:
    return 32 + sum(len(str(value).encode("utf-8")) for value in values if value is not None)


def test_metadata_only_growth_pauses_capture_at_scope_quota(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        before = service.space(scope_id)
        assert before["quota_kind"] == "LOGICAL_RETAINED"
        assert before["metadata_bytes"] > 0
        assert before["logical_retained_bytes"] == sum(
            before[key] for key in ("content_bytes", "recovery_bytes", "metadata_bytes")
        )

        for index in range(40):
            (project / f"secret-{index:03}.txt").write_text(
                "API_KEY=sk-ant-test1234567890", encoding="utf-8"
            )
        manifest, _epoch = service._scan(service.scope(scope_id))
        assert len(manifest) == 41
        assert sum(entry["category"] == "audit_only" for entry in manifest.values()) == 40

        service.configure(
            scope_id,
            "metadata-quota",
            1,
            enabled=True,
            retention_days=30,
            quota_bytes=before["logical_retained_bytes"] + 512,
        )
        result = service.capture(scope_id, force=True)

        assert result["status"] == "PAUSED"
        assert result["reason_code"] == "PROTECTION_QUOTA_EXCEEDED"
        assert service.scope(scope_id)["latest_id"] == baseline
        assert len(service.versions(scope_id)["items"]) == 1
        assert service.space(scope_id)["content_bytes"] == before["content_bytes"]
    finally:
        service.close()


def test_expired_versions_keep_summary_and_work_link_without_file_details(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        first = _capture_text(service, scope_id, target, "first")
        second = _capture_text(service, scope_id, target, "second")
        pinned = _capture_text(service, scope_id, target, "pinned")
        latest = _capture_text(service, scope_id, target, "latest")
        service.pin(scope_id, pinned, True)
        original = service.version(first)
        batch = next(
            item
            for item in service.changes(scope_id)["items"]
            if item["before_id"] == first and item["after_id"] == second
        )
        assert batch["entries"]

        work_id = "00000000-0000-4000-8000-000000000015"
        with service.db.transaction() as conn:
            conn.execute(
                "INSERT INTO works(id,title,owner,revision,requirement_version,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (work_id, "Retention link", "local", 1, 1, "2026-01-01", "2026-01-01"),
            )
            conn.execute(
                "UPDATE protection_changes SET created_at='2000-01-01T00:00:00+00:00' "
                "WHERE id=?",
                (batch["id"],),
            )
        service.link(work_id, "scope", scope_id, None, "link-scope", 1)
        service.link(work_id, "batch", batch["id"], None, "link-batch", 2)
        _age_versions(service, baseline, first, second, pinned)
        service.configure(
            scope_id,
            "short-retention",
            1,
            enabled=True,
            retention_days=1,
            quota_bytes=2 * 1024 * 1024 * 1024,
        )
        service.collect(scope_id)

        expired = service.version(first)
        assert expired["available"] == 0
        assert expired["metadata_available"] == 0
        assert expired["history_status"] == "DETAILS_EXPIRED"
        assert expired["details_included"] is False
        assert expired["files"] == []
        assert expired["digest"] == original["digest"]
        assert expired["coverage"] == original["coverage"]
        row = service.conn.execute(
            "SELECT manifest_json,summary_json FROM protection_versions WHERE id=?",
            (first,),
        ).fetchone()
        assert row[0] == "{}"
        assert json.loads(row[1])["file_count"] == 1

        for version_id in (baseline, pinned, latest):
            retained = service.version(version_id)
            assert retained["metadata_available"] == 1
            assert retained["history_status"] == "FULL"
            assert retained["files"]

        summarized = next(
            item for item in service.changes(scope_id)["items"] if item["id"] == batch["id"]
        )
        assert summarized["metadata_available"] == 0
        assert summarized["history_status"] == "DETAILS_EXPIRED"
        assert summarized["details_included"] is False
        assert summarized["entries"] == []
        assert service.conn.execute(
            "SELECT entries_json FROM protection_changes WHERE id=?", (batch["id"],)
        ).fetchone()[0] == "[]"
        linked = next(
            item
            for item in service.work_changes(work_id)["items"]
            if item["id"] == batch["id"]
        )
        assert linked["association"] == "USER_LINKED"
        assert linked["history_status"] == "DETAILS_EXPIRED"
    finally:
        service.close()


def test_active_preview_and_queued_operation_preserve_old_details(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        preview_target = _capture_text(service, scope_id, target, "preview target")
        operation_target = _capture_text(service, scope_id, target, "operation target")
        _capture_text(service, scope_id, target, "latest")
        file_id = _file_ids(service, baseline)["a.txt"]
        preview = service.preview(
            scope_id, "restore_version", [file_id], version_id=preview_target
        )
        assert preview["status"] == "READY"
        operation = _queue_version_restore(
            service, scope_id, operation_target, file_id, "queued-retention"
        )
        assert operation["state"] == "QUEUED"
        _age_versions(service, preview_target, operation_target)
        service.configure(
            scope_id,
            "protected-retention",
            1,
            enabled=True,
            retention_days=1,
            quota_bytes=2 * 1024 * 1024 * 1024,
        )
        service.collect(scope_id)

        for version_id in (preview_target, operation_target):
            retained = service.version(version_id)
            assert retained["available"] == 1
            assert retained["metadata_available"] == 1
            assert retained["history_status"] == "FULL"
            assert retained["files"]
    finally:
        service.close()


def test_large_version_summary_and_file_history_do_not_expand_all_manifests(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    service = _service(tmp_path)
    try:
        scope_id, _baseline = _enroll(service, project)
        file_id = "file-00000"
        first_manifest = {
            f"file-{index:05}": {
                "file_id": f"file-{index:05}",
                "relative_path": f"file-{index:05}.txt",
                "object_kind": "file",
                "category": "audit_only",
                "reason_code": "WORKSPACE_SENSITIVE_CONTENT_AUDIT_ONLY",
                "content_digest": None,
                "permission_proof": None,
                "size": 1,
            }
            for index in range(10_000)
        }
        second_manifest = {
            **first_manifest,
            file_id: {**first_manifest[file_id], "size": 2},
        }
        epoch = json.loads(service.scope(scope_id)["binding_json"])["epoch"]
        observed = [first_manifest]
        monkeypatch.setattr(service, "_scan", lambda _scope: (observed[0], epoch))
        first = service.capture(scope_id, force=True)
        assert first["status"] == "RECORDED"
        observed[0] = second_manifest
        second = service.capture(scope_id, force=True)
        assert second["status"] == "RECORDED"

        def no_full_version(*_args, **_kwargs):
            raise AssertionError("summary query expanded a full version")

        monkeypatch.setattr(service, "version", no_full_version)
        monkeypatch.setattr(service, "_manifest", no_full_version)
        statements: list[str] = []
        service.conn.set_trace_callback(statements.append)
        try:
            items = service.versions(scope_id, limit=2, view="summary")["items"]
        finally:
            service.conn.set_trace_callback(None)
        assert [item["id"] for item in items] == [second["version_id"], first["version_id"]]
        assert all(item["files"] == [] for item in items)
        assert all(item["details_included"] is False for item in items)
        assert all(item["metadata_available"] == 1 for item in items)
        assert all(item["coverage"]["audit_only"] == 10_000 for item in items)
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        assert len(selects) <= 4
        assert all("manifest_json" not in sql.casefold() for sql in selects)
        assert all("select * from protection_versions" not in sql.casefold() for sql in selects)

        monkeypatch.setattr(service, "versions", no_full_version)
        statements.clear()
        service.conn.set_trace_callback(statements.append)
        try:
            history = service.file_history(scope_id, file_id, limit=3)["items"]
        finally:
            service.conn.set_trace_callback(None)
        assert [item["version_id"] for item in history[:2]] == [
            second["version_id"],
            first["version_id"],
        ]
        assert [item["file"]["size"] for item in history[:2]] == [2, 1]
        assert history[2]["file"] is None
        history_selects = [
            sql for sql in statements if sql.lstrip().upper().startswith("SELECT")
        ]
        assert len(history_selects) <= 4
        assert any("json_extract" in sql.casefold() for sql in history_selects)
    finally:
        service.close()


def test_maybe_collect_interval_survives_service_reopen(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("baseline", encoding="utf-8")
    first = _service(tmp_path)
    try:
        scope_id, _baseline = _enroll(first, project)
        first.collect(scope_id)
    finally:
        first.close()

    reopened = _service(tmp_path)
    try:
        calls = []
        original_collect = reopened.collect

        def counted_collect(*args, **kwargs):
            calls.append((args, kwargs))
            return original_collect(*args, **kwargs)

        monkeypatch.setattr(reopened, "collect", counted_collect)
        reopened.maybe_collect(scope_id)
        assert calls == []
    finally:
        reopened.close()


def test_operations_summary_batches_queries_and_full_view_retains_steps(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        target.write_text("current", encoding="utf-8")
        assert service.capture(scope_id, force=True)["status"] == "RECORDED"
        file_id = _file_ids(service, baseline)["a.txt"]
        queued = _queue_version_restore(
            service, scope_id, baseline, file_id, "operation-list-restore"
        )
        for index in range(12):
            service.source_action("local", "disconnect", f"operation-list-{index}")

        def no_single_operation(*_args, **_kwargs):
            raise AssertionError("operation list performed a per-item lookup")

        monkeypatch.setattr(service, "operation", no_single_operation)
        statements: list[str] = []
        service.conn.set_trace_callback(statements.append)
        try:
            summary = service.operations(limit=20, view="summary")["items"]
        finally:
            service.conn.set_trace_callback(None)
        assert len(summary) == 13
        selected = next(item for item in summary if item["id"] == queued["id"])
        assert selected["step_count"] == 1
        assert selected["steps"] == []
        assert selected["evidence_refs"] == []
        assert selected["details_included"] is False
        assert selected["metadata_available"] is True
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        assert len(selects) <= 2
        assert all("protection_step_events" not in sql.casefold() for sql in selects)
        assert all("select * from protection_steps" not in sql.casefold() for sql in selects)

        statements.clear()
        service.conn.set_trace_callback(statements.append)
        try:
            full = service.operations(limit=20, view="full")["items"]
        finally:
            service.conn.set_trace_callback(None)
        selected_full = next(item for item in full if item["id"] == queued["id"])
        assert selected_full["details_included"] is True
        assert len(selected_full["steps"]) == 1
        assert selected_full["steps"][0]["step_id"] == "000000"
        assert selected_full["evidence_refs"]
        full_selects = [
            sql for sql in statements if sql.lstrip().upper().startswith("SELECT")
        ]
        assert len(full_selects) <= 4
    finally:
        service.close()


def test_http_summary_views_keep_list_payloads_bounded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    state = tmp_path / "state"
    app = create_app(
        state_db_path=state / "state.db",
        config={"base_dir": str(state)},
        product_startup_discovery=False,
    )
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        registered = client.post(
            "/api/v1/protection/scopes",
            json={"source_id": "local", "path": str(project), "request_id": "summary-http"},
        )
        assert registered.status_code == 201, registered.text
        scope_id = registered.json()["scope_id"]
        base = f"/api/v1/protection/scopes/{scope_id}"
        baseline = client.get(base + "/versions").json()["items"][0]
        file_id = baseline["files"][0]["file_id"]
        target.write_text("current", encoding="utf-8")
        captured = client.post(base + "/capture", json={})
        assert captured.status_code == 200, captured.text
        assert captured.json()["status"] == "RECORDED"

        versions = client.get(base + "/versions", params={"view": "summary"})
        changes = client.get(base + "/changes", params={"view": "summary"})
        assert versions.status_code == changes.status_code == 200
        assert len(versions.json()["items"]) == 2
        assert all(item["files"] == [] for item in versions.json()["items"])
        assert all(
            item["details_included"] is False for item in versions.json()["items"]
        )
        assert changes.json()["items"][0]["entries"] == []
        assert changes.json()["items"][0]["change_count"] == 1
        assert changes.json()["items"][0]["details_included"] is False
        assert client.get(base + "/versions", params={"view": "invalid"}).status_code == 422

        preview = client.post(
            "/api/v1/recovery/previews",
            json={
                "scope_id": scope_id,
                "mode": "restore_version",
                "file_ids": [file_id],
                "version_id": baseline["id"],
            },
        )
        assert preview.status_code == 200, preview.text
        queued = client.post(
            "/api/v1/recovery/operations",
            json={
                "preview_id": preview.json()["preview_id"],
                "digest": preview.json()["digest"],
                "request_id": "summary-http-restore",
            },
        )
        assert queued.status_code == 202, queued.text
        operations = client.get("/api/v1/recovery/operations", params={"view": "summary"})
        assert operations.status_code == 200, operations.text
        operation = next(
            item for item in operations.json()["items"] if item["id"] == queued.json()["id"]
        )
        assert operation["steps"] == []
        assert operation["step_count"] == 1
        assert operation["details_included"] is False
        assert client.get(
            "/api/v1/recovery/operations", params={"view": "invalid"}
        ).status_code == 422


def test_candidates_pages_observed_session_metadata(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        with service.db.transaction() as conn:
            for index in range(5):
                conn.execute(
                    "INSERT INTO protection_sessions(id,source_id,provider,provider_session_id,metadata_json,observed_at) VALUES (?,?,?,?,?,?)",
                    (
                        f"session-{index}",
                        "local",
                        "Codex",
                        f"provider-{index}",
                        json.dumps({"cwd": f"/project-{index}"}),
                        f"2026-01-0{index + 1}T00:00:00+00:00",
                    ),
                )
            conn.execute(
                "INSERT INTO protection_sessions(id,source_id,provider,provider_session_id,metadata_json,observed_at) VALUES (?,?,?,?,?,?)",
                ("without-cwd", "local", "Codex", "provider-empty", "{}", "2026-01-09T00:00:00+00:00"),
            )

        statements: list[str] = []
        service.conn.set_trace_callback(statements.append)
        try:
            first = service.candidates(limit=2)
        finally:
            service.conn.set_trace_callback(None)
        second = service.candidates(limit=2, offset=2)
        third = service.candidates(limit=2, offset=4)
        assert [item["session_id"] for item in first["items"]] == [
            "session-4", "session-3"
        ]
        assert [item["session_id"] for item in second["items"]] == [
            "session-2", "session-1"
        ]
        assert [item["session_id"] for item in third["items"]] == ["session-0"]
        assert [first["has_more"], second["has_more"], third["has_more"]] == [
            True, True, False
        ]
        assert (first["limit"], second["offset"], third["offset"]) == (2, 2, 4)
        assert all(item["registration_required"] for item in first["items"])
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 1
        assert "limit 3 offset 0" in selects[0].casefold()
        assert "select *" not in selects[0].casefold()
    finally:
        service.close()


def test_metadata_triggers_count_utf8_preview_and_recovery_journal_atomically(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("baseline", encoding="utf-8")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        metadata = lambda: service.scope(scope_id)["metadata_bytes"]
        original = service.conn.execute(
            "SELECT summary_json FROM protection_versions WHERE id=?", (baseline,)
        ).fetchone()[0]
        replacement = json.dumps({"note": "雪é"}, ensure_ascii=False)
        before = metadata()
        with service.db.transaction() as conn:
            conn.execute(
                "UPDATE protection_versions SET summary_json=? WHERE id=?",
                (replacement, baseline),
            )
        assert metadata() - before == len(replacement.encode()) - len(original.encode())
        after = metadata()
        with pytest.raises(RuntimeError, match="rollback"), service.db.transaction() as conn:
            conn.execute(
                "UPDATE protection_versions SET summary_json=? WHERE id=?",
                ("{}", baseline),
            )
            assert metadata() != after
            raise RuntimeError("rollback")
        assert metadata() == after
        assert service.conn.execute(
            "SELECT summary_json FROM protection_versions WHERE id=?", (baseline,)
        ).fetchone()[0] == replacement

        target.write_text("current", encoding="utf-8")
        assert service.capture(scope_id, force=True)["status"] == "RECORDED"
        file_id = _file_ids(service, baseline)["a.txt"]
        before_preview = metadata()
        preview = service.preview(
            scope_id, "restore_version", [file_id], version_id=baseline
        )
        assert preview["status"] == "READY"
        assert metadata() > before_preview
        preview_id = preview["preview_id"]
        ready_bytes = metadata()
        with service.db.transaction() as conn:
            conn.execute(
                "UPDATE protection_previews SET status='EXPIRED' WHERE id=?",
                (preview_id,),
            )
        assert metadata() == ready_bytes + len("EXPIRED") - len("READY")
        with service.db.transaction() as conn:
            conn.execute(
                "UPDATE protection_previews SET status='READY' WHERE id=?",
                (preview_id,),
            )
        assert metadata() == ready_bytes

        queued = service.queue_restore(preview_id, preview["digest"], "journal-accounting")
        operation_id = queued["id"]
        old_step = service.conn.execute(
            "SELECT * FROM protection_steps WHERE operation_id=? AND step_id='000000'",
            (operation_id,),
        ).fetchone()
        before_event = metadata()
        service._step_state(operation_id, "000000", "INTENT", {"state": "INTENT"})
        new_step = service.conn.execute(
            "SELECT * FROM protection_steps WHERE operation_id=? AND step_id='000000'",
            (operation_id,),
        ).fetchone()
        event = service.conn.execute(
            "SELECT * FROM protection_step_events WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert event is not None
        assert metadata() - before_event == (
            _logical_row_bytes(new_step)
            - _logical_row_bytes(old_step)
            + _logical_row_bytes(event)
        )
    finally:
        service.close()


def test_v15_nonempty_history_migrates_to_v16_without_rewriting_facts(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    db_path = state / "state.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, "
            "applied_at TEXT, checksum TEXT, duration_ms INTEGER)"
        )
        conn.commit()
        engine = MigrationEngine(db_path)
        migrated = engine.migrate(conn, target=15)
        assert migrated["errors"] == []
        assert engine.current_version(conn) == 15
        v15_checksum = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version=15"
        ).fetchone()[0]

        scope_id, first_id, second_id, batch_id = (
            "scope-legacy", "version-legacy-1", "version-legacy-2", "batch-legacy"
        )
        first_manifest = {
            "file-a": {
                "file_id": "file-a", "relative_path": "a.txt",
                "category": "audit_only", "size": 1,
            }
        }
        second_manifest = {
            **first_manifest,
            "file-b": {
                "file_id": "file-b", "relative_path": "b.txt",
                "category": "excluded", "size": 0,
            },
        }
        entries = [{"file_id": "file-b", "kind": "CREATED"}]
        timestamp = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO protection_scopes(id,source_id,root_key,identity_digest,"
            "binding_json,status,reason_code,observed_at,baseline_id,latest_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                scope_id, "local", str(tmp_path / "project"), "legacy-identity",
                encoded({"root": str(tmp_path / "project"), "kind": "NATIVE"}),
                "PROTECTED", "PROTECTION_VERSION_SAVED", timestamp,
                first_id, second_id, timestamp,
            ),
        )
        conn.executemany(
            "INSERT INTO protection_versions(id,scope_id,parent_id,created_at,"
            "manifest_json,digest,pinned,available,kind,operation_id,"
            "sample_started_at,sample_completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (first_id, scope_id, None, timestamp, encoded(first_manifest),
                 digest(first_manifest), 0, 0, "BASELINE", None, timestamp, timestamp),
                (second_id, scope_id, first_id, "2026-01-02T00:00:00+00:00",
                 encoded(second_manifest), digest(second_manifest), 0, 1,
                 "CAPTURE", None, timestamp, timestamp),
            ],
        )
        conn.execute(
            "INSERT INTO protection_changes(id,scope_id,before_id,after_id,"
            "created_at,attribution,operation_id,entries_json) VALUES (?,?,?,?,?,?,?,?)",
            (batch_id, scope_id, first_id, second_id, timestamp,
             "UNATTRIBUTED", None, encoded(entries)),
        )
        conn.commit()
        original_versions = conn.execute(
            "SELECT id,manifest_json,digest,available FROM protection_versions ORDER BY id"
        ).fetchall()
        original_batch = conn.execute(
            "SELECT id,entries_json FROM protection_changes"
        ).fetchone()

        upgraded = engine.migrate(conn, target=16)
        assert upgraded["errors"] == []
        assert len(upgraded["applied"]) == 1
        assert engine.current_version(conn) == 16
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version=15"
        ).fetchone()[0] == v15_checksum
        assert conn.execute(
            "SELECT id,manifest_json,digest,available FROM protection_versions ORDER BY id"
        ).fetchall() == original_versions
        assert conn.execute(
            "SELECT id,entries_json FROM protection_changes"
        ).fetchone() == original_batch

        versions = conn.execute(
            "SELECT id,summary_json,metadata_available FROM protection_versions ORDER BY id"
        ).fetchall()
        assert [item[2] for item in versions] == [1, 1]
        assert json.loads(versions[0][1]) == {
            "coverage": {"restorable": 0, "excluded": 0, "audit_only": 1,
                         "unreachable": 0},
            "file_count": 1,
            "change_count": 0,
        }
        assert json.loads(versions[1][1]) == {
            "coverage": {"restorable": 0, "excluded": 1, "audit_only": 1,
                         "unreachable": 0},
            "file_count": 2,
            "change_count": 1,
        }
        change = conn.execute(
            "SELECT summary_json,metadata_available FROM protection_changes WHERE id=?",
            (batch_id,),
        ).fetchone()
        assert json.loads(change[0]) == {"change_count": 1}
        assert change[1] == 1
        assert conn.execute(
            "SELECT metadata_bytes FROM protection_scopes WHERE id=?", (scope_id,)
        ).fetchone()[0] > 0
    finally:
        conn.close()

    service = _service(tmp_path)
    try:
        space = service.space(scope_id)
        assert space["metadata_bytes"] > 0
        assert space["logical_retained_bytes"] == sum(
            space[key] for key in ("content_bytes", "recovery_bytes", "metadata_bytes")
        )
        assert service.version(first_id)["history_status"] == "CONTENT_EXPIRED"
        assert service.version(second_id)["file_count"] == 2
    finally:
        service.close()
