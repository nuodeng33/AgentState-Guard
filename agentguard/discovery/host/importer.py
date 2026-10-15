"""Strict, offline Host Probe JSON importer."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from ..capabilities import (
    CapabilityAssessment,
    CapabilityStatus,
    DomainCapabilities,
    EvidenceReliability,
)
from ..errors import DiscoveryError, DiscoveryErrorCode, redact_text
from ..models import ExecutionDomainDescriptor, ExecutionDomainKind, ProbeEvidence
from .models import (
    HostFreshness,
    HostImportResult,
    HostProbeEnvelope,
    HostProbeWarning,
    HostSourceKind,
    HostTrustLevel,
    HostWarningCode,
    evaluate_freshness,
)

DEFAULT_TTL_SECONDS = 60
MAX_TTL_SECONDS = 300
STALE_GRACE_SECONDS = 60
MAX_FUTURE_SKEW_SECONDS = 5
DEFAULT_MAX_INPUT_BYTES = 65_536

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_URL_RE = re.compile(r"(?:https?|wss?)://[^\s]+", re.IGNORECASE)
_SENSITIVE_MARKERS = {
    "secret": "SECRET",
    "token": "TOKEN",
    "apikey": "API_KEY",
    "password": "PASSWORD",
    "credential": "CREDENTIAL",
    "environment": "ENVIRONMENT",
    "commandline": "COMMAND_LINE",
    "cmdline": "COMMAND_LINE",
    "privatekey": "PRIVATE_KEY",
    "remoteurl": "REMOTE_URL",
}
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "probe_version",
    "probe_id",
    "source_domain_id",
    "source_kind",
    "source_binding_id",
    "trust_level",
    "issued_at",
    "observed_at",
    "expires_at",
    "ttl_seconds",
    "sequence",
    "host",
    "capabilities",
    "evidence",
    "errors",
    "sanitized",
    "warnings",
}
_REQUIRED_FIELDS = {
    "schema_version",
    "probe_version",
    "probe_id",
    "source_domain_id",
    "source_kind",
    "issued_at",
    "observed_at",
    "sequence",
    "host",
    "capabilities",
    "evidence",
    "errors",
    "sanitized",
    "warnings",
}
_ALLOWED_CAPABILITIES = {
    "container_summary",
    "docker_cli",
    "docker_daemon",
    "filesystem_read",
    "host_docker_daemon",
    "host_os",
    "self_visible",
    "workspace_mapping",
    "wsl_generation",
}
_ALLOWED_FACT_TYPES = {
    "host.container_summary",
    "host.docker_cli",
    "host.docker_daemon",
    "host.import",
    "host.os",
    "host.workspace_mapping",
}
_ALLOWED_ERROR_REASON_CODES = {
    "HOST_DOCKER_PERMISSION_DENIED",
    "HOST_DOCKER_UNREACHABLE",
    "HOST_PERMISSION_DENIED",
    "HOST_PROBE_PARTIAL",
}
_ALLOWED_CAPABILITY_REASON_CODES = {
    "CLI_PRESENT",
    "DAEMON_AVAILABLE",
    "DAEMON_UNREACHABLE",
    "HOST_CONTAINER_SUMMARY",
    "HOST_OS_OBSERVED",
    "HOST_PERMISSION_DENIED",
    "HOST_PROBE_NOT_SELF_VISIBLE",
    "HOST_WORKSPACE_MAPPING",
    "NOT_PROBED",
    "PERMISSION_DENIED",
    "READ_ONLY_AVAILABLE",
}


class _InvalidPayload(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class HostProbeImporter:
    """Validate, sanitize, and source-bind one Host Probe envelope."""

    def __init__(self, *, max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES) -> None:
        if max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be positive")
        self.max_input_bytes = max_input_bytes

    def import_payload(
        self,
        payload: object,
        *,
        now: datetime,
    ) -> HostImportResult:
        warnings: list[HostProbeWarning] = []
        try:
            raw = self._decode_payload(payload)
            cleaned = _sanitize_value(raw, warnings)
            if not isinstance(cleaned, Mapping):
                raise _InvalidPayload("EXPECTED_JSON_OBJECT")
            envelope = self._build_envelope(cleaned, now, warnings)
            freshness = evaluate_freshness(
                envelope,
                now,
                stale_grace_seconds=STALE_GRACE_SECONDS,
            )
            return HostImportResult(
                freshness=freshness,
                envelope=envelope,
                warnings=tuple(warnings),
            )
        except _InvalidPayload as exc:
            return _invalid_result(exc.reason_code, warnings)
        except (TypeError, ValueError, OverflowError):
            return _invalid_result("INVALID_DATA", warnings)

    def import_cached_mapping(
        self,
        data: Mapping[str, Any],
        *,
        now: datetime,
    ) -> HostImportResult:
        """Read a normalized cache record without trusting Python objects."""

        warnings: list[HostProbeWarning] = []
        try:
            encoded = _canonical_json_bytes(data)
            if len(encoded) > self.max_input_bytes:
                raise _InvalidPayload("INPUT_TOO_LARGE")
            cleaned = _sanitize_value(data, warnings)
            if warnings:
                raise _InvalidPayload("CACHE_RECORD_REJECTED")
            envelope = HostProbeEnvelope.from_dict(cleaned)
            if envelope.schema_version != HostProbeEnvelope.CURRENT_SCHEMA_VERSION:
                raise _InvalidPayload("INCOMPATIBLE_SCHEMA")
            if not envelope.sanitized:
                raise _InvalidPayload("CACHE_RECORD_NOT_SANITIZED")
            _validate_temporal_bounds(envelope, now)
            freshness = evaluate_freshness(
                envelope,
                now,
                stale_grace_seconds=STALE_GRACE_SECONDS,
            )
            return HostImportResult(freshness=freshness, envelope=envelope)
        except _InvalidPayload as exc:
            return _invalid_result(exc.reason_code, warnings)
        except (TypeError, ValueError, OverflowError):
            return _invalid_result("INVALID_CACHE_RECORD", warnings)

    def _decode_payload(self, payload: object) -> Mapping[str, Any]:
        if isinstance(payload, bytes):
            if len(payload) > self.max_input_bytes:
                raise _InvalidPayload("INPUT_TOO_LARGE")
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _InvalidPayload("INVALID_ENCODING") from exc
            return self._parse_json(text)
        if isinstance(payload, str):
            encoded = payload.encode("utf-8")
            if len(encoded) > self.max_input_bytes:
                raise _InvalidPayload("INPUT_TOO_LARGE")
            return self._parse_json(payload)
        if isinstance(payload, Mapping):
            try:
                encoded = _canonical_json_bytes(payload)
            except (TypeError, ValueError) as exc:
                raise _InvalidPayload("INVALID_JSON_VALUE") from exc
            if len(encoded) > self.max_input_bytes:
                raise _InvalidPayload("INPUT_TOO_LARGE")
            return payload
        raise _InvalidPayload("EXPECTED_JSON_OBJECT")

    @staticmethod
    def _parse_json(text: str) -> Mapping[str, Any]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _InvalidPayload("INVALID_JSON") from exc
        if not isinstance(data, Mapping):
            raise _InvalidPayload("EXPECTED_JSON_OBJECT")
        return data

    def _build_envelope(
        self,
        data: Mapping[str, Any],
        now: datetime,
        warnings: list[HostProbeWarning],
    ) -> HostProbeEnvelope:
        missing = sorted(_REQUIRED_FIELDS.difference(data))
        if missing:
            raise _InvalidPayload("MISSING_REQUIRED_FIELD")
        if "ttl_seconds" not in data and "expires_at" not in data:
            raise _InvalidPayload("MISSING_TTL")
        if str(data["schema_version"]) != HostProbeEnvelope.CURRENT_SCHEMA_VERSION:
            raise _InvalidPayload("INCOMPATIBLE_SCHEMA")
        if set(data).difference(_TOP_LEVEL_FIELDS):
            _append_warning(
                warnings,
                HostWarningCode.UNKNOWN_FIELD_IGNORED,
                "TOP_LEVEL",
            )

        probe_version = _safe_token(data["probe_version"], "INVALID_PROBE_VERSION")
        probe_id = _safe_token(data["probe_id"], "INVALID_PROBE_ID")
        source_domain_id = _safe_token(
            data["source_domain_id"],
            "INVALID_SOURCE_DOMAIN_ID",
        )
        binding_value = data.get("source_binding_id")
        source_binding_id = (
            _safe_token(binding_value, "INVALID_SOURCE_BINDING_ID")
            if binding_value is not None
            else None
        )
        source_kind = _coerce_enum(
            HostSourceKind,
            data["source_kind"],
            warnings,
        )
        trust_level = _coerce_enum(
            HostTrustLevel,
            data.get("trust_level", HostTrustLevel.UNVERIFIED.value),
            warnings,
        )
        if (
            trust_level is HostTrustLevel.SOURCE_BOUND
            and source_binding_id is None
        ):
            trust_level = HostTrustLevel.UNVERIFIED
            _append_warning(
                warnings,
                HostWarningCode.SOURCE_BINDING_UNVERIFIED,
                "SOURCE_BINDING",
            )
        issued_at = _parse_datetime(data["issued_at"])
        observed_at = _parse_datetime(data["observed_at"])
        current = _as_utc(now)
        future_limit = current + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
        if issued_at > future_limit or observed_at > future_limit:
            raise _InvalidPayload("FUTURE_TIMESTAMP")
        if observed_at > issued_at:
            raise _InvalidPayload("OBSERVED_AFTER_ISSUED")
        ttl_seconds, expires_at = _ttl_bounds(data, observed_at)
        sequence = data["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise _InvalidPayload("INVALID_SEQUENCE")

        imported_evidence_id = f"host-import:{probe_id}"
        evidence, evidence_ids = _parse_evidence(
            data["evidence"],
            probe_id,
            imported_evidence_id,
            source_domain_id,
            trust_level,
            observed_at,
            warnings,
        )
        capabilities = _parse_capabilities(
            data["capabilities"],
            evidence_ids,
            imported_evidence_id,
            warnings,
        )
        host = _parse_domain(
            data["host"],
            evidence_ids,
            imported_evidence_id,
            warnings,
        )
        errors = _parse_errors(data["errors"])
        imported_warnings = _parse_input_warnings(data["warnings"])
        for item in imported_warnings:
            _append_warning(warnings, item.code, item.field_category)
        if data.get("sanitized") is not True:
            _append_warning(warnings, HostWarningCode.INPUT_SANITIZED, None)

        return HostProbeEnvelope(
            schema_version=HostProbeEnvelope.CURRENT_SCHEMA_VERSION,
            probe_version=probe_version,
            probe_id=probe_id,
            source_domain_id=source_domain_id,
            source_kind=source_kind,
            source_binding_id=source_binding_id,
            trust_level=trust_level,
            issued_at=issued_at,
            observed_at=observed_at,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            sequence=sequence,
            host=host,
            capabilities=capabilities,
            evidence=evidence,
            errors=errors,
            sanitized=True,
            warnings=tuple(warnings),
            imported=True,
            self_visible=False,
            source_authenticated=False,
            imported_evidence_id=imported_evidence_id,
        )


def _invalid_result(
    reason_code: str,
    warnings: Sequence[HostProbeWarning],
) -> HostImportResult:
    return HostImportResult(
        freshness=HostFreshness.INVALID,
        error=DiscoveryError(
            code=DiscoveryErrorCode.INVALID_DATA,
            message="Host Probe payload was rejected",
            collector="host_probe_importer",
            source="HOST_PROBE",
            retryable=False,
            details={"reason_code": reason_code},
        ),
        warnings=tuple(warnings),
    )


def _normalize_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _sensitive_category(key: object) -> str | None:
    normalized = _normalize_key(key)
    for marker, category in _SENSITIVE_MARKERS.items():
        if marker in normalized:
            return category
    if normalized == "env" or normalized.startswith("envvar"):
        return "ENVIRONMENT"
    return None


def _sanitize_value(value: Any, warnings: list[HostProbeWarning]) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _InvalidPayload("NON_FINITE_NUMBER")
        return value
    if isinstance(value, str):
        cleaned = redact_text(value)
        if cleaned != value or _URL_RE.search(value):
            _append_warning(warnings, HostWarningCode.REMOTE_URL_REJECTED, "URL")
        return cleaned
    if isinstance(value, Mapping):
        cleaned_mapping: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            category = _sensitive_category(key)
            if category is not None:
                _append_warning(
                    warnings,
                    HostWarningCode.SENSITIVE_FIELD_REJECTED,
                    category,
                )
                continue
            cleaned_mapping[key] = _sanitize_value(item, warnings)
        return cleaned_mapping
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, warnings) for item in value]
    raise _InvalidPayload("NON_JSON_VALUE")


def _append_warning(
    warnings: list[HostProbeWarning],
    code: HostWarningCode,
    field_category: str | None,
) -> None:
    item = HostProbeWarning(code=code, field_category=field_category)
    if item not in warnings:
        warnings.append(item)


def _safe_token(value: object, reason_code: str) -> str:
    text = str(value)
    if not _TOKEN_RE.fullmatch(text):
        raise _InvalidPayload(reason_code)
    return text


def _coerce_enum(
    enum_type: type[HostSourceKind | HostTrustLevel],
    value: object,
    warnings: list[HostProbeWarning],
) -> HostSourceKind | HostTrustLevel:
    try:
        return enum_type(str(value))
    except ValueError:
        _append_warning(
            warnings,
            HostWarningCode.UNKNOWN_ENUM_DOWNGRADED,
            enum_type.__name__.upper(),
        )
        return enum_type.UNKNOWN


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise _InvalidPayload("INVALID_TIMESTAMP")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _InvalidPayload("INVALID_TIMESTAMP") from exc
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _InvalidPayload("TIMESTAMP_NOT_UTC_AWARE")
    return value.astimezone(UTC)


def _ttl_bounds(
    data: Mapping[str, Any],
    observed_at: datetime,
) -> tuple[int, datetime]:
    ttl_value = data.get("ttl_seconds")
    expires_value = data.get("expires_at")
    if ttl_value is None:
        expires_at = _parse_datetime(expires_value)
        ttl_float = (expires_at - observed_at).total_seconds()
        if not ttl_float.is_integer():
            raise _InvalidPayload("INVALID_TTL")
        ttl_seconds = int(ttl_float)
    else:
        if isinstance(ttl_value, bool) or not isinstance(ttl_value, int):
            raise _InvalidPayload("INVALID_TTL")
        ttl_seconds = ttl_value
        expires_at = observed_at + timedelta(seconds=ttl_seconds)
        if expires_value is not None and _parse_datetime(expires_value) != expires_at:
            raise _InvalidPayload("TTL_MISMATCH")
    if ttl_seconds <= 0:
        raise _InvalidPayload("INVALID_TTL")
    if ttl_seconds > MAX_TTL_SECONDS:
        raise _InvalidPayload("TTL_TOO_LONG")
    return ttl_seconds, expires_at


def _parse_evidence(
    raw: object,
    probe_id: str,
    imported_evidence_id: str,
    source_domain_id: str,
    trust_level: HostTrustLevel,
    observed_at: datetime,
    warnings: list[HostProbeWarning],
) -> tuple[tuple[ProbeEvidence, ...], Mapping[str, str]]:
    if not isinstance(raw, list):
        raise _InvalidPayload("INVALID_EVIDENCE")
    records: list[ProbeEvidence] = []
    id_map: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise _InvalidPayload("INVALID_EVIDENCE")
        original_id = _safe_token(item.get("evidence_id"), "INVALID_EVIDENCE_ID")
        if original_id in id_map:
            raise _InvalidPayload("DUPLICATE_EVIDENCE_ID")
        fact_type = str(item.get("fact_type", ""))
        if fact_type not in _ALLOWED_FACT_TYPES:
            _append_warning(
                warnings,
                HostWarningCode.UNKNOWN_FIELD_IGNORED,
                "EVIDENCE_FACT_TYPE",
            )
            continue
        imported_id = f"host:{probe_id}:{original_id}"
        id_map[original_id] = imported_id
        status_value = str(item.get("status", "UNKNOWN"))
        reliability_value = str(item.get("reliability", "UNKNOWN"))
        if status_value not in CapabilityStatus._value2member_map_:
            _append_warning(
                warnings,
                HostWarningCode.UNKNOWN_ENUM_DOWNGRADED,
                "CAPABILITY_STATUS",
            )
        if reliability_value not in EvidenceReliability._value2member_map_:
            _append_warning(
                warnings,
                HostWarningCode.UNKNOWN_ENUM_DOWNGRADED,
                "EVIDENCE_RELIABILITY",
            )
        imported_reliability, imported_confidence = _cap_imported_fact(
            reliability_value,
            item.get("confidence"),
            trust_level,
        )
        records.append(
            ProbeEvidence.from_dict(
                {
                    "evidence_id": imported_id,
                    "collector": "host_probe_importer",
                    "source": "HOST_PROBE",
                    "observed_at": item.get("observed_at", observed_at.isoformat()),
                    "fact_type": fact_type,
                    "value": item.get("value"),
                    "summary": None,
                    "reliability": imported_reliability,
                    "confidence": imported_confidence,
                    "status": status_value,
                    "error": None,
                    "sanitized": True,
                }
            )
        )
    marker_reliability = (
        EvidenceReliability.MEDIUM
        if trust_level is HostTrustLevel.SOURCE_BOUND
        else EvidenceReliability.LOW
    )
    records.append(
        ProbeEvidence(
            evidence_id=imported_evidence_id,
            collector="host_probe_importer",
            source="HOST_PROBE",
            observed_at=observed_at,
            fact_type="host.import",
            value={
                "imported": True,
                "self_visible": False,
                "source_authenticated": False,
                "source_domain_id": source_domain_id,
                "trust_level": trust_level.value,
            },
            reliability=marker_reliability,
            confidence=0.7 if trust_level is HostTrustLevel.SOURCE_BOUND else 0.3,
            status=CapabilityStatus.AVAILABLE,
            sanitized=True,
        )
    )
    return tuple(records), id_map


def _parse_capabilities(
    raw: object,
    evidence_ids: Mapping[str, str],
    imported_evidence_id: str,
    warnings: list[HostProbeWarning],
) -> DomainCapabilities:
    if not isinstance(raw, Mapping):
        raise _InvalidPayload("INVALID_CAPABILITIES")
    parsed: dict[str, CapabilityAssessment] = {}
    for raw_name, value in raw.items():
        name = str(raw_name)
        if name not in _ALLOWED_CAPABILITIES:
            _append_warning(
                warnings,
                HostWarningCode.UNSUPPORTED_CAPABILITY_IGNORED,
                "CAPABILITY",
            )
            continue
        if not isinstance(value, Mapping):
            raise _InvalidPayload("INVALID_CAPABILITY")
        status_text = str(value.get("status", "UNKNOWN"))
        if status_text not in CapabilityStatus._value2member_map_:
            _append_warning(
                warnings,
                HostWarningCode.UNKNOWN_ENUM_DOWNGRADED,
                "CAPABILITY_STATUS",
            )
        status = CapabilityStatus._value2member_map_.get(
            status_text,
            CapabilityStatus.UNKNOWN,
        )
        refs = _remap_refs(value.get("evidence_ids"), evidence_ids, imported_evidence_id)
        reason_code = None
        if value.get("reason_code") is not None:
            reason_code = _safe_token(
                value["reason_code"],
                "INVALID_CAPABILITY_REASON_CODE",
            )
            if reason_code not in _ALLOWED_CAPABILITY_REASON_CODES:
                reason_code = "HOST_PROBE_CAPABILITY_REPORTED"
        if name == "self_visible":
            status = CapabilityStatus.UNKNOWN
            reason_code = "HOST_PROBE_NOT_SELF_VISIBLE"
        parsed[name] = CapabilityAssessment(
            status=status,
            reason_code=reason_code,
            evidence_ids=refs,
            confidence=value.get("confidence"),
            error=None,
        )
    return DomainCapabilities(parsed)


def _parse_domain(
    raw: object,
    evidence_ids: Mapping[str, str],
    imported_evidence_id: str,
    warnings: list[HostProbeWarning],
) -> ExecutionDomainDescriptor:
    if not isinstance(raw, Mapping):
        raise _InvalidPayload("INVALID_HOST_DESCRIPTOR")
    domain_id = _safe_token(raw.get("domain_id"), "INVALID_HOST_DOMAIN_ID")
    kind_text = str(raw.get("kind", "UNKNOWN"))
    if kind_text not in ExecutionDomainKind._value2member_map_:
        _append_warning(
            warnings,
            HostWarningCode.UNKNOWN_ENUM_DOWNGRADED,
            "EXECUTION_DOMAIN_KIND",
        )
    kind = ExecutionDomainKind._value2member_map_.get(
        kind_text,
        ExecutionDomainKind.UNKNOWN,
    )
    capabilities = _parse_capabilities(
        raw.get("capabilities", {}),
        evidence_ids,
        imported_evidence_id,
        warnings,
    )
    if "self_visible" not in capabilities.assessments:
        combined = dict(capabilities.assessments)
        combined["self_visible"] = CapabilityAssessment(
            status=CapabilityStatus.UNKNOWN,
            reason_code="HOST_PROBE_NOT_SELF_VISIBLE",
            evidence_ids=(imported_evidence_id,),
        )
        capabilities = DomainCapabilities(combined)
    children_raw = raw.get("children", [])
    if not isinstance(children_raw, list):
        raise _InvalidPayload("INVALID_HOST_CHILDREN")
    children = tuple(
        _parse_domain(
            item,
            evidence_ids,
            imported_evidence_id,
            warnings,
        )
        for item in children_raw
    )
    refs = _remap_refs(raw.get("evidence_ids"), evidence_ids, imported_evidence_id)
    return ExecutionDomainDescriptor(
        domain_id=domain_id,
        kind=kind,
        label=str(raw["label"]) if raw.get("label") is not None else None,
        capabilities=capabilities,
        children=children,
        evidence_ids=refs,
        confidence=raw.get("confidence"),
    )


def _remap_refs(
    raw: object,
    evidence_ids: Mapping[str, str],
    imported_evidence_id: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    if isinstance(raw, (list, tuple)):
        refs.extend(evidence_ids[item] for item in raw if item in evidence_ids)
    if imported_evidence_id not in refs:
        refs.append(imported_evidence_id)
    return tuple(refs)


def _parse_errors(raw: object) -> tuple[DiscoveryError, ...]:
    if not isinstance(raw, list):
        raise _InvalidPayload("INVALID_ERRORS")
    errors: list[DiscoveryError] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise _InvalidPayload("INVALID_ERRORS")
        code_text = str(item.get("code", "UNKNOWN"))
        code = DiscoveryErrorCode._value2member_map_.get(
            code_text,
            DiscoveryErrorCode.UNKNOWN,
        )
        details = item.get("details")
        reason_code = None
        if isinstance(details, Mapping) and details.get("reason_code") is not None:
            reason_code = _safe_token(
                details["reason_code"],
                "INVALID_ERROR_REASON_CODE",
            )
            if reason_code not in _ALLOWED_ERROR_REASON_CODES:
                reason_code = "HOST_PROBE_REPORTED_ERROR"
        errors.append(
            DiscoveryError(
                code=code,
                message="Imported Host Probe reported a structured error",
                collector="host_probe_importer",
                source="HOST_PROBE",
                retryable=bool(item.get("retryable", False)),
                details={"reason_code": reason_code} if reason_code else {},
            )
        )
    return tuple(errors)


def _parse_input_warnings(raw: object) -> tuple[HostProbeWarning, ...]:
    if not isinstance(raw, list):
        raise _InvalidPayload("INVALID_WARNINGS")
    parsed: list[HostProbeWarning] = []
    for item in raw:
        code_value = item.get("code") if isinstance(item, Mapping) else item
        try:
            code = HostWarningCode(str(code_value))
        except ValueError:
            code = HostWarningCode.UNKNOWN
        parsed.append(HostProbeWarning(code=code))
    return tuple(parsed)


def _validate_temporal_bounds(envelope: HostProbeEnvelope, now: datetime) -> None:
    future_limit = _as_utc(now) + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
    if envelope.issued_at > future_limit or envelope.observed_at > future_limit:
        raise _InvalidPayload("FUTURE_TIMESTAMP")
    if envelope.observed_at > envelope.issued_at:
        raise _InvalidPayload("OBSERVED_AFTER_ISSUED")
    if envelope.ttl_seconds > MAX_TTL_SECONDS:
        raise _InvalidPayload("TTL_TOO_LONG")
    expected_expiry = envelope.observed_at + timedelta(seconds=envelope.ttl_seconds)
    if envelope.expires_at != expected_expiry:
        raise _InvalidPayload("TTL_MISMATCH")


def _cap_imported_fact(
    reliability_value: str,
    confidence_value: object,
    trust_level: HostTrustLevel,
) -> tuple[EvidenceReliability, float | None]:
    reliability = EvidenceReliability._value2member_map_.get(
        reliability_value,
        EvidenceReliability.UNKNOWN,
    )
    ceiling = (
        EvidenceReliability.MEDIUM
        if trust_level is HostTrustLevel.SOURCE_BOUND
        else EvidenceReliability.LOW
    )
    ranks = {
        EvidenceReliability.UNKNOWN: 0,
        EvidenceReliability.LOW: 1,
        EvidenceReliability.MEDIUM: 2,
        EvidenceReliability.HIGH: 3,
    }
    if ranks[reliability] > ranks[ceiling]:
        reliability = ceiling
    if confidence_value is None:
        return reliability, None
    confidence = float(confidence_value)
    confidence_ceiling = 0.7 if trust_level is HostTrustLevel.SOURCE_BOUND else 0.3
    return reliability, min(confidence, confidence_ceiling)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "DEFAULT_MAX_INPUT_BYTES",
    "DEFAULT_TTL_SECONDS",
    "MAX_FUTURE_SKEW_SECONDS",
    "MAX_TTL_SECONDS",
    "STALE_GRACE_SECONDS",
    "HostProbeImporter",
]
