"""Static Adapter Registry boundary for R4-P3A Agent discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..capabilities import CapabilityStatus
from ..errors import DiscoveryError, DiscoveryErrorCode
from .models import AgentClassification, ProcessFact


@runtime_checkable
class AgentDiscoveryAdapter(Protocol):
    adapter_id: str

    def discover(
        self,
        facts: tuple[ProcessFact, ...],
    ) -> tuple[AgentClassification, ...]: ...


@dataclass(frozen=True)
class AgentAdapterRunResult:
    candidates: tuple[AgentClassification, ...] = ()
    errors: tuple[DiscoveryError, ...] = ()
    status: CapabilityStatus = CapabilityStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [item.to_dict() for item in self.candidates],
            "errors": [item.to_dict() for item in self.errors],
            "status": self.status.value,
        }


class AgentAdapterRegistry:
    """Explicit, stable-order registry with no plugins or dynamic imports."""

    def __init__(self, adapters: tuple[AgentDiscoveryAdapter, ...] = ()) -> None:
        adapter_ids = tuple(str(item.adapter_id) for item in adapters)
        if any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item) is None
            for item in adapter_ids
        ):
            raise ValueError("adapter_id must be a stable non-empty identifier")
        if len(set(adapter_ids)) != len(adapter_ids):
            raise ValueError("adapter_id values must be unique")
        self._adapters = tuple(adapters)
        self._adapter_ids = adapter_ids

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return self._adapter_ids

    def discover(self, facts: tuple[ProcessFact, ...]) -> AgentAdapterRunResult:
        candidates: list[AgentClassification] = []
        errors: list[DiscoveryError] = []
        for adapter in self._adapters:
            try:
                candidates.extend(adapter.discover(facts))
            except Exception:  # noqa: BLE001 - adapter isolation is the registry contract
                errors.append(
                    DiscoveryError(
                        code=DiscoveryErrorCode.COLLECTOR_FAILURE,
                        message="Agent adapter discovery failed",
                        collector="agent-adapter-registry",
                        source="local-agent-adapter",
                        details={
                            "adapter_id": adapter.adapter_id,
                            "reason_code": "ADAPTER_DISCOVERY_FAILED",
                        },
                    )
                )
        if errors and candidates:
            status = CapabilityStatus.DEGRADED
        elif errors:
            status = CapabilityStatus.ERROR
        else:
            status = CapabilityStatus.AVAILABLE
        return AgentAdapterRunResult(
            candidates=tuple(candidates),
            errors=tuple(errors),
            status=status,
        )


__all__ = [
    "AgentAdapterRegistry",
    "AgentAdapterRunResult",
    "AgentDiscoveryAdapter",
]
