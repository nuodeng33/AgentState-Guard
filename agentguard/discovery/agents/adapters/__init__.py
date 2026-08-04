"""Explicit product adapters for bounded Agent discovery."""

from .ccr import (
    CcrAdapter,
    CcrDetectionLevel,
    CcrDiscoveryResult,
    CcrMarker,
    CcrMarkerKind,
    CcrMarkerSource,
    CcrMarkerVerification,
    create_verified_ccr_marker,
    parse_ccr_pid_marker,
)
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
    "CcrAdapter",
    "CcrDetectionLevel",
    "CcrDiscoveryResult",
    "CcrMarker",
    "CcrMarkerKind",
    "CcrMarkerSource",
    "CcrMarkerVerification",
    "CloudCliAdapter",
    "CloudCliDetectionLevel",
    "CloudCliDiscoveryResult",
    "CloudCliMarker",
    "CloudCliMarkerKind",
    "CloudCliMarkerSource",
    "CloudCliMarkerVerification",
    "create_verified_ccr_marker",
    "create_verified_cloudcli_marker",
    "parse_ccr_pid_marker",
    "parse_cloudcli_pid_marker",
]
