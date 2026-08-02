"""Thin adapter contract for current execution-domain discovery."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..capabilities import DomainCapabilities
from ..models import DiscoverySnapshot


@runtime_checkable
class ExecutionDomainAdapter(Protocol):
    """Collect read-only facts visible to the current process."""

    def capabilities(self) -> DomainCapabilities:
        """Describe the adapter's deliberately narrow probe surface."""

    def discover(self) -> DiscoverySnapshot:
        """Return a best-effort snapshot without crossing execution boundaries."""
