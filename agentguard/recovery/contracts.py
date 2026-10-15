"""Stable P6 recovery request and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agentguard.discovery.capabilities import CapabilityStatus


class RecoveryOperation(str, Enum):
    SNAPSHOT = "snapshot"
    RESTORE = "restore"
    VERIFY = "verify"
    TEST_RESTORE = "test_restore"
    DRILL_RESTORE = "drill_restore"


@dataclass(frozen=True)
class RecoveryRequest:
    operation: RecoveryOperation
    execution_domain_id: str
    target_path: Path | None = None
    checkpoint_id: str | None = None
    supervision_session_id: str | None = None
    user_approved: bool = False
    artifact: dict[str, Any] | None = None
    sandbox_path: Path | None = None
    drill_root: Path | None = None


@dataclass(frozen=True)
class RecoveryOperationResult:
    operation: RecoveryOperation
    status: CapabilityStatus
    reason_code: str
    execution_domain_id: str
    checkpoint_id: str | None = None
    manifest_digest: str | None = None
    evidence_refs: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    artifact: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(sorted(set(self.evidence_refs))))

    @property
    def ok(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE
