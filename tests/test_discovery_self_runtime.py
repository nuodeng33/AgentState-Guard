"""Offline tests for current-process execution-domain discovery."""

from __future__ import annotations

import builtins
import json
import socket
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from agentguard.discovery import (
    CapabilityStatus,
    DiscoveryErrorCode,
    EvidenceReliability,
    ExecutionDomainKind,
)
from agentguard.discovery.domains import ExecutionDomainAdapter, SelfRuntimeAdapter

OBSERVED_AT = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


class FakeSource:
    """Controlled local facts; no test depends on the actual host."""

    def __init__(
        self,
        *,
        os_name: str = "posix",
        system: str = "Linux",
        release: str = "6.8.0-test",
        version: str = "#1 test kernel",
        environment: Mapping[str, str] | None = None,
        files: Mapping[str, bytes | str | BaseException] | None = None,
        links: Mapping[str, str | BaseException] | None = None,
    ) -> None:
        self._os_name = os_name
        self._system = system
        self._release = release
        self._version = version
        self._environment = dict(environment or {})
        self._files = dict(files or {})
        self._links = dict(links or {})

    def os_name(self) -> str:
        return self._os_name

    def platform_system(self) -> str:
        return self._system

    def platform_release(self) -> str:
        return self._release

    def platform_version(self) -> str:
        return self._version

    def environment_name_present(self, name: str) -> bool:
        return name in self._environment

    def read_bytes(self, path: str) -> bytes:
        value = self._files.get(path, FileNotFoundError(path))
        if isinstance(value, BaseException):
            raise value
        return value.encode("utf-8") if isinstance(value, str) else value

    def readlink(self, path: str) -> str:
        value = self._links.get(path, FileNotFoundError(path))
        if isinstance(value, BaseException):
            raise value
        return value


def _linux_files(**overrides: bytes | str | BaseException) -> dict[str, bytes | str | BaseException]:
    files: dict[str, bytes | str | BaseException] = {
        "/proc/sys/kernel/osrelease": "6.8.0-test\n",
        "/proc/version": "Linux version 6.8.0-test\n",
        "/proc/1/cgroup": "0::/\n",
        "/proc/self/cgroup": "0::/\n",
        "/proc/self/mountinfo": "24 23 0:22 / / rw - ext4 /dev/root rw\n",
        "/proc/self/status": "NoNewPrivs:\t0\nSeccomp:\t0\nCapEff:\t0000000000000000\n",
    }
    files.update(overrides)
    return files


def _adapter(source: FakeSource) -> SelfRuntimeAdapter:
    return SelfRuntimeAdapter(source=source, clock=lambda: OBSERVED_AT)


def _flatten(domains):
    flattened = []
    for domain in domains:
        flattened.append(domain)
        flattened.extend(_flatten(domain.children))
    return flattened


def _evidence(snapshot, fact_type: str):
    return next(item for item in snapshot.evidence if item.fact_type == fact_type)


def test_adapter_contract_exposes_capabilities_and_discover():
    adapter = _adapter(FakeSource(files=_linux_files()))

    assert isinstance(adapter, ExecutionDomainAdapter)
    assert adapter.capabilities().get("local_platform").status is CapabilityStatus.AVAILABLE
    assert adapter.capabilities().get("network_probe").status is CapabilityStatus.UNSUPPORTED
    assert adapter.discover().domains[0].kind is ExecutionDomainKind.LINUX


def test_windows_native_uses_platform_fact_without_linux_probes():
    snapshot = _adapter(
        FakeSource(os_name="nt", system="Windows", release="11", version="10.0.26100")
    ).discover()

    assert snapshot.status is CapabilityStatus.AVAILABLE
    assert snapshot.domains[0].kind is ExecutionDomainKind.WINDOWS
    assert snapshot.domains[0].children == ()
    assert snapshot.domains[0].evidence_ids == ("self-runtime:platform",)
    assert _evidence(snapshot, "probe.linux_files").status is CapabilityStatus.UNSUPPORTED


def test_plain_linux_host_is_not_mislabeled_as_wsl_or_container():
    snapshot = _adapter(FakeSource(files=_linux_files())).discover()

    assert snapshot.status is CapabilityStatus.AVAILABLE
    assert snapshot.domains[0].kind is ExecutionDomainKind.LINUX
    assert snapshot.domains[0].children == ()
    assert {item.kind for item in _flatten(snapshot.domains)} == {ExecutionDomainKind.LINUX}


@pytest.mark.parametrize(
    ("osrelease", "expected_label"),
    [
        ("4.4.0-19041-Microsoft", "WSL1"),
        ("5.15.153.1-microsoft-standard-WSL2", "WSL2"),
    ],
)
def test_wsl_versions_are_classified_from_kernel_and_interop(osrelease, expected_label):
    files = _linux_files(
        **{
            "/proc/sys/kernel/osrelease": osrelease,
            "/proc/version": f"Linux version {osrelease}",
        }
    )
    snapshot = _adapter(
        FakeSource(environment={"WSL_INTEROP": "must-not-be-recorded"}, files=files)
    ).discover()

    windows = snapshot.domains[0]
    wsl = windows.children[0]
    assert windows.kind is ExecutionDomainKind.WINDOWS
    assert wsl.kind is ExecutionDomainKind.WSL
    assert expected_label in (wsl.label or "")
    assert set(wsl.evidence_ids) >= {
        "self-runtime:wsl-interop",
        "self-runtime:kernel-osrelease",
    }
    generation = wsl.capabilities.get("wsl_generation")
    assert generation.status is CapabilityStatus.AVAILABLE
    assert generation.reason_code == f"{expected_label}_KERNEL_SIGNATURE"
    assert set(generation.evidence_ids) == {
        "self-runtime:kernel-osrelease",
        "self-runtime:kernel-version",
    }


def test_docker_cgroup_v1_uses_multiple_independent_signals():
    files = _linux_files(
        **{
            "/.dockerenv": b"",
            "/proc/1/cgroup": "11:memory:/docker/0123456789abcdef\n",
        }
    )
    snapshot = _adapter(FakeSource(files=files)).discover()

    container = snapshot.domains[0].children[0]
    assert container.kind is ExecutionDomainKind.CONTAINER
    assert "Docker" in (container.label or "")
    assert container.confidence is not None and container.confidence > 0.8
    assert set(container.evidence_ids) >= {
        "self-runtime:dockerenv",
        "self-runtime:cgroup-init",
    }


def test_cgroup_v2_without_docker_text_uses_dockerenv_and_mount_signal():
    files = _linux_files(
        **{
            "/.dockerenv": b"",
            "/proc/1/cgroup": "0::/\n",
            "/proc/self/cgroup": "0::/\n",
            "/proc/self/mountinfo": "24 23 0:22 / / rw - overlay overlay rw\n",
        }
    )
    snapshot = _adapter(FakeSource(files=files)).discover()

    container = snapshot.domains[0].children[0]
    assert container.kind is ExecutionDomainKind.CONTAINER
    assert container.confidence is not None and container.confidence > 0.8
    assert "self-runtime:mountinfo" in container.evidence_ids


def test_podman_containerenv_is_distinct_from_docker():
    files = _linux_files(
        **{
            "/run/.containerenv": b"",
            "/proc/self/cgroup": "0::/user.slice/libpod-abcdef.scope\n",
        }
    )
    snapshot = _adapter(FakeSource(files=files)).discover()

    container = snapshot.domains[0].children[0]
    assert container.kind is ExecutionDomainKind.CONTAINER
    assert "Podman" in (container.label or "")
    assert "Docker" not in (container.label or "")


def test_wsl2_and_docker_remain_nested_instead_of_mutually_exclusive():
    files = _linux_files(
        **{
            "/proc/sys/kernel/osrelease": "5.15.153.1-microsoft-standard-WSL2",
            "/proc/version": "Linux version 5.15.153.1-microsoft-standard-WSL2",
            "/.dockerenv": b"",
            "/proc/self/cgroup": "0::/docker/abcdef\n",
        }
    )
    snapshot = _adapter(
        FakeSource(environment={"WSL_INTEROP": "hidden"}, files=files)
    ).discover()

    windows = snapshot.domains[0]
    wsl = windows.children[0]
    container = wsl.children[0]
    assert [windows.kind, wsl.kind, container.kind] == [
        ExecutionDomainKind.WINDOWS,
        ExecutionDomainKind.WSL,
        ExecutionDomainKind.CONTAINER,
    ]


def test_lone_dockerenv_conflicting_with_readable_host_signals_is_degraded():
    snapshot = _adapter(
        FakeSource(files=_linux_files(**{"/.dockerenv": b""}))
    ).discover()

    container = snapshot.domains[0].children[0]
    assert snapshot.status is CapabilityStatus.DEGRADED
    assert container.confidence is not None and container.confidence <= 0.5
    assert "self-runtime:dockerenv" in container.evidence_ids
    assert _evidence(snapshot, "container.cgroup_init").status is CapabilityStatus.AVAILABLE
    assert _evidence(snapshot, "container.mountinfo").status is CapabilityStatus.AVAILABLE


def test_missing_proc_is_not_an_error_but_limits_the_snapshot():
    snapshot = _adapter(FakeSource()).discover()

    assert snapshot.domains[0].kind is ExecutionDomainKind.LINUX
    assert snapshot.status is CapabilityStatus.DEGRADED
    assert _evidence(snapshot, "kernel.osrelease").status is CapabilityStatus.NOT_PRESENT
    assert snapshot.errors == ()


def test_permission_error_is_structured_and_other_facts_survive():
    files = _linux_files(
        **{"/proc/self/status": PermissionError("value must not be serialized")}
    )
    snapshot = _adapter(FakeSource(files=files)).discover()

    evidence = _evidence(snapshot, "process.security_status")
    assert snapshot.status is CapabilityStatus.DEGRADED
    assert snapshot.domains[0].kind is ExecutionDomainKind.LINUX
    assert evidence.status is CapabilityStatus.PERMISSION_DENIED
    assert evidence.error is not None
    assert evidence.error.code is DiscoveryErrorCode.PERMISSION_DENIED
    assert "value must not be serialized" not in json.dumps(snapshot.to_dict())


def test_non_utf8_probe_data_is_error_without_aborting_discovery():
    snapshot = _adapter(
        FakeSource(files=_linux_files(**{"/proc/version": b"\xff\xfe"}))
    ).discover()

    evidence = _evidence(snapshot, "kernel.proc_version")
    assert snapshot.status is CapabilityStatus.DEGRADED
    assert snapshot.domains[0].kind is ExecutionDomainKind.LINUX
    assert evidence.status is CapabilityStatus.ERROR
    assert evidence.error is not None
    assert evidence.error.code is DiscoveryErrorCode.INVALID_DATA


def test_security_status_fields_are_read_as_structured_values():
    status = "NoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t00000000a80425fb\n"
    snapshot = _adapter(
        FakeSource(files=_linux_files(**{"/proc/self/status": status}))
    ).discover()

    evidence = _evidence(snapshot, "process.security_status")
    assert evidence.value["fields"] == {
        "cap_eff": "00000000a80425fb",
        "no_new_privs": 1,
        "seccomp": 2,
    }
    assert evidence.sanitized is True


def test_one_probe_exception_does_not_discard_other_local_facts():
    files = _linux_files(**{"/proc/1/cgroup": OSError("private failure detail")})
    snapshot = _adapter(FakeSource(files=files)).discover()

    assert snapshot.domains[0].kind is ExecutionDomainKind.LINUX
    assert snapshot.status is CapabilityStatus.DEGRADED
    assert _evidence(snapshot, "container.cgroup_init").status is CapabilityStatus.ERROR
    assert _evidence(snapshot, "platform.identity").status is CapabilityStatus.AVAILABLE


def test_unknown_platform_never_becomes_available_without_supporting_evidence():
    snapshot = _adapter(
        FakeSource(os_name="mystery", system="Plan9", release="unknown", version="unknown")
    ).discover()

    assert snapshot.status is CapabilityStatus.UNKNOWN
    assert snapshot.domains[0].kind is ExecutionDomainKind.UNKNOWN
    assert snapshot.domains[0].capabilities.get("self_visible").is_available is False


def test_major_domain_conclusions_reference_snapshot_evidence():
    files = _linux_files(
        **{
            "/proc/sys/kernel/osrelease": "5.15.153.1-microsoft-standard-WSL2",
            "/proc/version": "Linux version 5.15.153.1-microsoft-standard-WSL2",
            "/.dockerenv": b"",
            "/proc/self/cgroup": "0::/docker/abcdef\n",
        }
    )
    snapshot = _adapter(
        FakeSource(environment={"WSL_INTEROP": "hidden"}, files=files)
    ).discover()
    evidence_ids = {item.evidence_id for item in snapshot.evidence}

    for domain in _flatten(snapshot.domains):
        assert domain.evidence_ids
        assert set(domain.evidence_ids) <= evidence_ids


def test_sensitive_probe_fields_are_rejected_with_machine_warning():
    status = (
        "NoNewPrivs:\t1\n"
        "Seccomp:\t2\n"
        "CapEff:\t00000000a80425fb\n"
        "Api_Key:\tdo-not-store-this\n"
        "Command_Line:\ttool --token do-not-store-this\n"
    )
    snapshot = _adapter(
        FakeSource(
            environment={"WSL_INTEROP": "super-secret-token"},
            files=_linux_files(**{"/proc/self/status": status}),
        )
    ).discover()

    security = _evidence(snapshot, "process.security_status")
    encoded = json.dumps(snapshot.to_dict(), sort_keys=True).lower()
    assert "SENSITIVE_FIELD_REJECTED" in security.value["warnings"]
    assert "do-not-store-this" not in encoded
    assert "super-secret-token" not in encoded
    assert "api_key" not in encoded
    assert "command_line" not in encoded
    assert "--token" not in encoded


def test_sensitive_platform_text_is_rejected_instead_of_serialized():
    snapshot = _adapter(
        FakeSource(
            release="6.8 api_key=do-not-store-this",
            version="build https://example.invalid/private",
            files=_linux_files(),
        )
    ).discover()

    platform_evidence = _evidence(snapshot, "platform.identity")
    encoded = json.dumps(snapshot.to_dict(), sort_keys=True).lower()
    assert platform_evidence.value["release"] == "[REDACTED]"
    assert platform_evidence.value["version"] == "[REDACTED]"
    assert set(platform_evidence.value["warnings"]) == {
        "REMOTE_URL_REJECTED",
        "SENSITIVE_FIELD_REJECTED",
    }
    assert "do-not-store-this" not in encoded
    assert "https://" not in encoded


def test_discovery_stays_offline_when_network_and_provider_calls_are_forbidden(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("offline discovery attempted a forbidden call")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "agentguard.ai.provider" or name.startswith("agentguard.ai.provider."):
            fail()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    snapshot = _adapter(FakeSource(files=_linux_files())).discover()

    assert snapshot.domains[0].kind is ExecutionDomainKind.LINUX
    assert all(item.reliability is not EvidenceReliability.UNKNOWN for item in snapshot.evidence)
