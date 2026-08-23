"""Host-side sandbox-observation collector.

Launches the transient observer helper against a real Docker sandbox via
the Docker CLI (no SDK, no socket mount needed where the CLI works):

    docker run --rm
        --pid=container:<id>            # shared PID namespace (read /proc)
        --network container:<id>        # shared netns (/proc/net/tcp)
        --volumes-from <id>             # sandbox workspace volumes
        --entrypoint python3 -i=false <asg image> -   # helper via stdin

The helper emits one bounded JSON object per line; this collector maps
them to Evidence Ledger events (additive EventType values; readers that
filter by value are unaffected) and appends them through the existing
EvidenceLedger. Nothing is written inside the target sandbox and the
helper container removes itself (--rm).

Design boundaries kept deliberately:
- The observer is an evidence producer into the existing spine — no
  second database, no second authority model.
- Identity/admission policy stays host-side (Batch #1 maps); the helper
  reports bounded process facts (argv0 basename + token count), never a
  full command line.
- Network facts are sandbox-scoped (SANDBOX_NETWORK_OBSERVED); no
  process→socket attribution is claimed in V1.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HELPER_SOURCE = Path(__file__).with_name("helper.py")

_KNOWN_KINDS = frozenset(
    {
        "observer_hello",
        "observer_degraded",
        "observer_bye",
        "process_snapshot",
        "process_started",
        "process_exited",
        "file_activity",
        "network_established",
    }
)


@dataclass(frozen=True)
class SandboxObservation:
    """One bounded observation line plus the sandbox it came from."""

    execution_domain_id: str
    kind: str
    fields: dict[str, Any]

    def event_type(self) -> str:
        if self.kind in ("process_started", "process_snapshot"):
            return "SANDBOX_PROCESS_STARTED"
        if self.kind == "process_exited":
            return "SANDBOX_PROCESS_EXITED"
        if self.kind == "file_activity":
            return "OBSERVED_CHANGE"
        if self.kind == "network_established":
            return "SANDBOX_NETWORK_OBSERVED"
        return "SANDBOX_OBSERVER_LIFECYCLE"

    def result(self) -> str:
        return "AVAILABLE" if self.kind != "observer_degraded" else "DEGRADED"


def helper_command(
    container_id: str,
    *,
    image: str,
    docker: tuple[str, ...] = ("docker",),
) -> list[str]:
    """Docker argv that runs the observer helper against one sandbox.

    The helper program itself is delivered on stdin (``python3 -``), so no
    image bind mount is required; production bakes the same script into
    the ASG observer image and drops the stdin hop.
    """
    suffix = hashlib.sha256(container_id.encode()).hexdigest()[:6]
    return [
        *docker,
        "run",
        "--rm",
        "-i",
        f"--name=asg-observer-{container_id[:12]}-{suffix}",
        f"--pid=container:{container_id}",
        f"--network=container:{container_id}",
        f"--volumes-from={container_id}",
        "--entrypoint=python3",
        image,
        "-",
    ]


def launch_observer(
    container_id: str,
    *,
    image: str,
    workspaces: tuple[str, ...] = (),
    max_seconds: int = 60,
    poll_ms: int = 120,
    timeout: int | None = None,
    docker: tuple[str, ...] = ("docker",),
) -> subprocess.Popen[bytes]:
    """Start the helper container; its stdout carries the JSONL stream."""
    helper_argv = ["--poll-ms", str(poll_ms), "--max-seconds", str(max_seconds)]
    for root in workspaces:
        helper_argv += ["--workspace", root]
    command = helper_command(container_id, image=image, docker=docker) + helper_argv
    source = _HELPER_SOURCE.read_bytes()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    process.stdin.write(source)
    process.stdin.close()
    return process


def parse_stream(
    execution_domain_id: str, stream: Iterator[bytes]
) -> Iterator[SandboxObservation]:
    """Yield bounded observations from the helper's JSONL stdout."""
    for raw in stream:
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        kind = payload.get("kind")
        if kind not in _KNOWN_KINDS:
            continue
        fields = {
            key: value
            for key, value in payload.items()
            if key not in ("kind",) and _bounded_value(value)
        }
        yield SandboxObservation(
            execution_domain_id=execution_domain_id,
            kind=str(kind),
            fields=fields,
        )


def _bounded_value(value: Any) -> bool:
    if isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 512
    if isinstance(value, list):
        return len(value) <= 16 and all(_bounded_value(item) for item in value)
    return False


def to_evidence_event(
    observation: SandboxObservation,
    *,
    recorded_at: datetime | None = None,
) -> Any:
    """Map one observation to an EvidenceEvent (lazy model import)."""
    from agentguard.evidence.models import EventFamily, EventType

    family_map = {
        "SANDBOX_PROCESS_STARTED": EventFamily.DISCOVERY,
        "SANDBOX_PROCESS_EXITED": EventFamily.DISCOVERY,
        "SANDBOX_NETWORK_OBSERVED": EventFamily.DISCOVERY,
        "SANDBOX_OBSERVER_LIFECYCLE": EventFamily.DISCOVERY,
        "OBSERVED_CHANGE": EventFamily.CHANGE,
    }
    event_type_name = observation.event_type()
    event_type = EventType(event_type_name)
    now = recorded_at or datetime.now(UTC)
    ts = observation.fields.get("ts")
    observed_at = datetime.fromtimestamp(float(ts), tz=UTC) if isinstance(ts, (int, float)) else now
    payload = {
        "kind": observation.kind,
        **{key: value for key, value in observation.fields.items() if key != "ts"},
    }
    return _build_event(
        event_family=family_map[event_type_name],
        event_type=event_type,
        result=observation.result(),
        observed_at=observed_at,
        recorded_at=now,
        execution_domain_id=observation.execution_domain_id,
        payload=payload,
    )


def _build_event(
    *,
    event_family: Any,
    event_type: Any,
    result: str,
    observed_at: datetime,
    recorded_at: datetime,
    execution_domain_id: str,
    payload: dict[str, Any],
) -> Any:
    from agentguard.evidence.models import EvidenceEvent

    material = json.dumps(
        {"ts": observed_at.isoformat(), **payload}, sort_keys=True, default=str
    )
    event_id = f"sandbox-{hashlib.sha256((execution_domain_id + material).encode()).hexdigest()[:24]}"
    return EvidenceEvent(
        schema_version=1,
        event_id=event_id,
        recorded_at=recorded_at,
        observed_at=observed_at,
        event_family=event_family,
        event_type=event_type,
        source="asg-sandbox-observer",
        result=result,
        execution_domain_id=execution_domain_id,
        supervision_session_id=None,
        transaction_id=None,
        checkpoint_id=None,
        subject_ref=str(payload.get("relpath") or payload.get("pid") or execution_domain_id)[:128],
        evidence_refs=(),
        payload_safe=payload,
    )


def record_observations(
    db_path: Path,
    observations: list[SandboxObservation],
) -> list[str]:
    """Append observations to the existing Evidence Ledger; return event ids."""
    from agentguard.evidence.ledger import EvidenceLedger
    from agentguard.storage.db import StateDB

    ledger = EvidenceLedger()
    db = StateDB(db_path)
    db.connect()
    try:
        event_ids: list[str] = []
        with db.transaction() as conn:
            for observation in observations:
                event = to_evidence_event(observation)
                receipt = ledger.append(conn, event)
                event_ids.append(receipt.event_id)
        return event_ids
    finally:
        db.close()


__all__ = [
    "SandboxObservation",
    "helper_command",
    "launch_observer",
    "parse_stream",
    "record_observations",
    "to_evidence_event",
]
