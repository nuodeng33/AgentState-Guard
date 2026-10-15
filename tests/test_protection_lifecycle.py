"""Directory identity and background polling use real filesystem observations."""

import time
from contextlib import closing

import pytest

from agentguard.recovery.protection import (
    ProtectionError,
    ProtectionRuntime,
    ProtectionService,
)
from agentguard.recovery.protection_domains import DomainRegistry


def test_saved_disconnect_applies_before_first_startup_discovery(tmp_path):
    from agentguard.api.server import create_app

    events = []

    class Domains:
        def disconnect(self, source_id):
            events.append("disconnect")
            return {"source_id": source_id, "connected": False}

    class Discovery:
        def discover(self):
            events.append("discovery")
            assert events[0] == "disconnect"
            raise RuntimeError("fixture has no process records")

    state = tmp_path / "state"
    domains = Domains()
    with closing(ProtectionService(state / "state.db", state, domains)) as service:
        operation = service.source_action("docker-" + "a" * 24, "disconnect", "pause")
        assert service.execute_source(operation["id"])["state"] == "SUCCEEDED"
    events.clear()
    create_app(state_db_path=state / "state.db", config={"base_dir": str(state)},
        discovery_service=Discovery(), product_startup_discovery=True, protection_domains=domains)
    assert events == ["disconnect", "discovery"]


def test_replaced_directory_requires_explicit_new_registration(tmp_path):
    project, state = tmp_path / "project", tmp_path / "state"
    project.mkdir()
    (project / "a.txt").write_bytes(b"original")
    domains = DomainRegistry(state)
    with closing(ProtectionService(state / "state.db", state, domains)) as service:
        old = service.register("local", str(project), "first")["scope_id"]
        baseline = service.scope(old)["baseline_id"]
        file_id = service.version(baseline)["files"][0]["file_id"]
        project.rename(tmp_path / "original-project")
        project.mkdir()
        (project / "a.txt").write_bytes(b"unrelated replacement")
        assert service.capture(old, force=True)["status"] == "DEGRADED"
        with pytest.raises(ProtectionError, match="PROTECTION_ROOT_REPLACED"):
            service.register("local", str(project), "without-replacement")
        new = service.register("local", str(project), "replacement", replaces_scope_id=old)["scope_id"]
        assert new != old
        assert service.scope(old)["status"] == "RETIRED"
        assert service.file_content(old, baseline, file_id)["content_b64"] == "b3JpZ2luYWw="
        with pytest.raises(ProtectionError, match="PROTECTION_SCOPE_RETIRED"):
            service.preview(old, "restore_version", [file_id], version_id=baseline)
        assert (project / "a.txt").read_bytes() == b"unrelated replacement"
        assert service.register("local", str(project), "duplicate")["scope_id"] == new


def test_metadata_polling_still_forces_full_content_validation(tmp_path, monkeypatch):
    project, state = tmp_path / "project", tmp_path / "state"
    project.mkdir()
    target = project / "a.txt"
    target.write_bytes(b"old")
    domains = DomainRegistry(state)
    runtime = ProtectionRuntime(state / "state.db", state, domains)
    with runtime.service() as service:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        baseline = service.scope(scope_id)["baseline_id"]
        # Simulate a writer that preserves all metadata returned by a filesystem.
        monkeypatch.setattr(domains, "fingerprint", lambda binding: "a" * 64)
        runtime._poll_scope(service, scope_id)
        target.write_bytes(b"new")
        runtime._poll_scope(service, scope_id)
        assert service.scope(scope_id)["latest_id"] == baseline
        runtime.poll_state[scope_id]["full_at"] = time.monotonic() - 31
        runtime._poll_scope(service, scope_id)
        assert service.scope(scope_id)["status"] == "RECORDING"
        with service.db.transaction() as conn:
            conn.execute("UPDATE protection_scopes SET pending_since=? WHERE id=?", (time.time() - 3, scope_id))
        runtime._poll_scope(service, scope_id)
        latest = service.scope(scope_id)["latest_id"]
        assert latest != baseline
        version = service.version(latest)
        assert version["sample_started_at"] <= version["sample_completed_at"]
        file_id = version["files"][0]["file_id"]
        assert service.file_content(scope_id, latest, file_id)["content_b64"] == "bmV3"


def test_insufficient_domain_space_rejects_before_any_live_write(tmp_path, monkeypatch):
    project, state = tmp_path / "project", tmp_path / "state"
    project.mkdir()
    target = project / "a.txt"
    target.write_bytes(b"old")
    domains = DomainRegistry(state)
    with closing(ProtectionService(state / "state.db", state, domains)) as service:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        baseline = service.scope(scope_id)["baseline_id"]
        file_id = service.version(baseline)["files"][0]["file_id"]
        target.write_bytes(b"new")
        service.capture(scope_id, force=True)
        preview = service.preview(scope_id, "restore_version", [file_id], version_id=baseline)
        op = service.queue_restore(preview["preview_id"], preview["digest"], "restore")
        monkeypatch.setattr(domains, "available_bytes", lambda binding: 0)
        result = service.execute(op["id"])
        assert result["state"] == "REJECTED"
        assert result["reason_code"] == "PROTECTION_SPACE_INSUFFICIENT"
        assert target.read_bytes() == b"new"
        assert service.operation_events(op["id"])["items"] == []


def test_shared_scope_links_do_not_claim_authorship_and_can_be_unlinked(tmp_path):
    project, state = tmp_path / "project", tmp_path / "state"
    project.mkdir()
    target = project / "a.txt"
    target.write_bytes(b"old")
    domains = DomainRegistry(state)
    with closing(ProtectionService(state / "state.db", state, domains)) as service:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        for work_id in ("work-one", "work-two"):
            with service.db.transaction() as conn:
                conn.execute("INSERT INTO works(id,title,owner,revision,requirement_version,created_at,updated_at) VALUES (?,?,?,1,1,?,?)", (work_id, work_id, "local", "2026-01-01", "2026-01-01"))
            service.link(work_id, "scope", scope_id, None, "link-" + work_id, 1)
        target.write_bytes(b"new")
        service.capture(scope_id, force=True)
        for work_id in ("work-one", "work-two"):
            change = service.work_changes(work_id)["items"][0]
            assert change["attribution"] == "UNATTRIBUTED"
            assert change["association"] == "DIRECTORY_OBSERVATION"
        service.unlink("work-one", "scope", scope_id, None, "unlink", 2)
        assert service.work_changes("work-one")["items"] == []
        assert service.work_changes("work-two")["items"]
        assert len(service.scopes()["items"]) == 1


def test_core_storage_cannot_capture_itself(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name, allowed in (("state", False), (".agentguard", True)):
        state = project / name
        domains = DomainRegistry(state)
        with closing(ProtectionService(state / "state.db", state, domains)) as service:
            if allowed:
                assert service.register("local", str(project), "register")["scope_id"]
            else:
                with pytest.raises(ProtectionError, match="PROTECTION_CORE_STORAGE_OVERLAP"):
                    service.register("local", str(project), "register")
            with pytest.raises(ProtectionError, match="PROTECTION_CORE_STORAGE_OVERLAP"):
                service.register("local", str(state), "self")


def test_selected_external_edit_during_final_capture_is_not_success(tmp_path):
    project, state = tmp_path / "project", tmp_path / "state"
    project.mkdir()
    target = project / "a.txt"
    target.write_bytes(b"old")
    domains = DomainRegistry(state)

    def external_edit(point, operation_id, step_id=None):
        if point == "before_complete":
            target.write_bytes(b"external final edit")

    with closing(ProtectionService(state / "state.db", state, domains, fault=external_edit)) as service:
        scope_id = service.register("local", str(project), "register")["scope_id"]
        baseline = service.scope(scope_id)["baseline_id"]
        file_id = service.version(baseline)["files"][0]["file_id"]
        target.write_bytes(b"new")
        service.capture(scope_id, force=True)
        preview = service.preview(scope_id, "restore_version", [file_id], version_id=baseline)
        op = service.queue_restore(preview["preview_id"], preview["digest"], "restore")
        result = service.execute(op["id"])
        assert result["state"] == "NEEDS_ATTENTION"
        assert target.read_bytes() == b"external final edit"
