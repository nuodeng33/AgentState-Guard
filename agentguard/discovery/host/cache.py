"""Bounded, atomic, strict-JSON cache for imported Host Probe envelopes."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..errors import DiscoveryError, DiscoveryErrorCode
from .importer import HostProbeImporter
from .models import HostFreshness, HostProbeEnvelope

DEFAULT_MAX_RECORDS = 32
DEFAULT_MAX_RECORD_BYTES = 65_536
DEFAULT_MAX_TOTAL_BYTES = 524_288
CACHE_SCHEMA_VERSION = "1.0"


class HostCacheWriteStatus(str, Enum):
    STORED = "STORED"
    IDEMPOTENT = "IDEMPOTENT"
    CONFLICT = "CONFLICT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class HostCacheLimits:
    max_records: int = DEFAULT_MAX_RECORDS
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES

    def __post_init__(self) -> None:
        if min(self.max_records, self.max_record_bytes, self.max_total_bytes) <= 0:
            raise ValueError("Host Probe cache limits must be positive")


@dataclass(frozen=True)
class HostCachedRecord:
    envelope: HostProbeEnvelope
    freshness: HostFreshness

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_dict(),
            "freshness": self.freshness.value,
        }


@dataclass(frozen=True)
class HostCacheReadResult:
    status: HostFreshness
    records: tuple[HostCachedRecord, ...] = ()
    error: DiscoveryError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "records": [item.to_dict() for item in self.records],
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class HostCacheWriteResult:
    status: HostCacheWriteStatus
    error: DiscoveryError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "error": self.error.to_dict() if self.error else None,
        }


class HostProbeCache:
    """Store only the latest bounded envelope for each source domain."""

    def __init__(
        self,
        path: Path,
        *,
        limits: HostCacheLimits | None = None,
    ) -> None:
        self.path = Path(path)
        self.limits = limits or HostCacheLimits()
        self._importer = HostProbeImporter(
            max_input_bytes=self.limits.max_record_bytes,
        )

    def load(self, *, now) -> HostCacheReadResult:
        if not self.path.exists():
            return HostCacheReadResult(status=HostFreshness.NOT_PRESENT)
        try:
            encoded = self.path.read_bytes()
        except OSError:
            return _invalid_read("CACHE_READ_FAILED")
        if len(encoded) > self.limits.max_total_bytes:
            return _invalid_read("CACHE_TOTAL_TOO_LARGE")
        try:
            data = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _invalid_read("CACHE_INVALID_JSON")
        if not isinstance(data, Mapping):
            return _invalid_read("CACHE_INVALID_ROOT")
        if data.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
            return _invalid_read("CACHE_INCOMPATIBLE_SCHEMA")
        raw_records = data.get("records")
        if not isinstance(raw_records, list):
            return _invalid_read("CACHE_INVALID_RECORDS")
        if len(raw_records) > self.limits.max_records:
            return _invalid_read("CACHE_RECORD_LIMIT_EXCEEDED")

        records: list[HostCachedRecord] = []
        source_ids: set[str] = set()
        for item in raw_records:
            if not isinstance(item, Mapping):
                return _invalid_read("CACHE_INVALID_RECORD")
            try:
                record_bytes = _canonical_json_bytes(item)
            except (TypeError, ValueError):
                return _invalid_read("CACHE_INVALID_RECORD")
            if len(record_bytes) > self.limits.max_record_bytes:
                return _invalid_read("RECORD_TOO_LARGE")
            imported = self._importer.import_cached_mapping(item, now=now)
            if imported.envelope is None:
                reason = (
                    str(imported.error.details.get("reason_code"))
                    if imported.error
                    else "INVALID_CACHE_RECORD"
                )
                return _invalid_read(reason)
            if imported.envelope.source_domain_id in source_ids:
                return _invalid_read("DUPLICATE_SOURCE_DOMAIN")
            source_ids.add(imported.envelope.source_domain_id)
            records.append(
                HostCachedRecord(
                    envelope=imported.envelope,
                    freshness=imported.freshness,
                )
            )
        return HostCacheReadResult(
            status=_aggregate_freshness(records),
            records=tuple(records),
        )

    def store(self, envelope: HostProbeEnvelope, *, now) -> HostCacheWriteResult:
        try:
            record_bytes = _canonical_json_bytes(envelope.to_dict())
        except (TypeError, ValueError):
            return _write_error(HostCacheWriteStatus.INVALID, "INVALID_RECORD")
        if len(record_bytes) > self.limits.max_record_bytes:
            return _write_error(HostCacheWriteStatus.INVALID, "RECORD_TOO_LARGE")

        existing = self.load(now=now)
        if existing.status is HostFreshness.INVALID:
            return _write_error(HostCacheWriteStatus.INVALID, "CACHE_INVALID")
        envelopes = [item.envelope for item in existing.records]
        current_index = next(
            (
                index
                for index, item in enumerate(envelopes)
                if item.source_domain_id == envelope.source_domain_id
            ),
            None,
        )
        if current_index is not None:
            current = envelopes[current_index]
            if current.probe_id == envelope.probe_id:
                if _canonical_json_bytes(current.to_dict()) == record_bytes:
                    return HostCacheWriteResult(status=HostCacheWriteStatus.IDEMPOTENT)
                return _write_error(
                    HostCacheWriteStatus.CONFLICT,
                    "PROBE_ID_CONTENT_CONFLICT",
                )
            if envelope.sequence <= current.sequence:
                return _write_error(
                    HostCacheWriteStatus.OUT_OF_ORDER,
                    "SEQUENCE_OUT_OF_ORDER",
                )
            if envelope.observed_at <= current.observed_at:
                return _write_error(
                    HostCacheWriteStatus.OUT_OF_ORDER,
                    "OBSERVED_AT_OUT_OF_ORDER",
                )
            envelopes[current_index] = envelope
        else:
            if len(envelopes) >= self.limits.max_records:
                return _write_error(
                    HostCacheWriteStatus.CAPACITY_EXCEEDED,
                    "CACHE_RECORD_LIMIT_EXCEEDED",
                )
            envelopes.append(envelope)

        cache_bytes = _canonical_json_bytes(
            {
                "cache_schema_version": CACHE_SCHEMA_VERSION,
                "records": [
                    item.to_dict()
                    for item in sorted(envelopes, key=lambda value: value.source_domain_id)
                ],
            }
        )
        if len(cache_bytes) > self.limits.max_total_bytes:
            return _write_error(
                HostCacheWriteStatus.CAPACITY_EXCEEDED,
                "CACHE_TOTAL_TOO_LARGE",
            )
        if not self._atomic_write(cache_bytes):
            return _write_error(HostCacheWriteStatus.INVALID, "ATOMIC_WRITE_FAILED")
        return HostCacheWriteResult(status=HostCacheWriteStatus.STORED)

    def _atomic_write(self, data: bytes) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        descriptor = -1
        temporary_path = ""
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            os.write(descriptor, data)
            os.fsync(descriptor)
            try:
                os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
            except (AttributeError, OSError):
                pass
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary_path, self.path)
            temporary_path = ""
            try:
                os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            _sync_directory(self.path.parent)
            return True
        except OSError:
            return False
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass


def _aggregate_freshness(records: list[HostCachedRecord]) -> HostFreshness:
    if not records:
        return HostFreshness.NOT_PRESENT
    states = {item.freshness for item in records}
    if HostFreshness.FRESH in states:
        return HostFreshness.FRESH
    if HostFreshness.STALE in states:
        return HostFreshness.STALE
    return HostFreshness.EXPIRED


def _invalid_read(reason_code: str) -> HostCacheReadResult:
    return HostCacheReadResult(
        status=HostFreshness.INVALID,
        error=_cache_error(reason_code),
    )


def _write_error(
    status: HostCacheWriteStatus,
    reason_code: str,
) -> HostCacheWriteResult:
    return HostCacheWriteResult(
        status=status,
        error=_cache_error(reason_code),
    )


def _cache_error(reason_code: str) -> DiscoveryError:
    return DiscoveryError(
        code=DiscoveryErrorCode.INVALID_DATA,
        message="Host Probe cache operation was rejected",
        collector="host_probe_cache",
        source="HOST_PROBE_CACHE",
        retryable=False,
        details={"reason_code": reason_code},
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_MAX_RECORD_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "HostCacheLimits",
    "HostCacheReadResult",
    "HostCacheWriteResult",
    "HostCacheWriteStatus",
    "HostCachedRecord",
    "HostProbeCache",
]
