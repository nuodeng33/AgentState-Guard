"""Thin adapter contract for current execution-domain discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..capabilities import DomainCapabilities
from ..models import DiscoverySnapshot

if TYPE_CHECKING:
    from agentguard.recovery.contracts import RecoveryOperationResult, RecoveryRequest


@runtime_checkable
class ExecutionDomainAdapter(Protocol):
    """Collect read-only facts visible to the current process."""

    def capabilities(self) -> DomainCapabilities:
        """Describe the adapter's deliberately narrow probe surface."""

    def discover(self) -> DiscoverySnapshot:
        """Return a best-effort snapshot without crossing execution boundaries."""

    def snapshot(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Create a P6 recovery artifact only inside this execution domain."""

    def restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Route a P6 restore request without crossing execution boundaries."""

    def verify(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Verify a P6 recovery artifact without changing target files."""

    def test_restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Perform only an isolated P7 test restore inside this domain."""
