"""Product discovery composition and packaged API refresh contracts."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

import agentguard.api.server as server_module
from agentguard.api.server import create_app
from agentguard.discovery import (
    AgentDescriptor,
    AgentLifecycleStatus,
    CapabilityAssessment,
    CapabilityStatus,
    DiscoverySnapshot,
    DomainCapabilities,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
    RuntimeDescriptor,
)
from agentguard.discovery.agents import (
    ProcessAccessDeniedError,
    ProcessWorkspaceAuthority,
)
from agentguard.discovery.product import (
    HostAgentProcessAuthority,
    ProductDiscoveryReport,
    ProductDiscoveryService,
)
from agentguard.recovery.workspace_scope import WorkspaceScopeService
from agentguard.storage.db import StateDB

NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


class _RuntimeAdapter:
    def discover(self) -> DiscoverySnapshot:
        return DiscoverySnapshot(
            snapshot_id="self-runtime-input",
            observed_at=NOW,
            domains=(
                ExecutionDomainDescriptor(
                    domain_id="windows-current",
                    kind=ExecutionDomainKind.WINDOWS,
                    capabilities=DomainCapabilities(
                        {
                            "self_visible": CapabilityAssessment(
                                status=CapabilityStatus.AVAILABLE,
                                reason_code="CURRENT_PROCESS_VISIBLE",
                                evidence_ids=("domain-evidence",),
                                confidence=0.95,
                            )
                        }
                    ),
                    evidence_ids=("domain-evidence",),
                    confidence=0.95,
                ),
            ),
            evidence=(
                ProbeEvidence(
                    evidence_id="domain-evidence",
                    collector="self-runtime",
                    source="local",
                    observed_at=NOW,
                    fact_type="platform.current",
                    value={"system": "Windows"},
                    status=CapabilityStatus.AVAILABLE,
                    sanitized=True,
                ),
            ),
            status=CapabilityStatus.AVAILABLE,
        )


class _ProcessHandle:
    def __init__(self, pid: int, basename: str, cwd: str = "C:\\work") -> None:
        self.pid = pid
        self._basename = basename
        self._cwd = cwd

    def parent_pid(self):
        return 1

    def executable_basename(self):
        return self._basename

    def create_time(self):
        return NOW - timedelta(minutes=2)

    def cwd(self):
        return self._cwd

    def fixed_boolean_facts(self):
        return {}


class _ProcessBackend:
    def __init__(self, handles=(), failure=None, product_identities=None) -> None:
        self._handles = tuple(handles)
        self._failure = failure
        self._product_identities = dict(product_identities or {})

    def iter_processes(self):
        if self._failure is not None:
            raise self._failure
        return self._handles

    def bounded_product_identity(self, pid: int):
        return self._product_identities.get(pid)


def test_product_discovery_separates_self_runtime_from_external_agents():
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend(
            (
                _ProcessHandle(101, "agentguard-sidecar.exe"),
                _ProcessHandle(102, "codex.exe"),
                _ProcessHandle(103, "notepad.exe"),
            )
        ),
        clock=lambda: NOW,
        home_path="C:\\Users\\private-user",
        host_domain_observers=(),
    )

    snapshot = service.discover()

    assert [(item.runtime_type, item.domain_id) for item in snapshot.runtimes] == [
        ("SELF_RUNTIME", "windows-current")
    ]
    assert [(item.agent_type, item.lifecycle.value) for item in snapshot.agents] == [
        ("CODEX", "RUNNING")
    ]
    encoded = json.dumps(snapshot.to_dict())
    assert "AGENTSTATE_GUARD" not in encoded
    assert "notepad" not in encoded.lower()


def test_product_discovery_keeps_private_host_process_authority_for_observer():
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend((_ProcessHandle(102, "codex.exe"),)),
        clock=lambda: NOW,
        host_domain_observers=(),
    )

    report = service.discover_with_authority()

    assert len(report.host_agent_process_authorities) == 1
    authority = report.host_agent_process_authorities[0]
    assert authority.agent_id == report.snapshot.agents[0].agent_id
    assert authority.pid == 102
    assert authority.create_time == NOW - timedelta(minutes=2)
    assert authority.execution_domain_id == "windows-current"


def test_product_discovery_admits_verified_pi_process_without_generic_electron_guess(
    tmp_path,
):
    install_root = tmp_path / "pi-install"
    workspace = tmp_path / "workspace"
    install_root.mkdir()
    workspace.mkdir()
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend(
            (
                _ProcessHandle(201, "Pi Agent Desktop.exe", str(workspace)),
                _ProcessHandle(202, "electron.exe", str(workspace)),
            ),
            product_identities={201: ("PI", str(install_root))},
        ),
        clock=lambda: NOW,
        host_domain_observers=(),
    )

    report = service.discover_with_authority()

    assert [(item.agent_type, item.lifecycle.value) for item in report.snapshot.agents] == [
        ("PI", "RUNNING")
    ]
    assert report.snapshot.agents[0].label.startswith("PI ")
    agent_evidence = next(
        item for item in report.snapshot.evidence if item.fact_type == "agent.metadata"
    )
    assert agent_evidence.value["role"] == "EXECUTION_AGENT"
    assert [item.pid for item in report.host_agent_process_authorities] == [201]


def test_product_discovery_admits_verified_zcode_process_without_generic_electron_guess(
    tmp_path,
):
    install_root = tmp_path / "zcode-install"
    workspace = tmp_path / "workspace"
    install_root.mkdir()
    workspace.mkdir()
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend(
            (
                _ProcessHandle(301, "ZCode.exe", str(workspace)),
                _ProcessHandle(302, "electron.exe", str(workspace)),
                _ProcessHandle(303, "node.exe", str(workspace)),
            ),
            product_identities={301: ("ZCODE", str(install_root))},
        ),
        clock=lambda: NOW,
        host_domain_observers=(),
    )

    report = service.discover_with_authority()

    assert [(item.agent_type, item.lifecycle.value) for item in report.snapshot.agents] == [
        ("ZCODE", "RUNNING")
    ]
    assert report.snapshot.agents[0].label.startswith("ZCODE ")
    agent_evidence = next(
        item for item in report.snapshot.evidence if item.fact_type == "agent.metadata"
    )
    assert agent_evidence.value["role"] == "EXECUTION_AGENT"
    assert [item.pid for item in report.host_agent_process_authorities] == [301]


def test_pi_install_directory_is_not_promoted_to_workspace_authority(tmp_path):
    install_root = tmp_path / "pi-install"
    install_root.mkdir()
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend(
            (_ProcessHandle(201, "Pi Agent Desktop.exe", str(install_root)),),
            product_identities={201: ("PI", str(install_root))},
        ),
        clock=lambda: NOW,
        host_domain_observers=(),
    )

    report = service.discover_with_authority()

    assert [item.agent_type for item in report.snapshot.agents] == ["PI"]
    assert report.snapshot.agents[0].workspace_ids == ()
    assert report.workspace_authorities == ()


def test_product_discovery_preserves_agent_probe_permission_denied():
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend(failure=ProcessAccessDeniedError()),
        clock=lambda: NOW,
        host_domain_observers=(),
    )

    snapshot = service.discover()

    assert snapshot.agents == ()
    failure = next(
        item for item in snapshot.evidence if item.fact_type == "probe.unreachable"
    )
    assert failure.status is CapabilityStatus.PERMISSION_DENIED
    assert failure.value == {
        "execution_domain_id": "windows-current",
        "reason_code": "PROCESS_ACCESS_DENIED",
        "scope": "agents",
    }


def _snapshot(snapshot_id: str, *, include_agent: bool) -> DiscoverySnapshot:
    runtime_evidence = ProbeEvidence(
        evidence_id=f"{snapshot_id}-runtime",
        collector="product-discovery",
        source="local",
        observed_at=NOW,
        fact_type="runtime.metadata",
        value={"runtime_kind": "SELF_RUNTIME"},
        status=CapabilityStatus.AVAILABLE,
        sanitized=True,
    )
    agent_evidence = ProbeEvidence(
        evidence_id=f"{snapshot_id}-agent",
        collector="product-discovery",
        source="local",
        observed_at=NOW,
        fact_type="agent.metadata",
        value={
            "agent_kind": "CODEX",
            "lifecycle": "RUNNING",
            "role": "EXECUTION_AGENT",
        },
        status=CapabilityStatus.AVAILABLE,
        sanitized=True,
    )
    return DiscoverySnapshot(
        snapshot_id=snapshot_id,
        observed_at=NOW,
        runtimes=(
            RuntimeDescriptor(
                runtime_id="self-runtime",
                runtime_type="SELF_RUNTIME",
                domain_id="windows-current",
                status=CapabilityStatus.AVAILABLE,
                evidence_ids=(runtime_evidence.evidence_id,),
                confidence=0.95,
            ),
        ),
        agents=(
            AgentDescriptor(
                agent_id="codex-process",
                agent_type="CODEX",
                lifecycle=AgentLifecycleStatus.RUNNING,
                domain_id="windows-current",
                runtime_id="self-runtime",
                evidence_ids=(agent_evidence.evidence_id,),
                confidence=0.8,
            ),
        )
        if include_agent
        else (),
        evidence=(runtime_evidence, agent_evidence)
        if include_agent
        else (runtime_evidence,),
        status=CapabilityStatus.AVAILABLE,
    )


class _SequenceDiscovery:
    def __init__(self) -> None:
        self._snapshots = iter(
            (
                _snapshot("startup-snapshot", include_agent=True),
                _snapshot("refresh-snapshot", include_agent=False),
            )
        )

    def discover(self) -> DiscoverySnapshot:
        return next(self._snapshots)


class _FailingDiscovery:
    def discover(self) -> DiscoverySnapshot:
        raise PermissionError("sensitive operating-system detail")


class _LifecycleDiscovery:
    def discover_with_authority(self):
        return ProductDiscoveryReport(
            snapshot=_snapshot("observer-startup", include_agent=True),
            host_agent_process_authorities=(
                HostAgentProcessAuthority(
                    agent_id="codex-process",
                    process_instance_id="process-codex",
                    pid=4242,
                    create_time=NOW - timedelta(minutes=2),
                    execution_domain_id="windows-current",
                ),
            ),
        )


class _MultiWorkspaceDiscovery:
    def __init__(self, first: Path, second: Path) -> None:
        self._roots = (first, second)

    def discover_with_authority(self):
        return ProductDiscoveryReport(
            snapshot=_snapshot("multi-workspace", include_agent=False),
            workspace_authorities=tuple(
                ProcessWorkspaceAuthority(
                    process_instance_id=f"process-{index}",
                    candidate_id=f"candidate-{index}",
                    execution_domain_id="windows-current",
                    cwd=root,
                    evidence_refs=("multi-workspace-runtime",),
                )
                for index, root in enumerate(self._roots, 1)
            ),
        )


class _ObserverLifecycle:
    def __init__(self):
        self.plans = []
        self.started = 0
        self.stopped = 0
        self.reconcile = None
        self.reconcile_basenames = set()

    def update(self, plan):
        self.plans.append(plan)

    def configure_reconciliation(self, callback, *, basenames):
        self.reconcile = callback
        self.reconcile_basenames = set(basenames)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def test_packaged_app_owns_host_observer_start_update_and_shutdown(tmp_path):
    observer = _ObserverLifecycle()
    app = create_app(
        state_db_path=tmp_path / "state.db",
        config={"base_dir": str(tmp_path)},
        discovery_service=_LifecycleDiscovery(),
        product_startup_discovery=True,
        host_observer=observer,
    )

    with TestClient(app):
        assert observer.started == 1
        assert len(observer.plans) == 1
        target = observer.plans[0].targets[0]
        assert target.agent_ref == "codex-process"
        assert target.agent_event_id.startswith("discovery-")
        assert target.pid == 4242
        assert observer.reconcile is not None
        assert {"codex.exe", "node.exe"} <= observer.reconcile_basenames

    assert observer.stopped == 1


def test_refresh_persists_independent_workspace_authorities(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    state_path = tmp_path / "state.db"
    app = create_app(
        state_db_path=state_path,
        config={"base_dir": str(tmp_path)},
        discovery_service=_MultiWorkspaceDiscovery(first, second),
        product_startup_discovery=True,
        host_observer=_ObserverLifecycle(),
    )

    with TestClient(app):
        pass

    database = StateDB(state_path)
    database.connect()
    try:
        results = WorkspaceScopeService(database).authority_results()
    finally:
        database.close()
    assert len(results) == 2
    assert {item.authority_state for item in results} == {"BOUND"}
    assert {item.scope.root_path for item in results if item.scope is not None} == {
        first.resolve(),
        second.resolve(),
    }


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _request(
    base_url: str, path: str, token: str | None = None, *, method="GET", body=None
):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    if token is not None:
        request.add_header("X-Session-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@contextmanager
def _running_api(root: Path):
    port = _free_port()
    app = create_app(
        state_db_path=root / "state.db",
        config={"base_dir": str(root)},
        discovery_service=_SequenceDiscovery(),
        product_startup_discovery=True,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    base_url = f"http://127.0.0.1:{port}"
    token = _request(base_url, "/api/session")[1]["token"]
    try:
        yield base_url, token
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def _running_failing_api(root: Path):
    port = _free_port()
    app = create_app(
        state_db_path=root / "state.db",
        config={"base_dir": str(root)},
        discovery_service=_FailingDiscovery(),
        product_startup_discovery=True,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    base_url = f"http://127.0.0.1:{port}"
    token = _request(base_url, "/api/session")[1]["token"]
    try:
        yield base_url, token
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_packaged_startup_records_discovery_and_refresh_replaces_visible_agents(
    tmp_path,
):
    with _running_api(tmp_path) as (base_url, token):
        startup_agents = _request(base_url, "/api/v1/agents", token)[1]
        status, refreshed = _request(
            base_url,
            "/api/v1/discovery/refresh",
            token,
            method="POST",
            body={},
        )
        injected_status, injected = _request(
            base_url,
            "/api/v1/discovery/refresh",
            token,
            method="POST",
            body={"target_path": "C:\\caller-controlled"},
        )
        refreshed_agents = _request(base_url, "/api/v1/agents", token)[1]
        refreshed_runtime = _request(base_url, "/api/v1/runtime", token)[1]

    assert startup_agents["items"][0]["detected_identity"] == "CODEX"
    assert startup_agents["items"][0]["lifecycle"] == "RUNNING"
    assert startup_agents["items"][0]["observed_at"] == NOW.isoformat()
    assert status == 200
    assert refreshed["schema_version"] == "product-discovery-1"
    assert refreshed["status"] == "AVAILABLE"
    assert refreshed["reason_code"] == "DISCOVERY_REFRESHED"
    assert refreshed["snapshot_id"] == "refresh-snapshot"
    assert refreshed["observed_at"] == NOW.isoformat()
    assert refreshed["affected_views"] == ["runtime", "agents", "supervision"]
    assert refreshed["runtime_count"] == 1
    assert refreshed["agent_count"] == 0
    assert refreshed["evidence_refs"]
    assert refreshed_agents["status"] == "EMPTY"
    assert refreshed_agents["items"] == []
    assert len(refreshed_runtime["items"]) == 1
    assert injected_status == 422
    assert injected["reason_code"] == "DISCOVERY_REFRESH_REQUEST_INVALID"


def test_discovery_failure_is_ledgered_as_degraded_without_leaking_exception(tmp_path):
    with _running_failing_api(tmp_path) as (base_url, token):
        runtime = _request(base_url, "/api/v1/runtime", token)[1]
        status, refresh = _request(
            base_url,
            "/api/v1/discovery/refresh",
            token,
            method="POST",
            body={},
        )

    assert runtime["status"] == "DEGRADED"
    assert runtime["items"][0]["availability"] == "UNREACHABLE"
    assert runtime["items"][0]["reason_code"] == "DISCOVERY_PROBE_UNREACHABLE"
    assert status == 503
    assert refresh["status"] == "DEGRADED"
    assert refresh["reason_code"] == "DISCOVERY_REFRESH_UNAVAILABLE"
    assert "sensitive" not in json.dumps(refresh).lower()


def test_run_server_enables_startup_discovery_and_disables_access_log(
    tmp_path, monkeypatch
):
    captured = {}

    def fake_create_app(**kwargs):
        captured["create_app"] = kwargs
        return object()

    def fake_uvicorn_run(app, **kwargs):
        captured["app"] = app
        captured["uvicorn"] = kwargs

    monkeypatch.setattr(server_module, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    server_module.run_server(config={"base_dir": str(tmp_path)})

    assert captured["create_app"]["product_startup_discovery"] is True
    assert captured["uvicorn"]["access_log"] is False
    assert captured["uvicorn"]["host"] == "127.0.0.1"
