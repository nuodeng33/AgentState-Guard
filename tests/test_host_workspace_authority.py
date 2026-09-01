"""Host-native workspace authority remains exact internally and private externally."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentguard.discovery import (
    CapabilityAssessment,
    CapabilityStatus,
    DiscoverySnapshot,
    DomainCapabilities,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
)
from agentguard.discovery.agents import ProcessWorkspaceAuthority
from agentguard.discovery.product import ProductDiscoveryReport, ProductDiscoveryService
from agentguard.discovery.workspace_authority import resolve_host_workspace

NOW = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)


def _snapshot(*, docker: bool = False, wsl: bool = False) -> DiscoverySnapshot:
    evidence = ProbeEvidence(
        evidence_id="domain-evidence",
        collector="test-runtime",
        source="local",
        observed_at=NOW,
        fact_type="platform.current",
        value={"system": "Windows"},
        status=CapabilityStatus.AVAILABLE,
        sanitized=True,
    )
    return DiscoverySnapshot(
        snapshot_id="authority-snapshot",
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
                            evidence_ids=(evidence.evidence_id,),
                            confidence=0.95,
                        ),
                        "docker": CapabilityAssessment(
                            status=(
                                CapabilityStatus.AVAILABLE
                                if docker
                                else CapabilityStatus.NOT_PRESENT
                            ),
                            reason_code=(
                                "DOCKER_PRESENT" if docker else "DOCKER_NOT_PRESENT"
                            ),
                            evidence_ids=(evidence.evidence_id,),
                            confidence=0.95,
                        ),
                        "wsl": CapabilityAssessment(
                            status=(
                                CapabilityStatus.AVAILABLE
                                if wsl
                                else CapabilityStatus.NOT_PRESENT
                            ),
                            reason_code="WSL_PRESENT" if wsl else "WSL_NOT_PRESENT",
                            evidence_ids=(evidence.evidence_id,),
                            confidence=0.95,
                        ),
                    }
                ),
                evidence_ids=(evidence.evidence_id,),
                confidence=0.95,
            ),
        ),
        evidence=(evidence,),
        status=CapabilityStatus.AVAILABLE,
    )


def _authority(root: Path, index: int = 1) -> ProcessWorkspaceAuthority:
    return ProcessWorkspaceAuthority(
        process_instance_id=f"process-{index}",
        candidate_id=f"candidate-{index}",
        execution_domain_id="windows-current",
        cwd=root,
        evidence_refs=(f"process-evidence-{index}",),
        agent_id=f"agent-{index}",
    )


def _report(*roots: Path, docker: bool = False, wsl: bool = False):
    return ProductDiscoveryReport(
        snapshot=_snapshot(docker=docker, wsl=wsl),
        workspace_authorities=tuple(
            _authority(root, index) for index, root in enumerate(roots, 1)
        ),
    )


def test_same_git_root_codex_and_claude_resolve_one_host_scope(tmp_path):
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    first = root / "packages" / "one"
    second = root / "packages" / "two"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    resolved = resolve_host_workspace(
        _report(first, second),
        home_path=tmp_path,
        system_roots=(),
    )

    assert resolved.status == "BOUND"
    assert resolved.reason_code == "WORKSPACE_SCOPE_VERIFIED"
    assert resolved.root_path == root.resolve()
    assert resolved.agent_ids == ("agent-1", "agent-2")


def test_distinct_agent_roots_fail_closed(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    resolved = resolve_host_workspace(
        _report(first, second),
        home_path=tmp_path.parent,
        system_roots=(),
    )

    assert resolved.status == "UNAVAILABLE"
    assert resolved.reason_code == "WORKSPACE_SCOPE_AMBIGUOUS"
    assert resolved.root_path is None


def test_docker_and_wsl_absence_do_not_block_windows_scope(tmp_path):
    root = tmp_path / "native-workspace"
    root.mkdir()

    resolved = resolve_host_workspace(
        _report(root, docker=False, wsl=False),
        home_path=tmp_path,
        system_roots=(),
    )

    assert resolved.status == "BOUND"
    assert resolved.root_path == root.resolve()


def test_home_root_and_symlink_scope_fail_closed(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    home_result = resolve_host_workspace(
        _report(home),
        home_path=home,
        system_roots=(),
    )
    assert home_result.reason_code == "WORKSPACE_SCOPE_TOO_BROAD"

    link = home / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    link_result = resolve_host_workspace(
        _report(link),
        home_path=home,
        system_roots=(),
    )
    assert link_result.reason_code == "WORKSPACE_SCOPE_REPARSE_POINT"


def test_system_installation_subdirectory_is_not_a_workspace(tmp_path):
    program_files = tmp_path / "Program Files"
    install_directory = program_files / "Vendor" / "Agent App"
    install_directory.mkdir(parents=True)

    resolved = resolve_host_workspace(
        _report(install_directory),
        home_path=tmp_path / "Users" / "person",
        system_roots=(program_files,),
    )

    assert resolved.status == "UNAVAILABLE"
    assert resolved.reason_code == "WORKSPACE_SCOPE_TOO_BROAD"
    assert resolved.root_path is None


class _RuntimeAdapter:
    def discover(self) -> DiscoverySnapshot:
        return _snapshot()


class _Handle:
    def __init__(self, cwd: Path) -> None:
        self.pid = 41
        self._cwd = cwd

    def parent_pid(self):
        return 1

    def executable_basename(self):
        return "codex.exe"

    def create_time(self):
        return NOW - timedelta(minutes=1)

    def cwd(self):
        return str(self._cwd)

    def fixed_boolean_facts(self):
        return {}


class _Backend:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    def iter_processes(self):
        return (_Handle(self._cwd),)


def test_product_discovery_keeps_exact_path_out_of_serialized_snapshot(tmp_path):
    home = tmp_path / "private-user"
    workspace = home / "secret-project"
    workspace.mkdir(parents=True)
    service = ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_Backend(workspace),
        clock=lambda: NOW,
        home_path=str(home),
        host_domain_observers=(),
    )

    report = service.discover_with_authority()
    encoded = json.dumps(report.snapshot.to_dict())

    assert report.workspace_authorities[0].cwd == workspace
    assert str(workspace) not in encoded
    assert not hasattr(report, "to_dict")
