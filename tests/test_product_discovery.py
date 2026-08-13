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
from agentguard.discovery.agents import ProcessAccessDeniedError
from agentguard.discovery.product import ProductDiscoveryService

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
    def __init__(self, handles=(), failure=None) -> None:
        self._handles = tuple(handles)
        self._failure = failure

    def iter_processes(self):
        if self._failure is not None:
            raise self._failure
        return self._handles


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


def test_product_discovery_preserves_agent_probe_permission_denied():
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend(failure=ProcessAccessDeniedError()),
        clock=lambda: NOW,
    )

    snapshot = service.discover()

    assert snapshot.agents == ()
    failure = next(item for item in snapshot.evidence if item.fact_type == "probe.unreachable")
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
        value={"agent_kind": "CODEX", "lifecycle": "RUNNING", "role": "EXECUTION_AGENT"},
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
        evidence=(runtime_evidence, agent_evidence) if include_agent else (runtime_evidence,),
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


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _request(base_url: str, path: str, token: str | None = None, *, method="GET", body=None):
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


def test_packaged_startup_records_discovery_and_refresh_replaces_visible_agents(tmp_path):
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
    assert status == 200
    assert refreshed == {
        "schema_version": "product-discovery-1",
        "status": "AVAILABLE",
        "reason_code": "DISCOVERY_REFRESHED",
        "snapshot_id": "refresh-snapshot",
        "runtime_count": 1,
        "agent_count": 0,
    }
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


def test_run_server_enables_startup_discovery_and_disables_access_log(tmp_path, monkeypatch):
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
