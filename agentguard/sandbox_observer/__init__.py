"""Sandbox supervision observation: transient ASG-owned observer.

The observer is an evidence producer for the existing spine (Evidence
Ledger, Supervision, Checkpoint). It is not a second core, database, or
authority model.
"""

from .collector import (
    SandboxObservation,
    helper_command,
    launch_observer,
    parse_stream,
    record_observations,
    to_evidence_event,
)

__all__ = [
    "SandboxObservation",
    "helper_command",
    "launch_observer",
    "parse_stream",
    "record_observations",
    "to_evidence_event",
]
