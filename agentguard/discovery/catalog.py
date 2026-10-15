"""Program and session-store registrations, separate from recovery authority."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime

from .sessions import _safe_cwd, _safe_title, _safe_updated_at
from .source_identity import reconcile_source_alias, resolve_source


def _now():
    return datetime.now(UTC).isoformat()


def _encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _id(prefix, *parts):
    return prefix + "-" + hashlib.sha256(_encoded(parts).encode()).hexdigest()[:32]


def _controls():
    return {"create": False, "continue": False, "send": False, "stream": False,
            "cancel": False, "reason_code": "CONTROL_ADAPTER_NOT_IMPLEMENTED", "ownership": "EXTERNAL"}


class CatalogError(ValueError):
    def __init__(self, reason_code, status_code=409):
        super().__init__(reason_code)
        self.reason_code, self.status_code = reason_code, status_code


class SessionPageCache:
    """Bounded metadata pages; every read still validates the source and revision.

    No TTL grants authority or hides a replaced directory. Concurrent readers of
    the same page share one probe; only sanitized API metadata is retained.
    """

    def __init__(self, max_pages=32):
        self.max_pages = max_pages
        self._pages = OrderedDict()
        self._pending = {}
        self._lock = threading.Lock()

    def read(self, key, load):
        with self._lock:
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = self._pending[key] = Future()
            previous = self._pages.get(key)
        if not owner:
            return deepcopy(pending.result())
        try:
            result = load(deepcopy(previous))
            with self._lock:
                if result.get("revision") and result.get("status") in {"AVAILABLE", "EMPTY", "DEGRADED"}:
                    self._pages[key] = deepcopy(result)
                    self._pages.move_to_end(key)
                    while len(self._pages) > self.max_pages:
                        self._pages.popitem(last=False)
                else:
                    self._pages.pop(key, None)
                pending.set_result(result)
            return result
        except BaseException as exc:
            with self._lock:
                self._pages.pop(key, None)
                pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)


def record_sources(db, result):
    with db.transaction() as conn:
        for item in result.get("items", []):
            safe = {k: item[k] for k in ("source_id", "kind", "label", "state", "reason_code", "running",
                "connected", "epoch", "capabilities", "observed_at", "protection", "short_id") if k in item}
            conn.execute("INSERT INTO protection_sources VALUES (?,?,?) ON CONFLICT(id) DO UPDATE "
                         "SET metadata_json=excluded.metadata_json,observed_at=excluded.observed_at",
                         (item["source_id"], _encoded(safe), _now()))
    return include_historical_sources(db, result)


def include_historical_sources(db, result):
    """Merge retained identities without making polling readers acquire a write lock."""
    observed = {item["source_id"] for item in result.get("items", [])}
    historical = []
    for row in db._conn.execute("SELECT id,metadata_json FROM protection_sources"):
        item = json.loads(row[1])
        if row[0] in observed or item.get("canonical_source_id"):
            continue
        item.update(state="NOT_CURRENT", running=None, connected=False, freshness="STALE",
                    reason_code="PROTECTION_SOURCE_NOT_CURRENT", configurable=False, agent_count=0,
                    reason="Historical observation", capabilities={"observe": False, "sessions": False, "start": False, "cancel": False})
        if "protection" in item:
            item["protection"] = {**item["protection"], "running": None, "connected": False,
                                  "capabilities": {"scan": False, "apply": False}}
        historical.append(item)
    return {**result, "items": [*result.get("items", []), *historical]}


def record_sessions(db, source_id, result):
    if result.get("status") not in {"AVAILABLE", "EMPTY", "PARTIAL", "DEGRADED"}:
        return result
    source_id = resolve_source(db._conn, source_id)
    store_id = result.get("store_id") or "legacy:" + source_id
    output = []
    with (nullcontext(db._conn) if db._transaction_active else db.transaction()) as conn:
        for record in result.get("sessions", []):
            provider = record.get("provider", result.get("provider", "UNKNOWN"))
            prior = conn.execute("SELECT id FROM protection_sessions WHERE source_id=? AND store_id=? AND provider=? AND provider_session_id=?",
                                 (source_id, store_id, provider, record["session_id"])).fetchone()
            session_id = prior[0] if prior else _id("session", source_id, store_id, provider, record["session_id"])
            metadata = {k: v for k, v in record.items() if k != "title"}
            metadata["store_id"] = store_id
            conn.execute("INSERT INTO protection_sessions(id,source_id,provider,provider_session_id,metadata_json,observed_at,store_id) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json,observed_at=excluded.observed_at",
                (session_id, source_id, provider, record["session_id"], _encoded(metadata), _now(), store_id))
            output.append({**record, "binding_id": session_id, "store_id": store_id,
                           "association": None, "scope": "PROVIDER_STORE"})
    return {**result, "source_id": source_id, "store_id": store_id, "sessions": output}


class DiscoveryCatalog:
    def __init__(self, database, domains, discovery, session_cache=None):
        self.db, self.domains, self.discovery = database, domains, discovery
        self.conn = database._conn
        self.conn.row_factory = sqlite3.Row
        self.session_cache = session_cache

    def reconcile(self):
        sources = self.domains.sources()
        with (nullcontext(self.conn) if self.db._transaction_active else self.db.transaction()) as conn:
            for source in sources:
                if source.get("kind") == "CONTAINER" and source.get("container_id"):
                    reconcile_source_alias(conn, source["container_id"])
        return sources

    def resolve(self, source_id):
        return resolve_source(self.conn, source_id)

    def _source(self, source_id):
        source_id = self.resolve(source_id)
        item = next((s for s in self.discovery.sources()["items"] if s["source_id"] == source_id), None)
        if item is None:
            raise CatalogError("DISCOVERY_SOURCE_NOT_FOUND", 404)
        if not item.get("enabled", True) or item.get("freshness") != "FRESH" or item.get("state") not in {"OBSERVED", "PARTIAL", "EMPTY"}:
            raise CatalogError("DISCOVERY_SOURCE_NOT_CURRENT")
        return item

    def _probe(self, source_id, action, **kwargs):
        self._source(source_id)
        return self.domains.discovery_query(self.resolve(source_id), {"action": action, **kwargs})

    def observe(self, report):
        """Store local-only display facts; no executable paths enter the ledger."""
        observations = getattr(report, "application_observations", ())
        providers = {agent.agent_id: agent.agent_type
                     for agent in getattr(getattr(report, "snapshot", None), "agents", ())}
        grouped = {}
        for observation in observations:
            program = observation.get("program", {})
            if not program.get("path"):
                continue
            key = (observation.get("source_id", "local"), program["path"])
            grouped.setdefault(key, {"program": program, "instances": []})["instances"].append(
                {"provider": providers.get(observation.get("agent_id"), "UNKNOWN"), **{
                    k: observation.get(k) for k in ("agent_id", "pid", "parent_pid", "create_time", "start_ticks", "cwd", "observed_at")}})
        # A refresh replaces running facts, not durable registrations.
        with self.db.transaction() as conn:
            conn.execute("UPDATE discovery_applications SET metadata_json=json_set(metadata_json,'$.instances',json('[]'))")
            conn.execute("DELETE FROM discovery_applications WHERE registered=0 AND json_extract(metadata_json,'$.classification')='UNCLASSIFIED' AND julianday(observed_at) < julianday('now','-1 day')")
            for (source_id, _), value in grouped.items():
                self._save_application(source_id, value["program"], instances=value["instances"])

    def refresh_inventory(self):
        sources = self.discovery.sources()["items"]
        coverage = []
        for source in sources:
            if source["kind"] not in {"NATIVE", "CONTAINER", "WSL"} or source.get("state") not in {"OBSERVED", "PARTIAL", "EMPTY"} or not source.get("enabled", True):
                continue
            source_id = source["source_id"]
            paths = [row[0] for row in self.conn.execute("SELECT path FROM discovery_applications WHERE source_id=? AND (registered=1 OR json_extract(metadata_json,'$.classification')='RECOGNIZED') LIMIT 256", (source_id,))]
            try:
                from .session_metadata import PROVIDERS
                from .signatures import COMMAND_NAMES
                result = self._probe(source_id, "application_inventory", paths=paths, names=COMMAND_NAMES)
                process_hints = []
                for row in self.conn.execute("SELECT metadata_json FROM discovery_applications WHERE source_id=? AND json_array_length(metadata_json,'$.instances')>0", (source_id,)):
                    metadata = json.loads(row[0])
                    for instance in metadata.get("instances", []):
                        provider = instance.get("provider", metadata.get("provider"))
                        if provider in PROVIDERS:
                            process_hints.append({"provider": provider, **{
                                k: instance.get(k) for k in ("pid", "create_time", "start_ticks")}})
                stores = self._probe(source_id, "session_stores", process_hints=process_hints)
                with self.db.transaction():
                    for program in result.get("items", []):
                        self._save_application(source_id, program)
                    for candidate in stores.get("items", []):
                        self._save_store(source_id, candidate["provider"], candidate["path"], candidate)
                coverage.append({"source_id": source_id, **result.get("coverage", {}),
                                 "session_locations": stores.get("coverage", {})})
            except (OSError, RuntimeError, ValueError) as exc:
                coverage.append({"source_id": source_id, "status": "UNKNOWN", "reason_code": getattr(exc, "reason_code", "DISCOVERY_INVENTORY_UNAVAILABLE")})
        return coverage

    def _save_application(self, source_id, program, *, instances=None, registered=False, label=None):
        from .signatures import classify_metadata
        program = dict(program)
        path = program.get("entry_path") or program["path"]
        program["observed_entry"] = program["path"]
        program["path"] = path
        key_path = os.path.normcase(os.path.normpath(path)) if source_id == "local" else path
        identity = _id("application", source_id, key_path)
        prior = self.conn.execute("SELECT metadata_json,registered FROM discovery_applications WHERE id=?", (identity,)).fetchone()
        old = json.loads(prior[0]) if prior else {}
        # Process presence cannot refresh or erase an independent file-entry check.
        if instances is not None:
            for key in ("status", "reason_code", "identity", "version"):
                program[key] = old.get(key) if old.get("installation_observed_at") else (
                    "UNVERIFIED" if key == "status" else program.get(key))
        else:
            program["installation_observed_at"] = _now()
        classification_input = program
        interpreter = path.replace("\\", "/").rsplit("/", 1)[-1].lower() in {"node", "node.exe", "python", "python.exe", "python3"}
        if interpreter and not program.get("entry_path"):
            classification_input = {**program, "name": None, "executable_basename": path.replace("\\", "/").rsplit("/", 1)[-1]}
        signature = classify_metadata(classification_input, source_id)
        metadata = {**old, **program, "provider": signature[0] if signature else "UNKNOWN",
                    "instances": instances if instances is not None else old.get("instances", []),
                    "control_capabilities": _controls(),
                    "classification": "RECOGNIZED" if signature else "USER_REGISTERED" if registered or prior and prior[1] else "UNCLASSIFIED"}
        if label:
            metadata["label"] = _safe_title(label) or "Registered program"
        with (nullcontext(self.conn) if self.db._transaction_active else self.db.transaction()) as conn:
            conn.execute("INSERT INTO discovery_applications(id,source_id,path,metadata_json,registered,observed_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json,registered=MAX(registered,excluded.registered),observed_at=excluded.observed_at",
                (identity, source_id, path, _encoded(metadata), int(registered), _now()))
        return identity

    def _save_store(self, source_id, provider, path, metadata, registered=False):
        prior = self.conn.execute("SELECT id,metadata_json FROM discovery_session_stores WHERE source_id=? AND provider=? AND path=? AND json_extract(metadata_json,'$.status') <> 'RETIRED' ORDER BY observed_at DESC LIMIT 1",
                                  (source_id, provider, path)).fetchone()
        old = json.loads(prior["metadata_json"]) if prior else {}
        previous_identity = old.get("identity")
        # Failed observation is not evidence that a different directory exists.
        # Retain the binding and its history while exposing the current failure.
        if previous_identity and not metadata.get("identity") and not registered:
            value = {**old, **metadata, "identity": previous_identity}
            with (nullcontext(self.conn) if self.db._transaction_active else self.db.transaction()) as conn:
                conn.execute("UPDATE discovery_session_stores SET metadata_json=?,observed_at=? WHERE id=?",
                             (_encoded(value), _now(), prior["id"]))
            return prior["id"]
        changed = previous_identity and metadata.get("identity") and previous_identity != metadata["identity"]
        if changed and not registered:
            value = {**old, "status": "UNKNOWN", "reason_code": "SESSION_STORE_IDENTITY_CHANGED"}
            with (nullcontext(self.conn) if self.db._transaction_active else self.db.transaction()) as conn:
                conn.execute("UPDATE discovery_session_stores SET metadata_json=? WHERE id=?", (_encoded(value), prior["id"]))
            return prior["id"]
        identity = _id("store", source_id, provider, path, metadata.get("identity"))
        value = {**old, **metadata, "control_capabilities": _controls()}
        with (nullcontext(self.conn) if self.db._transaction_active else self.db.transaction()) as conn:
            if prior and prior["id"] != identity:
                conn.execute("UPDATE discovery_session_stores SET metadata_json=json_set(metadata_json,'$.status','RETIRED') WHERE id=?", (prior["id"],))
            conn.execute("INSERT INTO discovery_session_stores(id,source_id,provider,path,metadata_json,registered,observed_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json,registered=MAX(registered,excluded.registered),observed_at=excluded.observed_at",
                (identity, source_id, provider, path, _encoded(value), int(registered), _now()))
        return identity

    def applications(self, source_id=None, limit=50, offset=0, include_unclassified=False):
        result = self._list("discovery_applications", source_id, limit, offset, include_unclassified=include_unclassified)
        clause = " AND source_id=?" if source_id else ""
        parameters = (self.resolve(source_id),) if source_id else ()
        result["unclassified_count"] = self.conn.execute("SELECT COUNT(*) FROM discovery_applications WHERE registered=0 AND json_extract(metadata_json,'$.classification')='UNCLASSIFIED'" + clause, parameters).fetchone()[0]
        return result

    def stores(self, source_id, limit=50, offset=0):
        return self._list("discovery_session_stores", source_id, limit, offset)

    def _list(self, table, source_id, limit, offset, *, include_unclassified=True):
        params = (self.resolve(source_id),) if source_id else ()
        where = " WHERE source_id=?" if source_id else " WHERE 1=1"
        if table == "discovery_session_stores":
            where += " AND COALESCE(json_extract(metadata_json,'$.status'),'UNKNOWN') <> 'RETIRED'"
        if table == "discovery_applications" and not include_unclassified:
            where += " AND (registered=1 OR json_extract(metadata_json,'$.classification')='RECOGNIZED')"
        rows = self.conn.execute(f"SELECT * FROM {table}{where} ORDER BY id LIMIT ? OFFSET ?", (*params, limit + 1, offset)).fetchall()
        items = []
        for row in rows[:limit]:
            item = dict(row)
            metadata = json.loads(item.pop("metadata_json"))
            item.update(metadata)
            item["registered"] = bool(item["registered"])
            age = (datetime.now(UTC) - datetime.fromisoformat(item["observed_at"])).total_seconds()
            item["freshness"] = "FRESH" if -5 <= age <= 120 else "STALE"
            if table == "discovery_applications":
                installation_at = item.get("installation_observed_at")
                installation_age = (datetime.now(UTC) - datetime.fromisoformat(installation_at)).total_seconds() if installation_at else None
                item["installation_freshness"] = "UNKNOWN" if installation_age is None else "FRESH" if -5 <= installation_age <= 120 else "STALE"
            item["running"] = bool(item.get("instances")) and item["freshness"] == "FRESH"
            items.append(item)
        return {"items": items, "has_more": len(rows) > limit, "next_offset": offset + limit if len(rows) > limit else None}

    def register_application(self, source_id, path=None, candidate_id=None, label=None):
        source_id = self.resolve(source_id)
        if candidate_id:
            candidate = self.conn.execute("SELECT path FROM discovery_applications WHERE id=? AND source_id=?", (candidate_id, source_id)).fetchone()
            if candidate is None:
                raise CatalogError("DISCOVERY_APPLICATION_NOT_FOUND", 404)
            path = candidate[0]
        if _safe_cwd(path) is None:
            raise CatalogError("DISCOVERY_PATH_INVALID", 422)
        result = self._probe(source_id, "application_entry", path=path)
        if result.get("status") != "AVAILABLE":
            raise CatalogError(result.get("reason_code", "DISCOVERY_APPLICATION_UNAVAILABLE"))
        identity = self._save_application(source_id, result, registered=True, label=label)
        return {"application_id": identity, "source_id": source_id, "control_capabilities": _controls()}

    def register_store(self, source_id, provider, path):
        source_id = self.resolve(source_id)
        if _safe_cwd(path) is None:
            raise CatalogError("DISCOVERY_PATH_INVALID", 422)
        result = self._probe(source_id, "inspect_session_store", provider=provider, path=path)
        if not result.get("identity") or result.get("status") in {"UNSUPPORTED", "ERROR"}:
            raise CatalogError(result.get("reason_code", "SESSION_STORE_UNAVAILABLE"))
        identity = self._save_store(source_id, provider, result["path"], result, registered=True)
        return {"store_id": identity, "source_id": source_id, "status": result["status"], "reason_code": result["reason_code"]}

    def read_sessions(self, source_id, store_id=None, limit=50, offset=0):
        source_id = self.resolve(source_id)
        source = self._source(source_id)
        if store_id is None:
            candidates = self.conn.execute("SELECT id,metadata_json FROM discovery_session_stores WHERE source_id=? AND COALESCE(json_extract(metadata_json,'$.status'),'UNKNOWN') <> 'RETIRED' ORDER BY id", (source_id,)).fetchall()
            usable = [r[0] for r in candidates if json.loads(r[1]).get("identity") and
                      json.loads(r[1]).get("status") in {"AVAILABLE", "DEGRADED"}]
            if len(usable) != 1:
                return {"status": "UNKNOWN", "reason_code": "SESSION_STORE_SELECTION_REQUIRED" if len(usable) > 1 else "SESSION_STORE_NOT_PRESENT",
                        "source_id": source_id, "sessions": [], "stores": self.stores(source_id)["items"]}
            store_id = usable[0]
        row = self.conn.execute("SELECT * FROM discovery_session_stores WHERE id=? AND source_id=?", (store_id, source_id)).fetchone()
        if row is None:
            raise CatalogError("SESSION_STORE_NOT_FOUND", 404)
        metadata = json.loads(row["metadata_json"])
        if metadata.get("status") == "RETIRED":
            raise CatalogError("SESSION_STORE_RETIRED")
        key = (source_id, store_id, source.get("revision"),
               source.get("protection", {}).get("epoch"), limit, offset)
        load = lambda previous: self._read_session_page(source, row, metadata, limit, offset, previous)
        return self.session_cache.read(key, load) if self.session_cache else load(None)

    def _read_session_page(self, source, row, metadata, limit, offset, previous):
        source_id, store_id = row["source_id"], row["id"]
        query = {"provider": row["provider"], "path": row["path"], "limit": limit, "offset": offset}
        if previous and previous.get("revision"):
            query["known_revision"] = previous["revision"]
        # read_store inspects identity itself. A separate inspect would repeat
        # Docker/WSL identity checks and transport setup without closing a race.
        result = self._probe(source_id, "read_session_store", **query)
        current = self._source(source_id)
        if (current.get("revision") != source.get("revision") or
                current.get("protection", {}).get("epoch") != source.get("protection", {}).get("epoch")):
            raise CatalogError("DISCOVERY_SOURCE_CHANGED")
        if (metadata.get("identity") and result.get("store_identity") is not None
                and result["store_identity"] != metadata["identity"]):
            raise CatalogError("SESSION_STORE_IDENTITY_CHANGED")
        if len(_encoded(result).encode()) > 256 * 1024:
            raise CatalogError("SESSION_OUTPUT_LIMITED")
        if result.get("not_modified"):
            if not previous or result.get("revision") != previous.get("revision"):
                raise CatalogError("SESSION_REVISION_INVALID")
            return {**previous, "observed_at": _now()}
        for record in result.get("sessions", []):
            record["title"] = _safe_title(record.get("title"))
            record["cwd"] = _safe_cwd(record.get("cwd"))
            record["updated_at"] = _safe_updated_at(record.get("updated_at"))
            record["control_capabilities"] = _controls()
        result.update(source_id=source_id, store_id=store_id, observed_at=_now())
        return record_sessions(self.db, source_id, result)

    def enrich_agents(self, result):
        identities = {}
        sources = {row[0]: json.loads(row[1]) for row in self.conn.execute("SELECT id,metadata_json FROM protection_sources")}
        for row in self.conn.execute("SELECT id,source_id,path,metadata_json FROM discovery_applications"):
            metadata = json.loads(row["metadata_json"])
            for instance in metadata.get("instances", []):
                if instance.get("agent_id"):
                    identities[instance["agent_id"]] = {**instance, "application_id": row["id"] if metadata.get("classification") != "UNCLASSIFIED" else None,
                        "program_path": row["path"], "source_id": row["source_id"],
                        "source_label": sources.get(row["source_id"], {}).get("label", row["source_id"]),
                        "source_short_id": sources.get(row["source_id"], {}).get("short_id"),
                        "control_capabilities": _controls()}
        return {**result, "items": [{**item, **identities.get(item.get("agent_id"), {})} for item in result.get("items", [])]}
