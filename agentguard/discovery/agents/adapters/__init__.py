"""Explicit product adapters for bounded Agent discovery."""

from .cloudcli import (
    CloudCliAdapter,
    CloudCliDetectionLevel,
    CloudCliDiscoveryResult,
    CloudCliMarker,
    CloudCliMarkerKind,
    CloudCliMarkerSource,
    CloudCliMarkerVerification,
    create_verified_cloudcli_marker,
    parse_cloudcli_pid_marker,
)

__all__ = [
    "CloudCliAdapter",
    "CloudCliDetectionLevel",
    "CloudCliDiscoveryResult",
    "CloudCliMarker",
    "CloudCliMarkerKind",
    "CloudCliMarkerSource",
    "CloudCliMarkerVerification",
    "create_verified_cloudcli_marker",
    "parse_cloudcli_pid_marker",
]
