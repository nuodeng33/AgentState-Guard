"""Public R4-P3A Agent discovery contracts."""

from .base import (
    AgentAdapterRegistry,
    AgentAdapterRunResult,
    AgentDiscoveryAdapter,
)
from .classifier import (
    AgentSignatureRule,
    classify_executable,
    classify_process,
)
from .models import (
    AgentCandidateType,
    AgentClassification,
    AgentRole,
    ExecutableIdentityKind,
    ProcessFact,
    ProcessRelationship,
    ProcessState,
    ProcessWarningCode,
    WorkspaceCandidate,
    WorkspacePathKind,
    WorkspaceSource,
    make_process_instance_id,
    normalize_process_create_time,
)
from .processes import (
    ProcessBackend,
    ProcessCollectionResult,
    ProcessCollector,
    ProcessHandle,
    build_process_relationships,
)
from .workspaces import (
    deduplicate_workspace_candidates,
    unavailable_workspace_candidate,
    workspace_candidate_from_path,
)

__all__ = [
    "AgentAdapterRegistry",
    "AgentAdapterRunResult",
    "AgentCandidateType",
    "AgentClassification",
    "AgentDiscoveryAdapter",
    "AgentRole",
    "AgentSignatureRule",
    "ExecutableIdentityKind",
    "ProcessBackend",
    "ProcessCollectionResult",
    "ProcessCollector",
    "ProcessFact",
    "ProcessHandle",
    "ProcessRelationship",
    "ProcessState",
    "ProcessWarningCode",
    "WorkspaceCandidate",
    "WorkspacePathKind",
    "WorkspaceSource",
    "build_process_relationships",
    "classify_executable",
    "classify_process",
    "deduplicate_workspace_candidates",
    "make_process_instance_id",
    "normalize_process_create_time",
    "unavailable_workspace_candidate",
    "workspace_candidate_from_path",
]
