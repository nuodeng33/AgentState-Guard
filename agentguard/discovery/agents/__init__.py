"""Public R4-P3A Agent discovery contracts."""

from . import launcher_identity
from .models import (
    AgentRole,
    ExecutableIdentityKind,
    ProcessFact,
    ProcessRelationship,
    ProcessState,
    ProcessWarningCode,
    ProcessWorkspaceAuthority,
    WorkspaceCandidate,
    WorkspacePathKind,
    WorkspaceSource,
    make_process_instance_id,
    normalize_process_create_time,
)
from .processes import (
    ProcessAccessDeniedError,
    ProcessBackend,
    ProcessBackendUnavailableError,
    ProcessCollectionResult,
    ProcessCollector,
    ProcessCollectorFailure,
    ProcessHandle,
    ProcessZombieError,
    build_process_relationships,
)
from .psutil_backend import PsutilProcessBackend, PsutilProcessHandle
from .workspaces import (
    deduplicate_workspace_candidates,
    unavailable_workspace_candidate,
    workspace_candidate_from_path,
)

__all__ = [
    "AgentRole",
    "ExecutableIdentityKind",
    "ProcessAccessDeniedError",
    "ProcessBackend",
    "ProcessBackendUnavailableError",
    "ProcessCollectionResult",
    "ProcessCollector",
    "ProcessCollectorFailure",
    "ProcessFact",
    "ProcessHandle",
    "ProcessRelationship",
    "ProcessState",
    "ProcessWarningCode",
    "ProcessWorkspaceAuthority",
    "ProcessZombieError",
    "PsutilProcessBackend",
    "PsutilProcessHandle",
    "WorkspaceCandidate",
    "WorkspacePathKind",
    "WorkspaceSource",
    "build_process_relationships",
    "deduplicate_workspace_candidates",
    "launcher_identity",
    "make_process_instance_id",
    "normalize_process_create_time",
    "unavailable_workspace_candidate",
    "workspace_candidate_from_path",
]
