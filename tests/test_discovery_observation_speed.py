"""Observation remains truthful while sharing work and isolating slow probes."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from agentguard.discovery.catalog import (
    CatalogError,
    DiscoveryCatalog,
    SessionPageCache,
)
from agentguard.discovery.managed import ManagedDiscoveryService
from agentguard.recovery.protection_domains import DomainRegistry
from agentguard.storage.db import StateDB


def test_concurrent_page_reads_share_probe_but_later_reads_revalidate():
    pages = SessionPageCache(max_pages=2)
    entered, release, joined = threading.Event(), threading.Event(), threading.Event()
    count = 0

    def probe(previous):
        nonlocal count
        count += 1
        entered.set()
        assert release.wait(3)
        return {"revision": "one", "status": "AVAILABLE", "sessions": [{"session_id": "s"}]}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(pages.read, "page", probe)
        assert entered.wait(3)
        # Hook only the Future join, not production scheduling or filesystem I/O.
        pending = pages._pending["page"]
        original = pending.result

        def wait_for_result(*args, **kwargs):
            joined.set()
            return original(*args, **kwargs)

        pending.result = wait_for_result
        second = pool.submit(pages.read, "page", probe)
        assert joined.wait(3)
        release.set()
        a, b = first.result(3), second.result(3)
    assert count == 1
    a["sessions"].clear()
    assert b["sessions"]
    assert pages.read("page", probe)["sessions"]
    assert count == 2  # No TTL can conceal directory replacement.
    for key in ("two", "three"):
        pages.read(key, probe)
    assert len(pages._pages) == 2 and "page" not in pages._pages


def test_failed_page_probe_does_not_leave_cached_success_or_stuck_future():
    pages = SessionPageCache()
    pages.read("key", lambda old: {"revision": "one", "status": "AVAILABLE"})

    def unavailable(old):
        raise CatalogError("DOMAIN_DISCONNECTED")

    with pytest.raises(CatalogError, match="DOMAIN_DISCONNECTED"):
        pages.read("key", unavailable)
    assert not pages._pending and not pages._pages
    assert pages.read("key", lambda old: {"status": "EMPTY"})["status"] == "EMPTY"


class Source:
    def __init__(self):
        self.item = {"source_id": "local", "kind": "NATIVE", "label": "Local",
                     "state": "OBSERVED", "enabled": True, "freshness": "FRESH",
                     "revision": 0, "protection": {"epoch": "first"}}

    def sources(self):
        return {"items": [dict(self.item)]}


def make_store(root):
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    target = sessions / "rollout-one.jsonl"
    target.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": "s", "cwd": "/project-one", "timestamp": "2026-09-30T00:00:00Z"}}) + "\n")
    return target


def test_real_store_cache_checks_changes_identity_and_disabled_source(tmp_path):
    root = tmp_path / "codex"
    target = make_store(root)
    with closing(StateDB(tmp_path / "state.db")) as db:
        db.connect()
        source, domains = Source(), DomainRegistry(tmp_path / "state")
        catalog = DiscoveryCatalog(db, domains, source, SessionPageCache())
        store_id = catalog.register_store("local", "CODEX", str(root))["store_id"]
        first = catalog.read_sessions("local", store_id)
        before = db._conn.total_changes
        second = catalog.read_sessions("local", store_id)
        assert second["sessions"] == first["sessions"]
        assert db._conn.total_changes == before
        assert second["observed_at"] >= first["observed_at"]
        target.write_text(target.read_text().replace("/project-one", "/project-two"))
        changed = catalog.read_sessions("local", store_id)
        assert changed["sessions"][0]["cwd"] == "/project-two"
        assert changed["sessions"][0]["binding_id"] == first["sessions"][0]["binding_id"]
        source.item["enabled"] = False
        with pytest.raises(CatalogError, match="DISCOVERY_SOURCE_NOT_CURRENT"):
            catalog.read_sessions("local", store_id)
        source.item["enabled"] = True
        root.rename(tmp_path / "old-root")
        make_store(root)
        with pytest.raises(CatalogError, match="SESSION_STORE_IDENTITY_CHANGED"):
            catalog.read_sessions("local", store_id)


def test_source_changed_during_metadata_probe_is_not_published(tmp_path):
    source = Source()

    class Domains:
        def discovery_query(self, source_id, query):
            source.item["revision"] += 1
            return {"store_identity": "identity", "status": "AVAILABLE", "sessions": []}

    with closing(StateDB(tmp_path / "state.db")) as db:
        db.connect()
        catalog = DiscoveryCatalog(db, Domains(), source, SessionPageCache())
        store = catalog._save_store("local", "CODEX", "/store", {"identity": "identity", "status": "AVAILABLE"})
        with pytest.raises(CatalogError, match="DISCOVERY_SOURCE_CHANGED"):
            catalog.read_sessions("local", store)
        assert db._conn.execute("SELECT COUNT(*) FROM protection_sessions").fetchone()[0] == 0


def test_missing_store_observation_retains_binding_and_reports_unavailability(tmp_path):
    root = tmp_path / "codex"
    make_store(root)
    with closing(StateDB(tmp_path / "state.db")) as db:
        db.connect()
        catalog = DiscoveryCatalog(db, DomainRegistry(tmp_path / "state"), Source(), SessionPageCache())
        store = catalog.register_store("local", "CODEX", str(root))["store_id"]
        binding = catalog.read_sessions("local", store)["sessions"][0]["binding_id"]
        hidden = tmp_path / "temporarily-unavailable"
        root.rename(hidden)
        same = catalog._save_store("local", "CODEX", str(root), {
            "identity": None, "status": "UNKNOWN", "reason_code": "SESSION_STORE_NOT_PRESENT"})
        assert same == store
        missing = catalog.read_sessions("local", store)
        assert missing["status"] == "UNKNOWN" and missing["sessions"] == []
        assert missing["reason_code"] == "SESSION_STORE_NOT_PRESENT"
        assert catalog.stores("local")["items"][0]["id"] == store
        hidden.rename(root)
        assert catalog.read_sessions("local", store)["sessions"][0]["binding_id"] == binding


def test_source_view_does_not_wait_for_collection_lock():
    managed = ManagedDiscoveryService()
    with ThreadPoolExecutor(max_workers=1) as pool, managed.lock:
        # If the view shares the collection lock this cannot finish until exit.
        assert pool.submit(managed.sources).result(timeout=2)["items"] == []


def test_local_session_read_does_not_wait_for_unrelated_domain_transport(tmp_path):
    root = tmp_path / "codex"
    make_store(root)
    domains = DomainRegistry(tmp_path / "state")
    with ThreadPoolExecutor(max_workers=1) as pool, domains._lock:
        result = pool.submit(domains.discovery_query, "local", {
            "action": "read_session_store", "provider": "CODEX", "path": str(root)}).result(timeout=2)
        assert result["sessions"][0]["session_id"] == "s"


def test_cached_domain_view_is_nonblocking_without_revalidating_write_authority(tmp_path):
    domains = DomainRegistry(tmp_path)
    domains._sources = {"local": {"source_id": "local", "running": True}}
    with ThreadPoolExecutor(max_workers=1) as pool, domains._lock:
        result = pool.submit(domains.sources, refresh=False).result(timeout=2)
    assert result == [{"source_id": "local", "running": True}]
    result[0]["running"] = False
    assert domains._sources["local"]["running"] is True
