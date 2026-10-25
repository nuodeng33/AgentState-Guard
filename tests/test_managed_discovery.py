"""Real product composition: mixed domains, coverage, freshness and source control."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.api.server import create_app
from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.managed import ManagedDiscoveryService
from agentguard.discovery.product import ProductDiscoveryService
from agentguard.discovery.source_settings import SourceSettings
from agentguard.evidence.discovery_adapter import record_discovery_snapshot
from agentguard.storage.db import StateDB
from tests.test_product_discovery import (
    NOW,
    _ProcessBackend,
    _ProcessHandle,
    _RuntimeAdapter,
)


class DockerProbe:
    def __init__(self):
        self.targets = (
            SimpleNamespace(container_id="a" * 64, name="agent-k3", status="Up"),
            SimpleNamespace(container_id="b" * 64, name="agent-codex-b", status="Up"),
        )
        self.calls = []
        self.status = CapabilityStatus.AVAILABLE
        self.processes = ({"pid": 42, "ppid": 1, "name": "kimi-code", "start_ticks": 100,
                           "cwd": "/private/project"},)

    def list_targets(self):
        return SimpleNamespace(status=CapabilityStatus.AVAILABLE if self.targets else CapabilityStatus.NOT_PRESENT,
                               reason_code="DOCKER_TARGETS_OBSERVED" if self.targets else "DOCKER_TARGETS_EMPTY", targets=self.targets)

    def probe_target(self, target):
        self.calls.append(target.container_id)
        return SimpleNamespace(target=target, observed_at=NOW, boot_id="boot-1", status=self.status,
                               reason_code="CONTAINER_PROCESSES_OBSERVED",
                               processes=self.processes if target == self.targets[0] else (
                                   {"pid": 44, "ppid": 1, "name": "bash", "start_ticks": 20},))


class DockerDisconnectRegistry:
    def __init__(self):
        self.disconnected = set()

    def sources(self, refresh=True):
        return []

    def is_docker_disconnected(self, source_id):
        return source_id in self.disconnected


def service(*, preferences=None):
    return ManagedDiscoveryService(
        native=ProductDiscoveryService(
            runtime_adapter=_RuntimeAdapter(),
            process_backend=_ProcessBackend((_ProcessHandle(102, "codex.exe"),)),
            clock=lambda: NOW,
        ), docker=DockerProbe(), preferences=preferences, clock=lambda: NOW,
    )


def projection(tmp_path, report, *, now=NOW):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    try:
        record_discovery_snapshot(database, report.snapshot, recorded_at=now)
        projector = R4ReadProjectionService(database, None, clock=lambda: now)
        return projector.agents(), projector.runtime()
    finally:
        database.close()


def test_mixed_domains_are_projected_together_without_foreign_recovery_authority(tmp_path):
    managed = service()
    report = managed.discover_with_authority()
    agents, runtimes = projection(tmp_path, report)
    assert {item["detected_identity"] for item in agents["items"]} == {"CODEX", "KIMI_CODE"}
    assert len(runtimes["items"]) == 3
    kimi = next(item for item in agents["items"] if item["detected_identity"] == "KIMI_CODE")
    assert kimi["workspace"]["status"] == "BOUND"
    assert kimi["control_status"] == "NOT_CONNECTED"
    assert kimi["identity_basis"] == "PROCESS_NAME_MATCH"
    assert all(item.execution_domain_id == "windows-current" for item in report.workspace_authorities)
    assert "/private/project" not in str(report.snapshot.to_dict())
    empty_container = next(item for item in managed.sources()["items"] if item["label"] == "agent-codex-b")
    assert empty_container["state"] == "EMPTY"
    assert empty_container["agent_count"] == 0
    assert next(item for item in managed.sources()["items"] if item["source_id"] == "docker")["agent_count"] == 1


def test_partial_container_scan_keeps_positive_facts_but_marks_projection_degraded(tmp_path):
    managed = service()
    managed.docker.status = CapabilityStatus.DEGRADED
    agents, _ = projection(tmp_path, managed.discover_with_authority())
    assert agents["status"] == "DEGRADED"
    assert len(agents["items"]) == 2
    assert agents["reason_code"] == "R4_AGENT_DISCOVERY_PARTIAL"


def test_stale_observation_does_not_imply_current_process_or_control(tmp_path):
    agents, runtimes = projection(tmp_path, service().discover_with_authority(), now=NOW + timedelta(seconds=121))
    assert all(item["presence"] == "STALE" for item in agents["items"])
    assert all(item["lifecycle"] == "UNKNOWN" for item in agents["items"])
    assert all(not item["management_capabilities"]["observe"] for item in agents["items"])
    assert all(item["availability"] == "UNKNOWN" for item in runtimes["items"])


def test_process_restart_gets_new_instance_and_preserves_other_domain(tmp_path):
    managed = service()
    first, _ = projection(tmp_path, managed.discover_with_authority())
    managed.docker.processes = ({**managed.docker.processes[0], "start_ticks": 200},)
    second, _ = projection(tmp_path, managed.discover_with_authority())
    by_kind = lambda items: {item["detected_identity"]: item["agent_id"] for item in items}
    before, after = by_kind(first["items"]), by_kind(second["items"])
    assert before["KIMI_CODE"] != after["KIMI_CODE"]
    assert before["CODEX"] == after["CODEX"]


def test_source_control_is_authenticated_revision_checked_and_survives_restart(tmp_path):
    settings = SourceSettings(tmp_path / "state.db")
    managed = service(preferences=settings.read)
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     discovery_service=managed, product_startup_discovery=True)
    with TestClient(app) as client:
        assert app.state.protection.initial_discovery_complete.wait(5)
        assert client.get("/api/v1/discovery/sources").status_code == 401
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        sources = client.get("/api/v1/discovery/sources").json()["items"]
        kimi = next(item for item in sources if item["label"] == "agent-k3")
        path = f'/api/v1/discovery/sources/{kimi["source_id"]}/configure'
        result = client.post(path, json={"enabled": False, "expected_revision": 0})
        assert result.status_code == 200, result.text
        assert result.json()["revision"] == 1
        assert client.post(path, json={"enabled": True, "expected_revision": 0}).status_code == 409
        assert client.post(path, json={"enabled": True, "expected_revision": 1,
                                       "container_id": "arbitrary"}).status_code == 422
        assert client.post('/api/v1/discovery/sources/unknown/configure',
                           json={"enabled": True, "expected_revision": 0}).status_code == 404
    restarted = service(preferences=settings.read)
    report = restarted.discover_with_authority()
    assert [item.agent_type for item in report.snapshot.agents] == ["CODEX"]
    assert "a" * 64 not in restarted.docker.calls
    assert next(item for item in restarted.sources()["items"] if item["label"] == "agent-k3")["state"] == "DISABLED"


def test_explicit_docker_disconnect_stops_process_and_session_observation():
    registry = DockerDisconnectRegistry()
    managed = service()
    managed.domain_registry = registry
    assert any(agent.agent_type == "KIMI_CODE" for agent in managed.discover().agents)
    source_id = next(item["source_id"] for item in managed.sources()["items"]
                     if item["label"] == "agent-k3")
    managed.docker.session_transport = lambda target: ("C:/docker.exe", {})
    registry.disconnected.add(source_id)

    reader = SessionReaderFake()
    assert managed.read_sessions(source_id, reader=reader)["reason_code"] == "DOMAIN_DISCONNECTED"
    assert reader.calls == []
    disconnected = next(item for item in managed.sources()["items"] if item["source_id"] == source_id)
    assert disconnected["state"] == "NOT_PROBED"
    assert disconnected["capabilities"]["observe"] is False
    assert disconnected["session_probe_available"] is False

    previous_calls = len(managed.docker.calls)
    assert all(agent.agent_type != "KIMI_CODE" for agent in managed.discover().agents)
    assert "a" * 64 not in managed.docker.calls[previous_calls:]
    registry.disconnected.remove(source_id)
    assert any(agent.agent_type == "KIMI_CODE" for agent in managed.discover().agents)


def test_docker_observation_preference_does_not_disconnect_protection():
    preferences = {}
    registry = DockerDisconnectRegistry()
    managed = service(preferences=lambda: preferences)
    managed.domain_registry = registry
    managed.discover()
    source_id = next(item["source_id"] for item in managed.sources()["items"]
                     if item["label"] == "agent-k3")
    preferences[source_id] = {"enabled": False}
    previous_calls = len(managed.docker.calls)
    managed.discover()
    assert "a" * 64 not in managed.docker.calls[previous_calls:]
    assert registry.is_docker_disconnected(source_id) is False
    assert next(item for item in managed.sources()["items"] if item["source_id"] == source_id)["state"] == "DISABLED"
    preferences[source_id] = {"enabled": True}
    assert any(agent.agent_type == "KIMI_CODE" for agent in managed.discover().agents)


def test_empty_and_unreachable_remain_distinct():
    managed = service()
    managed.docker.status = CapabilityStatus.UNREACHABLE
    report = managed.discover_with_authority()
    assert [item.agent_type for item in report.snapshot.agents] == ["CODEX"]
    assert next(item for item in managed.sources()["items"] if item["label"] == "agent-k3")["state"] == "UNREACHABLE"
    managed.docker.targets = ()
    managed.discover()
    assert next(item for item in managed.sources()["items"] if item["source_id"] == "docker")["state"] == "EMPTY"


class SessionReaderFake:
    def __init__(self, status="AVAILABLE"):
        self.calls = []
        self.status = status

    def read(self, provider, **kwargs):
        self.calls.append((provider, kwargs))
        return {"status": self.status, "reason_code": "SESSION_LIST_AVAILABLE",
                "identity_basis": "PROTOCOL_HANDSHAKE", "sessions": []}


def test_provider_store_read_uses_pinned_transport_without_promoting_agent_control(tmp_path):
    managed = service()
    report = managed.discover_with_authority()
    source = next(item for item in managed.sources()["items"] if item["kind"] == "CONTAINER")
    environment = {"DOCKER_HOST": "npipe:////./pipe/docker_engine"}
    managed.docker.session_transport = lambda target: ("C:/docker.exe", environment)
    reader = SessionReaderFake(status="EMPTY")
    result = managed.read_sessions(source["source_id"], reader=reader)
    assert result["scope"] == "PROVIDER_STORE"
    assert reader.calls == [("KIMI_CODE", {"container_id": "a" * 64,
                                         "docker_executable": "C:/docker.exe",
                                         "environment": environment})]
    connected = next(item for item in managed.sources()["items"] if item["source_id"] == source["source_id"])
    assert connected["capabilities"]["sessions"] is True
    agents, _ = projection(tmp_path, report)
    assert all(item["control_status"] == "NOT_CONNECTED" for item in agents["items"])
    assert all(item["association"] is None for item in agents["items"])
    assert all(not item["management_capabilities"]["start"] for item in agents["items"])
    managed.discover()
    refreshed = next(item for item in managed.sources()["items"] if item["source_id"] == source["source_id"])
    assert refreshed["capabilities"]["sessions"] is False


@pytest.mark.parametrize("state", ["missing", "disabled", "stale", "unreachable", "unsupported", "changed"])
def test_session_read_rejects_unusable_sources_without_calling_protocol(state):
    preferences = {}
    managed = service(preferences=lambda: preferences)
    managed.discover()
    source_id = next(item["source_id"] for item in managed.sources()["items"] if item["label"] == "agent-k3")
    if state == "missing":
        source_id = "unknown"
    elif state == "disabled":
        preferences[source_id] = {"enabled": False}
    elif state == "stale":
        managed.clock = lambda: NOW + timedelta(seconds=121)
    elif state == "unreachable":
        managed.docker.status = CapabilityStatus.UNREACHABLE
        managed.discover()
    elif state == "unsupported":
        source_id = next(item["source_id"] for item in managed.sources()["items"] if item["label"] == "agent-codex-b")
    else:
        managed.docker.session_transport = lambda target: None
    reader = SessionReaderFake()
    result = managed.read_sessions(source_id, reader=reader)
    assert result["status"] in {"UNAVAILABLE", "UNSUPPORTED"}
    assert reader.calls == []


def test_session_capability_expires_even_without_discovery_refresh():
    managed = service()
    managed.discover()
    managed.read_sessions("local", reader=SessionReaderFake())
    managed.clock = lambda: NOW + timedelta(seconds=121)
    local = next(item for item in managed.sources()["items"] if item["source_id"] == "local")
    assert local["state"] == "STALE"
    assert not local["capabilities"]["sessions"]
    assert not local["session_probe_available"]


def test_failed_docker_backend_preserves_native_facts():
    managed = service()
    def failed():
        raise OSError("backend failed")
    managed.docker.list_targets = failed
    report = managed.discover_with_authority()
    assert [item.agent_type for item in report.snapshot.agents] == ["CODEX"]
    assert report.snapshot.status is CapabilityStatus.DEGRADED


def test_future_observation_is_unknown_rather_than_current(tmp_path):
    agents, runtimes = projection(tmp_path, service().discover_with_authority(), now=NOW - timedelta(seconds=10))
    assert all(item["presence"] == "UNKNOWN" for item in agents["items"])
    assert all(item["reason_code"] == "AGENT_OBSERVATION_UNKNOWN" for item in agents["items"])
    assert all(item["reason_code"] == "RUNTIME_OBSERVATION_UNKNOWN" for item in runtimes["items"])


def test_running_container_runtime_remains_visible_when_process_probe_fails(tmp_path):
    managed = service()
    managed.docker.status = CapabilityStatus.UNREACHABLE
    report = managed.discover_with_authority()
    _agents, runtime = projection(tmp_path, report)
    containers = [item for item in runtime['items'] if item['runtime_type'] == 'CONTAINER']
    assert len(containers) == 2
    assert all(item['availability'] == 'AVAILABLE' for item in containers)
    assert all(item['capabilities'] == ['container_observation'] for item in containers)
    assert all(item['state'] == 'UNREACHABLE' for item in managed.sources()['items'] if item['kind'] == 'CONTAINER')


def test_truly_unreachable_container_retains_unreachable_projection_reason(tmp_path):
    managed = service()
    managed.docker.status = CapabilityStatus.UNREACHABLE
    agents, _ = projection(tmp_path, managed.discover_with_authority())
    assert agents['status'] == 'DEGRADED'
    assert len(agents['items']) == 1
    assert agents['reason_code'] == 'R4_AGENT_DISCOVERY_UNREACHABLE'


def test_permission_denied_is_partial_coverage_not_total_discovery_unreachability(tmp_path):
    managed = service()
    managed.docker.status = CapabilityStatus.PERMISSION_DENIED
    agents, _ = projection(tmp_path, managed.discover_with_authority())
    assert agents['status'] == 'DEGRADED'
    assert len(agents['items']) == 1
    assert agents['reason_code'] == 'R4_AGENT_DISCOVERY_PARTIAL'
