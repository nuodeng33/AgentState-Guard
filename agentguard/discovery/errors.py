"""Structured, machine-readable failures for Runtime Discovery."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping


class DiscoveryErrorCode(str, Enum):
    """Stable failure categories; messages are explanatory, not authoritative."""

    UNREACHABLE = "UNREACHABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_PRESENT = "NOT_PRESENT"
    TIMEOUT = "TIMEOUT"
    INVALID_DATA = "INVALID_DATA"
    COLLECTOR_FAILURE = "COLLECTOR_FAILURE"
    UNKNOWN = "UNKNOWN"


_FORBIDDEN_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "command_line",
    "commandline",
    "environment",
    "private_key",
    "remote_url",
    "password",
    "passwd",
    "secret",
    "token",
)
_URL_RE = re.compile(r"(?:https?|wss?)://[^\s]+", re.IGNORECASE)


def _coerce_error_code(value: object) -> DiscoveryErrorCode:
    try:
        return value if isinstance(value, DiscoveryErrorCode) else DiscoveryErrorCode(str(value))
    except ValueError:
        return DiscoveryErrorCode.UNKNOWN


def redact_text(value: object) -> str:
    """Remove complete remote URLs from a human-readable field."""

    return _URL_RE.sub("[REDACTED_URL]", str(value))


def sanitize_json_value(value: Any) -> Any:
    """Return a JSON-compatible value with forbidden secret-bearing fields removed."""

    if value is None or isinstance(value, (bool, int, str)):
        return redact_text(value) if isinstance(value, str) else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Discovery values must use finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        cleaned: Dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            if any(marker in normalized for marker in _FORBIDDEN_KEY_MARKERS):
                continue
            cleaned[key] = sanitize_json_value(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(item) for item in value]
    raise TypeError(f"Discovery value is not JSON-compatible: {type(value).__name__}")


@dataclass(frozen=True)
class DiscoveryError:
    """A discovery failure with a stable code and optional sanitized context."""

    code: DiscoveryErrorCode
    message: str
    collector: str = ""
    source: str = ""
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _coerce_error_code(self.code))
        object.__setattr__(self, "message", redact_text(self.message))
        object.__setattr__(self, "source", redact_text(self.source))
        object.__setattr__(self, "details", sanitize_json_value(self.details))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "collector": self.collector,
            "source": self.source,
            "retryable": self.retryable,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DiscoveryError":
        return cls(
            code=_coerce_error_code(data.get("code", DiscoveryErrorCode.UNKNOWN)),
            message=str(data.get("message", "")),
            collector=str(data.get("collector", "")),
            source=str(data.get("source", "")),
            retryable=bool(data.get("retryable", False)),
            details=data.get("details", {}) if isinstance(data.get("details", {}), Mapping) else {},
        )
