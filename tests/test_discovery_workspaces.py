"""R4-P3A workspace candidate and path-boundary tests."""

from datetime import UTC, datetime, timedelta

import pytest

from agentguard.discovery import CapabilityStatus
from agentguard.discovery.agents import (
    ProcessCollector,
    ProcessWarningCode,
    WorkspacePathKind,
    WorkspaceSource,
    deduplicate_workspace_candidates,
    unavailable_workspace_candidate,
    workspace_candidate_from_path,
)

NOW = datetime(2026, 8, 2, 14, 0, tzinfo=UTC)


def test_process_cwd_produces_sanitized_workspace_candidate():
    candidate = workspace_candidate_from_path(
        path="/home/private-user/project",
        source=WorkspaceSource.PROCESS_CWD,
        execution_domain_id="linux-host",
        evidence_refs=("cwd:410",),
        home_path="/home/private-user",
    )

    assert candidate.path_hint == "~/project"
    assert candidate.path_kind is WorkspacePathKind.REDACTED
    assert candidate.access_status is CapabilityStatus.AVAILABLE
    assert ProcessWarningCode.PATH_REDACTED in candidate.warnings
    assert "private-user" not in str(candidate.to_dict())


def test_windows_user_home_is_minimized_without_reading_environment():
    candidate = workspace_candidate_from_path(
        path=r"C:\Users\private-user\project",
        source=WorkspaceSource.USER_INPUT,
        execution_domain_id="windows-host",
        evidence_refs=("user-input:1",),
    )

    assert candidate.path_hint == r"~\project"
    assert candidate.path_kind is WorkspacePathKind.REDACTED
    assert "private-user" not in str(candidate.to_dict())


def test_cwd_permission_denied_remains_structured_and_pathless():
    candidate = unavailable_workspace_candidate(
        source=WorkspaceSource.PROCESS_CWD,
        execution_domain_id="linux-host",
        evidence_refs=("cwd:410",),
        access_status=CapabilityStatus.PERMISSION_DENIED,
    )

    assert candidate.path_hint is None
    assert candidate.access_status is CapabilityStatus.PERMISSION_DENIED
    assert ProcessWarningCode.CWD_UNAVAILABLE in candidate.warnings


def test_windows_and_wsl_paths_are_not_automatically_merged():
    windows = workspace_candidate_from_path(
        path=r"C:\work\project",
        source=WorkspaceSource.KNOWN_LOGICAL_PATH,
        execution_domain_id="windows-host",
        evidence_refs=("known:windows",),
    )
    wsl = workspace_candidate_from_path(
        path="/mnt/c/work/project",
        source=WorkspaceSource.KNOWN_LOGICAL_PATH,
        execution_domain_id="wsl-runtime",
        evidence_refs=("known:wsl",),
    )

    candidates = deduplicate_workspace_candidates((windows, wsl))

    assert len(candidates) == 2
    assert windows.candidate_id != wsl.candidate_id


def test_wsl_unc_display_path_is_not_linux_native_write_path():
    candidate = workspace_candidate_from_path(
        path=r"\\wsl$\Ubuntu\home\private-user\project",
        source=WorkspaceSource.HOST_PROBE_MAPPING,
        execution_domain_id="windows-host",
        evidence_refs=("host-map:1",),
    )

    assert candidate.path_kind is WorkspacePathKind.DISPLAY_ONLY
    assert ProcessWarningCode.WSL_DISPLAY_PATH in candidate.warnings
    assert "private-user" not in (candidate.path_hint or "")


def test_git_root_is_only_a_candidate_not_proof_of_agent_binding():
    candidate = workspace_candidate_from_path(
        path="/srv/project",
        source=WorkspaceSource.GIT_ROOT_CANDIDATE,
        execution_domain_id="linux-host",
        evidence_refs=("git-marker:1",),
        git_root_candidate=True,
    )

    assert candidate.git_root_candidate is True
    assert candidate.is_final_binding is False


def test_workspace_candidate_requires_source_evidence():
    with pytest.raises(ValueError, match="evidence"):
        workspace_candidate_from_path(
            path="/srv/project",
            source=WorkspaceSource.USER_INPUT,
            execution_domain_id="linux-host",
            evidence_refs=(),
        )


def test_remote_url_is_rejected_without_preserving_original():
    candidate = workspace_candidate_from_path(
        path="https://example.invalid/private/project?token=forbidden",
        source=WorkspaceSource.USER_INPUT,
        execution_domain_id="linux-host",
        evidence_refs=("user-input:2",),
    )

    assert candidate.path_hint == "[REDACTED_PATH]"
    assert candidate.path_kind is WorkspacePathKind.REDACTED
    encoded = str(candidate.to_dict())
    assert "example.invalid" not in encoded
    assert "forbidden" not in encoded


class CwdHandle:
    pid = 410

    def __init__(self, cwd):
        self._cwd = cwd

    def parent_pid(self):
        return None

    def executable_basename(self):
        return "tool"

    def executable_identity_digest(self):
        return "sha256:" + "a" * 64

    def create_time(self):
        return NOW - timedelta(minutes=1)

    def cwd(self):
        if isinstance(self._cwd, BaseException):
            raise self._cwd
        return self._cwd

    def fixed_boolean_facts(self):
        return {}


class CwdBackend:
    def __init__(self, handle):
        self._handle = handle

    def iter_processes(self):
        return (self._handle,)


def _collect_cwd(value):
    return ProcessCollector(
        backend=CwdBackend(CwdHandle(value)),
        execution_domain_id="linux-host",
        collector="cwd-fixture",
        clock=lambda: NOW,
        home_path="/home/private-user",
    ).collect()


def test_process_collector_emits_workspace_candidate_from_readable_cwd():
    result = _collect_cwd("/home/private-user/project")

    assert result.workspace_candidates[0].path_hint == "~/project"
    assert result.workspace_candidates[0].source is WorkspaceSource.PROCESS_CWD


def test_process_collector_keeps_cwd_permission_denied_without_losing_fact():
    result = _collect_cwd(PermissionError("private cwd"))

    assert len(result.facts) == 1
    assert result.status is CapabilityStatus.DEGRADED
    candidate = result.workspace_candidates[0]
    assert candidate.access_status is CapabilityStatus.PERMISSION_DENIED
    assert candidate.path_hint is None
    assert "private cwd" not in str(result.to_dict())
