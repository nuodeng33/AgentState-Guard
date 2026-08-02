"""R4-P2C bounded Host Probe cache tests."""

import builtins
import json
from datetime import timedelta

from agentguard.discovery.host import HostProbeImporter
from tests.test_discovery_host_import import NOW, _valid_payload


def _envelope(payload=None):
    result = HostProbeImporter().import_payload(payload or _valid_payload(), now=NOW)
    assert result.envelope is not None
    return result.envelope


def _second_source_payload():
    payload = _valid_payload()
    payload["probe_id"] = "probe-002"
    payload["source_domain_id"] = "linux-host"
    payload["host"]["domain_id"] = "linux-host"
    payload["host"]["kind"] = "LINUX"
    payload["sequence"] = 1
    return payload


def test_missing_cache_is_not_present_not_host_absence(tmp_path):
    from agentguard.discovery.host import HostFreshness, HostProbeCache

    result = HostProbeCache(tmp_path / "host-cache.json").load(now=NOW)

    assert result.status is HostFreshness.NOT_PRESENT
    assert result.records == ()
    assert result.error is None


def test_cache_stores_strict_json_and_reads_fresh_record(tmp_path):
    from agentguard.discovery.host import (
        HostCacheWriteStatus,
        HostFreshness,
        HostProbeCache,
    )

    path = tmp_path / "host-cache.json"
    cache = HostProbeCache(path)

    stored = cache.store(_envelope(), now=NOW)
    loaded = cache.load(now=NOW)

    assert stored.status is HostCacheWriteStatus.STORED
    assert loaded.status is HostFreshness.FRESH
    assert loaded.records[0].envelope.probe_id == "probe-001"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["cache_schema_version"] == "1.0"
    assert isinstance(parsed["records"], list)


def test_freshness_transitions_from_fresh_to_stale_to_expired(tmp_path):
    from agentguard.discovery.host import HostFreshness, HostProbeCache

    cache = HostProbeCache(tmp_path / "host-cache.json")
    cache.store(_envelope(), now=NOW)

    fresh = cache.load(now=NOW + timedelta(seconds=50))
    stale = cache.load(now=NOW + timedelta(seconds=60))
    expired = cache.load(now=NOW + timedelta(seconds=121))

    assert fresh.status is HostFreshness.FRESH
    assert stale.status is HostFreshness.STALE
    assert expired.status is HostFreshness.EXPIRED


def test_freshness_exact_expiry_and_stale_grace_boundaries_are_inclusive(tmp_path):
    from agentguard.discovery.host import HostFreshness, HostProbeCache

    cache = HostProbeCache(tmp_path / "host-cache.json")
    cache.store(_envelope(), now=NOW)

    at_expiry = cache.load(now=NOW + timedelta(seconds=55))
    at_stale_grace = cache.load(now=NOW + timedelta(seconds=115))
    after_stale_grace = cache.load(
        now=NOW + timedelta(seconds=115, microseconds=1)
    )

    assert at_expiry.status is HostFreshness.FRESH
    assert at_stale_grace.status is HostFreshness.STALE
    assert after_stale_grace.status is HostFreshness.EXPIRED


def test_corrupt_cache_fails_closed_as_invalid(tmp_path):
    from agentguard.discovery.host import HostFreshness, HostProbeCache

    path = tmp_path / "host-cache.json"
    path.write_text('{"records":[', encoding="utf-8")

    result = HostProbeCache(path).load(now=NOW)

    assert result.status is HostFreshness.INVALID
    assert result.records == ()
    assert result.error.details["reason_code"] == "CACHE_INVALID_JSON"


def test_oversized_single_record_is_rejected_without_creating_cache(tmp_path):
    from agentguard.discovery.host import (
        HostCacheLimits,
        HostCacheWriteStatus,
        HostProbeCache,
    )

    path = tmp_path / "host-cache.json"
    limits = HostCacheLimits(max_records=8, max_record_bytes=100, max_total_bytes=4096)

    result = HostProbeCache(path, limits=limits).store(_envelope(), now=NOW)

    assert result.status is HostCacheWriteStatus.INVALID
    assert result.error.details["reason_code"] == "RECORD_TOO_LARGE"
    assert not path.exists()


def test_cache_record_count_is_bounded_without_evicting_existing_source(tmp_path):
    from agentguard.discovery.host import (
        HostCacheLimits,
        HostCacheWriteStatus,
        HostProbeCache,
    )

    cache = HostProbeCache(
        tmp_path / "host-cache.json",
        limits=HostCacheLimits(
            max_records=1,
            max_record_bytes=65_536,
            max_total_bytes=131_072,
        ),
    )
    first = cache.store(_envelope(), now=NOW)
    second = cache.store(_envelope(_second_source_payload()), now=NOW)
    loaded = cache.load(now=NOW)

    assert first.status is HostCacheWriteStatus.STORED
    assert second.status is HostCacheWriteStatus.CAPACITY_EXCEEDED
    assert [item.envelope.probe_id for item in loaded.records] == ["probe-001"]


def test_cache_total_size_is_bounded(tmp_path):
    from agentguard.discovery.host import (
        HostCacheLimits,
        HostCacheWriteStatus,
        HostProbeCache,
    )

    cache = HostProbeCache(
        tmp_path / "host-cache.json",
        limits=HostCacheLimits(
            max_records=8,
            max_record_bytes=65_536,
            max_total_bytes=128,
        ),
    )

    result = cache.store(_envelope(), now=NOW)

    assert result.status is HostCacheWriteStatus.CAPACITY_EXCEEDED
    assert result.error.details["reason_code"] == "CACHE_TOTAL_TOO_LARGE"


def test_atomic_replace_failure_preserves_previous_cache(tmp_path, monkeypatch):
    import agentguard.discovery.host.cache as cache_module
    from agentguard.discovery.host import HostCacheWriteStatus, HostProbeCache

    path = tmp_path / "host-cache.json"
    cache = HostProbeCache(path)
    assert cache.store(_envelope(), now=NOW).status is HostCacheWriteStatus.STORED
    previous_bytes = path.read_bytes()
    replacement = _second_source_payload()

    def fail_replace(_source, _target):
        raise OSError("controlled atomic replace failure")

    monkeypatch.setattr(cache_module.os, "replace", fail_replace)
    failed = cache.store(_envelope(replacement), now=NOW)

    assert failed.status is HostCacheWriteStatus.INVALID
    assert failed.error.details["reason_code"] == "ATOMIC_WRITE_FAILED"
    assert path.read_bytes() == previous_bytes


def test_identical_probe_id_replay_is_idempotent(tmp_path):
    from agentguard.discovery.host import HostCacheWriteStatus, HostProbeCache

    path = tmp_path / "host-cache.json"
    cache = HostProbeCache(path)
    envelope = _envelope()
    assert cache.store(envelope, now=NOW).status is HostCacheWriteStatus.STORED
    before = path.read_bytes()

    replay = cache.store(envelope, now=NOW)

    assert replay.status is HostCacheWriteStatus.IDEMPOTENT
    assert path.read_bytes() == before


def test_probe_replay_is_idempotent_across_json_key_order(tmp_path):
    from agentguard.discovery.host import HostCacheWriteStatus, HostProbeCache

    payload = _valid_payload()
    reversed_payload = dict(reversed(payload.items()))
    first = HostProbeImporter().import_payload(
        json.dumps(payload),
        now=NOW,
    )
    reordered = HostProbeImporter().import_payload(
        json.dumps(reversed_payload),
        now=NOW,
    )
    assert first.envelope is not None
    assert reordered.envelope is not None
    cache = HostProbeCache(tmp_path / "host-cache.json")

    stored = cache.store(first.envelope, now=NOW)
    replay = cache.store(reordered.envelope, now=NOW)

    assert stored.status is HostCacheWriteStatus.STORED
    assert replay.status is HostCacheWriteStatus.IDEMPOTENT


def test_same_probe_id_with_different_content_is_conflict(tmp_path):
    from agentguard.discovery.host import HostCacheWriteStatus, HostProbeCache

    cache = HostProbeCache(tmp_path / "host-cache.json")
    assert cache.store(_envelope(), now=NOW).status is HostCacheWriteStatus.STORED
    changed = _valid_payload()
    changed["host"]["label"] = "Different host label"

    result = cache.store(_envelope(changed), now=NOW)

    assert result.status is HostCacheWriteStatus.CONFLICT
    assert result.error.details["reason_code"] == "PROBE_ID_CONTENT_CONFLICT"


def test_lower_sequence_cannot_overwrite_newer_source_record(tmp_path):
    from agentguard.discovery.host import HostCacheWriteStatus, HostProbeCache

    cache = HostProbeCache(tmp_path / "host-cache.json")
    assert cache.store(_envelope(), now=NOW).status is HostCacheWriteStatus.STORED
    older = _valid_payload()
    older["probe_id"] = "probe-older-sequence"
    older["sequence"] = 6

    result = cache.store(_envelope(older), now=NOW)

    assert result.status is HostCacheWriteStatus.OUT_OF_ORDER
    assert result.error.details["reason_code"] == "SEQUENCE_OUT_OF_ORDER"


def test_older_observed_at_cannot_overwrite_newer_source_record(tmp_path):
    from agentguard.discovery.host import HostCacheWriteStatus, HostProbeCache

    cache = HostProbeCache(tmp_path / "host-cache.json")
    assert cache.store(_envelope(), now=NOW).status is HostCacheWriteStatus.STORED
    older = _valid_payload()
    older["probe_id"] = "probe-older-observation"
    older["sequence"] = 8
    older["observed_at"] = "2026-08-02T11:59:54Z"

    result = cache.store(_envelope(older), now=NOW)

    assert result.status is HostCacheWriteStatus.OUT_OF_ORDER
    assert result.error.details["reason_code"] == "OBSERVED_AT_OUT_OF_ORDER"


def test_tampered_cache_sensitive_field_is_invalid_not_silently_cleaned(tmp_path):
    from agentguard.discovery.host import HostFreshness, HostProbeCache

    path = tmp_path / "host-cache.json"
    cache = HostProbeCache(path)
    cache.store(_envelope(), now=NOW)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["evidence"][0]["value"]["api-key"] = "DO_NOT_PERSIST"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = cache.load(now=NOW)

    assert result.status is HostFreshness.INVALID
    assert result.records == ()
    assert result.error.details["reason_code"] == "CACHE_RECORD_REJECTED"
    assert "DO_NOT_PERSIST" not in json.dumps(result.to_dict(), sort_keys=True)


def test_host_pipeline_does_not_use_network_pickle_database_or_external_commands(
    tmp_path,
    monkeypatch,
):
    import pickle
    import socket
    import sqlite3
    import subprocess
    import urllib.request

    from agentguard.discovery.host import HostProbeCache, merge_host_probe_snapshot
    from tests.test_discovery_host_merge import _local_linux, _snapshot

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden boundary was used")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        forbidden_roots = {
            "docker",
            "httpx",
            "requests",
            "socket",
            "sqlite3",
            "subprocess",
            "urllib",
        }
        if name.split(".", 1)[0] in forbidden_roots:
            raise AssertionError(f"forbidden import: {name}")
        if name.startswith("agentguard.ai") or "tailscale" in name.lower():
            raise AssertionError(f"forbidden import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    imported = HostProbeImporter().import_payload(_valid_payload(), now=NOW)
    assert imported.envelope is not None
    cache = HostProbeCache(tmp_path / "host-cache.json")
    cache.store(imported.envelope, now=NOW)
    loaded = cache.load(now=NOW)
    merged = merge_host_probe_snapshot(_snapshot(_local_linux()), imported)

    assert loaded.records[0].envelope.probe_id == "probe-001"
    assert merged.domains[0].domain_id == "linux-host"
