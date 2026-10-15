"""Session queries use only short-lived, read-only protocol brokers."""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
from types import SimpleNamespace

import pytest

from agentguard.discovery.models import ExecutionDomainKind
from agentguard.discovery.sessions import SessionReader
from agentguard.recovery.protection_domains import _helper_source
from tests.test_managed_discovery import service as managed_service

_LOCAL_ENV = {"DOCKER_HOST": "npipe:////./pipe/dockerDesktopLinuxEngine"}


class _WslRegistry:
    def __init__(self):
        self.connected = True
        self.boot_id = '11111111-2222-3333-4444-555555555555'
        self.start_ticks = 123
        self.probes = 0
        self.sessions = []
        self.processes = ({'pid': 42, 'ppid': 1, 'name': 'codex',
                           'start_ticks': self.start_ticks, 'cwd': '/home/user/project'},)

    def sources(self, refresh=True):
        return [{'source_id': 'wsl-fixture', 'kind': 'WSL', 'label': 'Ubuntu',
                 'guid': 'distro-guid', 'disk_identity': {'dev': 1, 'ino': 2},
                 'running': True, 'connected': self.connected, 'epoch': 'epoch',
                 'capabilities': {'scan': self.connected, 'apply': self.connected}}]

    def probe_processes(self, source_id):
        self.probes += 1
        processes = tuple({**process, 'start_ticks': self.start_ticks}
                          if process['name'] == 'codex' else process for process in self.processes)
        return SimpleNamespace(boot_id=self.boot_id, partial_reason=None, processes=processes)

    def read_sessions(self, source_id, provider):
        self.sessions.append(provider)
        return {'schema_version': 'discovery-sessions-1', 'provider': provider,
                'status': 'AVAILABLE' if provider == 'CODEX' else 'UNKNOWN',
                'reason_code': 'SESSION_STORE_METADATA_AVAILABLE' if provider == 'CODEX'
                               else 'SESSION_STORE_NOT_PRESENT',
                'identity_basis': 'STORE_METADATA', 'scope': 'PROVIDER_STORE',
                'sessions': [{'session_id': 'thread-1', 'provider': 'CODEX', 'title': None,
                              'updated_at': '2026-09-29T10:00:00+00:00',
                              'cwd': '/home/user/project'}] if provider == 'CODEX' else [],
                'has_more': False}


def test_connected_wsl_processes_and_store_metadata_have_distinct_identity():
    registry = _WslRegistry()
    managed = managed_service()
    managed.domain_registry = registry
    first = managed.discover_with_authority().snapshot
    wsl_domain = next(domain for domain in first.domains if domain.kind is ExecutionDomainKind.WSL)
    codex = next(agent for agent in first.agents if agent.domain_id == wsl_domain.domain_id)
    workspace = next(workspace for workspace in first.workspaces
                     if workspace.workspace_id in codex.workspace_ids)
    assert workspace.domain_id == wsl_domain.domain_id
    assert all(authority.execution_domain_id != wsl_domain.domain_id
               for authority in managed.discover_with_authority().workspace_authorities)
    result = managed.read_sessions('wsl-fixture')
    assert result['status'] == 'AVAILABLE'
    assert result['identity_basis'] == 'STORE_METADATA'
    assert result['sessions'][0]['provider'] == 'CODEX'
    assert result['sessions'][0]['cwd'] == '/home/user/project'
    assert registry.sessions == ['CODEX']
    registry.start_ticks += 1
    restarted = managed.discover_with_authority().snapshot
    assert next(agent for agent in restarted.agents if agent.domain_id == wsl_domain.domain_id).agent_id != codex.agent_id
    registry.connected = False
    disconnected = managed.discover_with_authority().snapshot
    assert all(agent.domain_id != wsl_domain.domain_id for agent in disconnected.agents)
    assert registry.probes == 3
    assert next(item for item in managed.sources()['items'] if item['source_id'] == 'wsl-fixture')['state'] == 'NOT_PROBED'


def test_compatibility_reader_requires_one_observed_wsl_provider():
    registry = _WslRegistry()
    registry.processes = (
        {'pid': 42, 'ppid': 1, 'name': 'codex', 'start_ticks': 123,
         'cwd': '/home/user/codex'},
        {'pid': 43, 'ppid': 1, 'name': 'kimi', 'start_ticks': 124,
         'cwd': '/home/user/kimi'},
    )
    managed = managed_service()
    managed.domain_registry = registry
    managed.discover_with_authority()
    result = managed.read_sessions('wsl-fixture')
    assert result['status'] == 'UNSUPPORTED'
    assert result['reason_code'] == 'SESSION_STORE_SELECTION_REQUIRED'
    assert result['sessions'] == []
    assert registry.sessions == []


@pytest.mark.skipif(os.environ.get('AGENTGUARD_LIVE_DOMAINS') != '1', reason='explicit live fixture only')
def test_live_wsl_fixed_session_metadata_readers():
    program = r'''
import json, os, pathlib, shutil, sys, tempfile
scope = {'__name__': 'session_helper_test'}
exec(compile(sys.stdin.read(), 'shared_helper_bundle.py', 'exec'), scope)
home = pathlib.Path(tempfile.mkdtemp(prefix='asg-session-'))
try:
    def fixtures(codex_root, kimi_root, suffix):
        codex = codex_root / 'sessions' / '2026' / '09' / '29'
        codex.mkdir(parents=True)
        (codex / 'rollout-test.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload':
            {'id': 'codex-' + suffix, 'cwd': '/tmp/codex-' + suffix,
             'timestamp': '2026-09-29T10:00:00Z'}}) + '\n')
        session = kimi_root / 'sessions' / 'bucket' / ('kimi-' + suffix)
        session.mkdir(parents=True)
        (kimi_root / 'session_index.jsonl').write_text(json.dumps({'sessionId': 'kimi-' + suffix,
            'sessionDir': str(session), 'workDir': '/tmp/kimi-' + suffix}) + '\n')
    fixtures(home / '.codex', home / '.kimi-code', 'default')
    fixtures(home / 'custom-codex', home / 'custom-kimi', 'custom')
    os.environ.pop('CODEX_HOME', None)
    os.environ.pop('KIMI_CODE_HOME', None)
    os.environ['HOME'] = str(home)
    def read_selected(basis):
        stores = scope['_discovery_query']({'action': 'session_stores'})['items']
        selected = {}
        for provider in ('CODEX', 'KIMI_CODE'):
            candidate = next(item for item in stores if item['provider'] == provider
                             and item['locator_basis'] == basis)
            selected[provider] = scope['_discovery_query']({'action': 'read_session_store',
                'provider': provider, 'path': candidate['path'], 'limit': 50, 'offset': 0})
        return selected
    default = read_selected('SERVICE_HOME')
    os.environ['CODEX_HOME'] = str(home / 'custom-codex')
    os.environ['KIMI_CODE_HOME'] = str(home / 'custom-kimi')
    custom = read_selected('EXPLICIT_ENV')
    print(json.dumps({'default': default, 'custom': custom}))
finally:
    shutil.rmtree(home)
'''
    result = subprocess.run(['wsl.exe', '--distribution', 'Ubuntu', '--exec', 'python3', '-I', '-B',
                             '-c', program], input=_helper_source(),
                            capture_output=True, text=True, check=True)
    stores = json.loads(result.stdout)
    for mode in ('default', 'custom'):
        assert stores[mode]['CODEX']['status'] == stores[mode]['KIMI_CODE']['status'] == 'AVAILABLE'
        assert stores[mode]['CODEX']['sessions'][0]['cwd'] == '/tmp/codex-' + mode
        assert stores[mode]['KIMI_CODE']['sessions'][0]['cwd'] == '/tmp/kimi-' + mode
        assert stores[mode]['CODEX']['sessions'][0]['provider'] == 'CODEX'
        assert stores[mode]['KIMI_CODE']['sessions'][0]['provider'] == 'KIMI_CODE'


def _lines(*messages: object) -> bytes:
    return b"".join(json.dumps(message).encode() + b"\n" for message in messages)


class _Input:
    def __init__(self) -> None:
        self.writes = bytearray()
        self.closed = False

    def write(self, value: bytes) -> None:
        self.writes.extend(value)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def messages(self) -> list[dict]:
        return [json.loads(line) for line in self.writes.splitlines()]


class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"") -> None:
        self.stdin = _Input()
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.terminated = False
        self.killed = False

    def wait(self, *, timeout: float) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _Factory:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], **kwargs):
        self.calls.append((argv, kwargs))
        return self.process


def test_codex_lists_bounded_provider_store_without_execution_commands(tmp_path):
    process = _FakeProcess(
        _lines(
            {"method": "thread/updated", "params": {}},
            {"id": 1, "result": {"serverInfo": {"version": "0.104.0"}}},
            {"method": "thread/updated", "params": {}},
            {
                "id": 2,
                "result": {
                    "data": [
                        {
                            "id": "thread-1",
                            "title": "Release review",
                            "updatedAt": "2026-09-29T10:00:00Z",
                            "cwd": "C:/private/workspace",
                        }
                    ],
                    "nextCursor": None,
                },
            },
        )
    )
    factory = _Factory(process)
    executable = str(tmp_path / "codex.cmd")

    result = SessionReader(process_factory=factory, which=lambda name: executable).read(
        "CODEX"
    )

    assert result == {
        "schema_version": "discovery-sessions-1",
        "status": "AVAILABLE",
        "reason_code": "SESSION_LIST_AVAILABLE",
        "provider": "CODEX",
        "scope": "PROVIDER_STORE",
        "identity_basis": "PROTOCOL_HANDSHAKE",
        "version": "0.104.0",
        "protocol_version": None,
        "sessions": [
            {
                "session_id": "thread-1",
                "title": "Release review",
                "updated_at": "2026-09-29T10:00:00+00:00",
                "cwd": "C:/private/workspace",
            }
        ],
        "has_more": False,
    }
    assert factory.calls[0][0] == [executable, "app-server", "--listen", "stdio://"]
    assert factory.calls[0][1]["shell"] is False
    assert factory.calls[0][1]["stdin"] is subprocess.PIPE
    assert factory.calls[0][1]["stdout"] is subprocess.PIPE
    assert factory.calls[0][1]["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert process.stdin.messages() == [
        {
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "agentstate_guard", "version": "0.1.0"}},
        },
        {"method": "initialized", "params": {}},
        {"id": 2, "method": "thread/list", "params": {"limit": 50}},
    ]
    assert process.stdin.closed
    assert not process.terminated and not process.killed
    assert result["sessions"][0]["cwd"] == "C:/private/workspace"


def test_kimi_acp_redacts_title_and_caps_session_page(tmp_path, monkeypatch):
    sessions = [
        {
            "sessionId": f"kimi-{index}",
            "title": "token=supersecretvalue" if index == 0 else "A" * 180,
            "updatedAt": "2026-09-29T10:00:00Z",
            "cwd": "/private/workspace",
        }
        for index in range(51)
    ]
    process = _FakeProcess(
        _lines(
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}},
            {"jsonrpc": "2.0", "id": 2, "result": {"sessions": sessions}},
        )
    )
    factory = _Factory(process)
    docker_executable = str(tmp_path / "docker.exe")
    container_id = "a" * 64
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote.example")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")

    result = SessionReader(process_factory=factory).read(
        "KIMI_CODE", container_id=container_id, docker_executable=docker_executable,
        environment=_LOCAL_ENV,
    )

    assert result["status"] == "AVAILABLE"
    assert result["scope"] == "PROVIDER_STORE"
    assert result["identity_basis"] == "PROTOCOL_HANDSHAKE"
    assert result["version"] is None
    assert result["protocol_version"] == 1
    assert result["has_more"] is True
    assert len(result["sessions"]) == 50
    assert result["sessions"][0]["title"] == "[REDACTED]"
    assert len(result["sessions"][1]["title"]) == 120
    assert result["sessions"][0]["cwd"] == "/private/workspace"
    assert factory.calls[0][0] == [
        docker_executable, "exec", "-i", container_id, "kimi", "acp"
    ]
    assert factory.calls[0][1]["env"] == _LOCAL_ENV
    assert factory.calls[0][1]["env"] is not _LOCAL_ENV
    assert "DOCKER_CONTEXT" not in factory.calls[0][1]["env"]
    assert process.stdin.messages() == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "agentstate_guard", "version": "0.1.0"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": "session/list", "params": {}},
    ]
    assert process.stdin.closed


def test_product_and_protocol_versions_are_independent(tmp_path):
    codex = _FakeProcess(_lines(
        {"id": 1, "result": {"protocolVersion": 2}},
        {"id": 2, "result": {"data": []}},
    ))
    codex_result = SessionReader(
        process_factory=_Factory(codex), which=lambda name: str(tmp_path / "codex.exe")
    ).read("CODEX")
    assert codex_result["status"] == "EMPTY"
    assert codex_result["version"] is None
    assert codex_result["protocol_version"] == 2

    kimi = _FakeProcess(_lines(
        {"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": 1, "agentInfo": {"name": "Kimi", "version": "1.8.0"},
        }},
        {"jsonrpc": "2.0", "id": 2, "result": {"sessions": []}},
    ))
    kimi_result = SessionReader(process_factory=_Factory(kimi)).read(
        "KIMI_CODE", container_id="a" * 64,
        docker_executable=str(tmp_path / "docker.exe"), environment=_LOCAL_ENV,
    )
    assert kimi_result["status"] == "EMPTY"
    assert kimi_result["version"] == "1.8.0"
    assert kimi_result["protocol_version"] == 1


@pytest.mark.parametrize(
    ("provider", "container_id", "docker_executable", "environment"),
    [
        ("OTHER", None, None, None),
        ("CODEX", "a" * 64, None, None),
        ("CODEX", None, "/usr/bin/docker", None),
        ("CODEX", None, None, _LOCAL_ENV),
        ("KIMI_CODE", None, "/usr/bin/docker", _LOCAL_ENV),
        ("KIMI_CODE", "a" * 12, "/usr/bin/docker", _LOCAL_ENV),
        ("KIMI_CODE", "a" * 64, "/usr/bin/other", _LOCAL_ENV),
        ("KIMI_CODE", "a" * 64, "/usr/bin/docker", None),
        ("KIMI_CODE", "a" * 64, "/usr/bin/docker", {"DOCKER_HOST": "ssh://remote"}),
        ("KIMI_CODE", "a" * 64, "/usr/bin/docker", {
            **_LOCAL_ENV, "DOCKER_CONTEXT": "remote",
        }),
        ("KIMI_CODE", "a" * 64, "/usr/bin/docker", {
            **_LOCAL_ENV, "docker_host": "ssh://remote",
        }),
    ],
)
def test_unsupported_sources_never_launch_process(
    provider, container_id, docker_executable, environment
):
    factory = _Factory(_FakeProcess(b""))
    result = SessionReader(process_factory=factory).read(
        provider, container_id=container_id, docker_executable=docker_executable,
        environment=environment,
    )

    assert result["status"] == "UNSUPPORTED"
    assert result["sessions"] == []
    assert factory.calls == []


@pytest.mark.parametrize(
    ("output", "status", "reason"),
    [
        (_lines({"id": 9, "result": {}}), "ERROR", "SESSION_RESPONSE_ID_INVALID"),
        (b"not-json\n", "ERROR", "SESSION_PROTOCOL_INVALID"),
        (_lines({"id": 1, "method": "permission/request", "params": {}}),
         "UNSUPPORTED", "SESSION_SERVER_REQUEST_UNSUPPORTED"),
        (_lines({"id": 1, "error": {"message": "private details"}}),
         "ERROR", "SESSION_INITIALIZE_REJECTED"),
        (_lines({"id": 1, "error": {"code": 401, "message": "private details"}}),
         "AUTH_REQUIRED", "SESSION_AUTH_REQUIRED"),
        (_lines({"id": 1, "error": {"code": 403, "message": "private details"}}),
         "AUTH_REQUIRED", "SESSION_AUTH_REQUIRED"),
        (_lines({"id": 1, "error": {"code": "403", "message": "private details"}}),
         "AUTH_REQUIRED", "SESSION_AUTH_REQUIRED"),
        (_lines({"id": 1, "error": {"code": "AUTH_REQUIRED", "message": "private details"}}),
         "AUTH_REQUIRED", "SESSION_AUTH_REQUIRED"),
        (_lines({"id": 1, "error": {"code": -32601, "message": "private details"}}),
         "UNSUPPORTED", "SESSION_INITIALIZE_UNSUPPORTED"),
        (_lines({"id": 1, "error": {"code": -32603, "message": "private details"}}),
         "ERROR", "SESSION_INITIALIZE_REJECTED"),
        (_lines({"id": 1, "result": {}}), "UNREACHABLE", "SESSION_PROTOCOL_EOF"),
    ],
)
def test_protocol_failures_are_bounded_and_never_return_raw_details(
    tmp_path, output, status, reason
):
    process = _FakeProcess(output, stderr=b"private stderr message")
    result = SessionReader(
        process_factory=_Factory(process), which=lambda name: str(tmp_path / "codex.exe")
    ).read("CODEX")

    assert result["status"] == status
    assert result["reason_code"] == reason
    assert result["sessions"] == []
    assert "private" not in json.dumps(result)
    assert process.stdin.closed
    expected_methods = (
        ["initialize", "initialized", "thread/list"]
        if reason == "SESSION_PROTOCOL_EOF"
        else ["initialize"]
    )
    assert [message["method"] for message in process.stdin.messages()] == expected_methods


@pytest.mark.parametrize(("code", "status", "reason"), [
    (-32601, "UNSUPPORTED", "SESSION_LIST_UNSUPPORTED"),
    (-32603, "ERROR", "SESSION_LIST_FAILED"),
    ("UNAUTHENTICATED", "AUTH_REQUIRED", "SESSION_AUTH_REQUIRED"),
])
def test_list_error_codes_do_not_expose_server_message(tmp_path, code, status, reason):
    process = _FakeProcess(_lines(
        {"id": 1, "result": {}},
        {"id": 2, "error": {"code": code, "message": "private details"}},
    ))
    result = SessionReader(
        process_factory=_Factory(process), which=lambda name: str(tmp_path / "codex.exe")
    ).read("CODEX")

    assert result["status"] == status
    assert result["reason_code"] == reason
    assert result["sessions"] == []
    assert "private" not in json.dumps(result)
    assert process.stdin.closed


def test_output_limit_fails_closed(tmp_path):
    process = _FakeProcess(b"x" * 200 + b"\n")
    result = SessionReader(
        process_factory=_Factory(process),
        which=lambda name: str(tmp_path / "codex.exe"),
        max_output_bytes=128,
    ).read("CODEX")

    assert result["status"] == "ERROR"
    assert result["reason_code"] == "SESSION_OUTPUT_LIMITED"
    assert process.stdin.closed


class _BlockingOutput:
    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def read(self, size: int) -> bytes:
        self.release.wait()
        return b""

    def close(self) -> None:
        self.release.set()


class _HangingProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__(b"")
        self.release = threading.Event()
        self.stdout = _BlockingOutput(self.release)

    def wait(self, *, timeout: float) -> int:
        if not self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("fake-provider", timeout)
        return 0

    def terminate(self) -> None:
        super().terminate()
        self.release.set()


def test_timeout_reclaims_only_temporary_query_process(tmp_path):
    process = _HangingProcess()
    result = SessionReader(
        process_factory=_Factory(process),
        which=lambda name: str(tmp_path / "codex.exe"),
        timeout_seconds=0.05,
    ).read("CODEX")

    assert result["status"] == "UNREACHABLE"
    assert result["reason_code"] == "SESSION_QUERY_TIMEOUT"
    assert process.terminated
    assert not process.killed
    assert process.stdin.closed
