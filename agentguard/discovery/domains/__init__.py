"""Current execution-domain adapters."""

from .base import ExecutionDomainAdapter
from .self_runtime import SelfRuntimeAdapter
from .windows import WindowsAdapter, WindowsWslListResult, WindowsWslProbeResult

__all__ = [
    "ExecutionDomainAdapter",
    "SelfRuntimeAdapter",
    "WindowsAdapter",
    "WindowsWslListResult",
    "WindowsWslProbeResult",
]
