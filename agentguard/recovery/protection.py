"""Continuous workspace versions in the canonical StateDB, Ledger and BlobStore.

Only adapters hold filesystem authority. API callers select server-owned IDs.
Every service instance owns its SQLite connection on its current thread.
"""

from __future__ import annotations

import base64
import difflib
import gzip
import hashlib
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import uuid4

from agentguard.core.sanitizer import contains_sensitive_data
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.blob import BlobStore
from agentguard.storage.db import StateDB

from . import protection_history as history


def now():
    return datetime.now(UTC).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def identifier(prefix):
    return f"{prefix}-{uuid4()}"


class ProtectionError(ValueError):
    def __init__(self, reason_code, status_code=409, *, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code
        self.details = details or {}


def entry_state(entry):
    """Compare persisted facts, not collection times or transported bytes."""
    if entry is None:
        return None
    return {key: entry.get(key) for key in (
        "object_kind", "category", "content_digest", "permission_proof", "size"
    )}


def same_entry(left, right):
    if left is None or right is None:
        return left is right
    # Windows automatically inherited DACL marker is not a semantic difference.
    from .workspace_permissions import PermissionProof

    a, b = entry_state(left), entry_state(right)
    pa, pb = a.pop("permission_proof"), b.pop("permission_proof")
    if a != b:
        return False
    if pa is None or pb is None:
        return pa == pb
    try:
        return PermissionProof(pa["kind"], pa["values"]).matches(
            PermissionProof(pb["kind"], pb["values"]))
    except (ValueError, KeyError, TypeError):
        return False


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_LOCK = threading.Lock()
_EXECUTION_LOCAL = threading.local()


@contextmanager
def recovery_lease(db_path, *, name='recovery', busy_code='PROTECTION_RECOVERY_BUSY'):
    """Nonblocking cross-process exclusion, reentrant only on the owning thread."""
    path = str(Path(db_path).resolve()) + '.' + name + '.lock'
    held = getattr(_EXECUTION_LOCAL, "held", set())
    if path in held:
        yield
        return
    with open(path, "a+b") as lease:
        lease.seek(0, 2)
        if lease.tell() == 0:
            lease.write(b"\0")
            lease.flush()
        lease.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ProtectionError(busy_code) from exc
        _EXECUTION_LOCAL.held = held | {path}
        try:
            yield
        finally:
            _EXECUTION_LOCAL.held = held
            if os.name == "nt":
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)


def protection_lock(db_path):
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(str(Path(db_path).resolve()), threading.RLock())


class ProtectionService:
    def __init__(self, db_path, state_dir, domains, *, fault=None):
        self.db_path = Path(db_path)
        self.db = StateDB(Path(db_path))
        self.db.connect()
        self.conn = self.db._conn
        self.conn.execute("PRAGMA synchronous=FULL")
        self.blobs = BlobStore(Path(state_dir) / "blobs")
        self.domains = domains
        self.lock = protection_lock(db_path)
        self.fault = fault or (lambda point, operation_id, step_id=None: None)

    def close(self):
        self.db.close()

    def _one(self, sql, params=(), *, missing="PROTECTION_NOT_FOUND"):
        cursor = self.conn.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            raise ProtectionError(missing, 404)
        return dict(zip((column[0] for column in cursor.description), row))

    def _rows(self, sql, params=()):
        cursor = self.conn.execute(sql, params)
        keys = [column[0] for column in cursor.description]
        return [dict(zip(keys, row)) for row in cursor]

    def _event(self, conn, scope_id, event_type, result, **facts):
        if verify_ledger(conn):
            raise ProtectionError("PROTECTION_LEDGER_INVALID")
        timestamp = datetime.now(UTC)
        event_id = identifier("protection-event")
        EvidenceLedger().append(conn, EvidenceEvent(
            schema_version=1, event_id=event_id, recorded_at=timestamp,
            observed_at=timestamp,
            event_family=EventFamily.CHANGE if event_type == EventType.OBSERVED_CHANGE else EventFamily.RECOVERY,
            event_type=event_type, source="core-protection", result=result,
            execution_domain_id=None, supervision_session_id=None,
            transaction_id=facts.get("operation_id"), checkpoint_id=None,
            subject_ref=scope_id, evidence_refs=(),
            payload_safe={"scope_id": scope_id, **facts},
        ))
        return event_id

    def _request(self, request_id, payload, fn):
        request_digest = digest(payload)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT digest,response_json FROM protection_requests WHERE id=?", (request_id,)).fetchone()
            if row:
                if row[0] != request_digest:
                    raise ProtectionError("PROTECTION_REQUEST_REUSED")
                return json.loads(row[1])
            result = fn(conn)
            conn.execute("INSERT INTO protection_requests(id,digest,response_json,scope_id) VALUES (?,?,?,?)",
                (request_id, request_digest, encoded(result), result.get("scope_id")))
            self._pause_if_over_quota(conn, result.get("scope_id"))
            return result

    def scope(self, scope_id):
        return self._one("SELECT * FROM protection_scopes WHERE id=?", (scope_id,))

    def scopes(self):
        result = []
        for row in self._rows("SELECT s.*,v.summary_json AS latest_summary FROM protection_scopes s LEFT JOIN protection_versions v ON v.id=s.latest_id ORDER BY s.created_at,s.id"):
            item = {key: value for key, value in row.items() if key not in {
                "binding_json", "pending_digest", "pending_since", "root_key", "identity_digest", "metadata_bytes", "last_collected_at", "latest_summary"}}
            binding = json.loads(row["binding_json"])
            item["root"] = binding["root"]
            item["kind"] = binding["kind"]
            item["coverage"] = json.loads(row["latest_summary"])["coverage"] if row["latest_summary"] else None
            item["space"] = self.space(row["id"])
            item["storage_bytes"] = item["space"]["logical_retained_bytes"]
            item["evidence_refs"] = [r[0] for r in self.conn.execute("SELECT event_id FROM evidence_ledger_events WHERE subject_ref=? ORDER BY sequence DESC LIMIT 5", (row["id"],))]
            item["limits"] = {"max_file_bytes": 4 * 1024 * 1024, "max_scan_bytes": 128 * 1024 * 1024,
                "max_entries": 10000, "atomic_directory_snapshot": False,
                "excluded": ["sensitive content", "links", "special files", "cross-mount boundaries", "unsupported metadata"]}
            item["seconds_since_content_observation"] = max(0, (datetime.now(UTC) - datetime.fromisoformat(row["observed_at"])).total_seconds()) if row["observed_at"] else None
            result.append(item)
        return {"items": result, "shared_storage": self.shared_storage()}

    def candidates(self, limit=50, offset=0):
        return history.candidates(self, limit, offset)

    def register(self, source_id, path, request_id, *, replaces_scope_id=None):
        from agentguard.discovery.source_identity import resolve_source
        source_id = resolve_source(self.conn, source_id)
        with self.lock:
            # Retried enrollment must not depend on the target still being online.
            prior = self.conn.execute("SELECT digest,response_json FROM protection_requests WHERE id=?", (request_id,)).fetchone()
            payload = ["register", source_id, path, replaces_scope_id]
            if prior:
                if prior[0] != digest(payload):
                    raise ProtectionError("PROTECTION_REQUEST_REUSED")
                return json.loads(prior[1])
            binding = self.domains.register(source_id, path)
            if binding["kind"] == "NATIVE":
                from .workspace_policy import _EXCLUDED_DIRECTORIES

                project, storage = Path(binding["root"]).resolve(), self.db_path.parent.resolve()
                if project.is_relative_to(storage):
                    raise ProtectionError("PROTECTION_CORE_STORAGE_OVERLAP")
                if storage.is_relative_to(project) and not any(
                    part.casefold() in _EXCLUDED_DIRECTORIES for part in storage.relative_to(project).parts
                ):
                    raise ProtectionError("PROTECTION_CORE_STORAGE_OVERLAP")
            root_key = binding["root"].replace("\\", "/").rstrip("/")
            if binding["kind"] == "NATIVE" and __import__("os").name == "nt":
                root_key = root_key.casefold()
            identity_digest = digest(binding["identity"])

            def apply(conn):
                if replaces_scope_id:
                    old = self.scope(replaces_scope_id)
                    if old["source_id"] != source_id or old["root_key"] != root_key or old["identity_digest"] == identity_digest:
                        raise ProtectionError("PROTECTION_REPLACEMENT_INVALID")
                    if conn.execute("SELECT 1 FROM protection_operations WHERE scope_id=? AND state IN ('QUEUED','RUNNING','COMPENSATING','NEEDS_ATTENTION')", (replaces_scope_id,)).fetchone():
                        raise ProtectionError("PROTECTION_RECOVERY_BUSY")
                    conn.execute("UPDATE protection_scopes SET enabled=0,status='RETIRED',reason_code='PROTECTION_ROOT_REPLACED',revision=revision+1 WHERE id=?", (replaces_scope_id,))
                    conn.execute("UPDATE protection_previews SET status='EXPIRED' WHERE scope_id=? AND status='READY'", (replaces_scope_id,))
                reused = None
                for existing in self._rows("SELECT * FROM protection_scopes WHERE source_id=? AND status<>'RETIRED'", (source_id,)):
                    other = existing["root_key"]
                    if existing["identity_digest"] == identity_digest:
                        reused = existing
                        continue
                    if root_key == other:
                        raise ProtectionError("PROTECTION_ROOT_REPLACED", details={"existing_scope_id": existing["id"], "existing_root": other})
                    if root_key.startswith(other + "/") or other.startswith(root_key + "/"):
                        raise ProtectionError("PROTECTION_SCOPE_OVERLAP", details={"existing_scope_id": existing["id"], "existing_root": other})
                if reused:
                    if reused["root_key"] != root_key:
                        if conn.execute("SELECT 1 FROM protection_operations WHERE scope_id=? AND state IN ('QUEUED','RUNNING','COMPENSATING','NEEDS_ATTENTION')", (reused["id"],)).fetchone():
                            raise ProtectionError("PROTECTION_RECOVERY_BUSY")
                        conn.execute("UPDATE protection_scopes SET root_key=?,binding_json=?,revision=revision+1 WHERE id=?", (root_key, encoded(binding), reused["id"]))
                        conn.execute("UPDATE protection_previews SET status='EXPIRED' WHERE scope_id=? AND status='READY'", (reused["id"],))
                        reused["revision"] += 1
                    return {"scope_id": reused["id"], "revision": reused["revision"], "reused": True}
                scope_id = identifier("scope")
                conn.execute("""INSERT INTO protection_scopes
                    (id,source_id,root_key,identity_digest,binding_json,status,reason_code,created_at)
                    VALUES (?,?,?,?,?,'INITIALIZING','PROTECTION_BASELINE_PENDING',?)""",
                    (scope_id, source_id, root_key, identity_digest, encoded(binding), now()))
                self._event(conn, scope_id, EventType.WORKSPACE_PROTECTION_BOUND, "REGISTERED")
                return {"scope_id": scope_id, "revision": 1, "reused": False}

            result = self._request(request_id, payload, apply)
            # Enrollment remains successful even if the initial baseline is incomplete.
            self.capture(result["scope_id"], force=True)
            return result

    def configure(self, scope_id, request_id, expected_revision, *, enabled, retention_days, quota_bytes):
        with self.lock:
            def apply(conn):
                scope = self.scope(scope_id)
                if scope["status"] == "RETIRED":
                    raise ProtectionError("PROTECTION_SCOPE_RETIRED")
                if scope["revision"] != expected_revision:
                    raise ProtectionError("PROTECTION_REVISION_CONFLICT")
                conn.execute("""UPDATE protection_scopes SET enabled=?,retention_days=?,quota_bytes=?,
                    revision=revision+1,status=?,reason_code=? WHERE id=?""",
                    (int(enabled), retention_days, quota_bytes, "PENDING" if enabled else "PAUSED",
                     "PROTECTION_SCAN_PENDING" if enabled else "PROTECTION_USER_PAUSED", scope_id))
                return {"scope_id": scope_id, "revision": expected_revision + 1}
            return self._request(request_id, ["configure", scope_id, expected_revision, enabled, retention_days, quota_bytes], apply)

    def _scan(self, scope):
        scan = self.domains.scan(json.loads(scope["binding_json"]))
        if not scan.get("complete") or any(e["category"] == "unreachable" for e in scan["entries"]):
            raise ProtectionError(scan.get("reason_code") or "PROTECTION_SCAN_INCOMPLETE")
        manifest = {}
        for raw in scan["entries"]:
            entry = dict(raw)
            if entry.get("category") == "restorable":
                data = base64.b64decode(entry.get("content_b64", ""), validate=True)
                if len(data) > 4 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != entry.get("content_digest"):
                    raise ProtectionError("PROTECTION_CONTENT_INVALID")
                if contains_sensitive_data(data.decode("utf-8", errors="ignore")):
                    entry.update(category="audit_only", reason_code="WORKSPACE_SENSITIVE_CONTENT_AUDIT_ONLY", permission_proof=None)
                    entry.pop("content_b64", None)
            relative = entry["relative_path"]
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
                raise ProtectionError("PROTECTION_PATH_INVALID")
            file_id = "file-" + digest([scope["id"], relative])[:32]
            entry["file_id"] = file_id
            manifest[file_id] = entry
        return manifest, str(scan.get("epoch", ""))

    @staticmethod
    def _clean(manifest):
        return {key: {k: v for k, v in entry.items() if k != "content_b64"} for key, entry in manifest.items()}

    def _read_blob(self, sha):
        try:
            return self.blobs.get(sha)
        except (ValueError, RuntimeError) as exc:
            raise ProtectionError("PROTECTION_CONTENT_CORRUPT") from exc

    def _save_manifest(self, conn, scope_id, version_id, manifest, *, owner_kind="version", owner_id=None):
        clean = self._clean(manifest)
        hashes = set()
        for file_id, entry in manifest.items():
            if entry["category"] != "restorable":
                continue
            value = entry.get("content_b64")
            data = base64.b64decode(value, validate=True) if value is not None else self._read_blob(entry["content_digest"])
            if data is None or len(data) > 4 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != entry["content_digest"]:
                raise ProtectionError("PROTECTION_CONTENT_INVALID")
            if contains_sensitive_data(data.decode("utf-8", errors="ignore")):
                raise ProtectionError("PROTECTION_SENSITIVE_CONTENT_NOT_STORED")
            sha = entry["content_digest"]
            exists = conn.execute("SELECT 1 FROM protection_blob_refs WHERE owner_kind=? AND owner_id=? AND sha256=?",
                (owner_kind, owner_id or version_id, sha)).fetchone()
            if sha not in hashes and not exists:
                try:
                    self.blobs.put(data, conn)
                except ValueError as exc:
                    raise ProtectionError("PROTECTION_CONTENT_CORRUPT") from exc
                conn.execute("INSERT INTO protection_blob_refs VALUES (?,?,?,?)",
                    (owner_kind, owner_id or version_id, scope_id, sha))
                hashes.add(sha)
            clean[file_id]["content_digest"] = sha
        return clean

    def _release(self, conn, kind, owner_id):
        for row in conn.execute("SELECT sha256 FROM protection_blob_refs WHERE owner_kind=? AND owner_id=?", (kind, owner_id)).fetchall():
            self.blobs.release(row[0], conn)
        conn.execute("DELETE FROM protection_blob_refs WHERE owner_kind=? AND owner_id=?", (kind, owner_id))
        conn.execute("DELETE FROM protection_version_dependencies WHERE owner_kind=? AND owner_id=?", (kind, owner_id))

    def _usage(self, scope_id):
        return history._usage(self, scope_id)

    def space(self, scope_id):
        return history.space(self, scope_id)

    def shared_storage(self):
        return history.shared_storage(self)

    def _additional_bytes(self, scope, manifest):
        known = {r[0] for r in self.conn.execute("SELECT DISTINCT sha256 FROM protection_blob_refs WHERE scope_id=?", (scope["id"],))}
        sizes = dict(self.conn.execute("SELECT sha256,compressed_size FROM blobs WHERE sha256 IN (SELECT value FROM json_each(?))",
            (encoded(list({e.get("content_digest") for e in manifest.values() if e["category"] == "restorable"})),)))
        needed = 0
        for entry in manifest.values():
            sha = entry.get("content_digest")
            if entry["category"] != "restorable" or sha in known:
                continue
            if sha in sizes:
                needed += sizes[sha]
            else:
                raw = base64.b64decode(entry.get("content_b64", ""), validate=True)
                needed += len(gzip.compress(raw))
            known.add(sha)
        return needed

    def _check_capacity(self, scope, manifest, metadata_bytes=0):
        needed = self._additional_bytes(scope, manifest) + metadata_bytes
        if self._usage(scope["id"]) + needed > scope["quota_bytes"]:
            raise ProtectionError("PROTECTION_QUOTA_EXCEEDED")

    @staticmethod
    def _manifest_summary(manifest, change_count=0):
        coverage = dict.fromkeys(("restorable", "excluded", "audit_only", "unreachable"), 0)
        for entry in manifest.values():
            coverage[entry["category"]] = coverage.get(entry["category"], 0) + 1
        return {"coverage": coverage, "file_count": len(manifest), "change_count": change_count}

    def maybe_collect(self, scope_id, *, reserve_bytes=0, protect_versions=()):
        return history.maybe_collect(self, scope_id, reserve_bytes=reserve_bytes, protect_versions=protect_versions)

    def collect(self, scope_id, *, reserve_bytes=0, protect_versions=()):
        return history.collect(self, scope_id, reserve_bytes=reserve_bytes, protect_versions=protect_versions)

    def _collect(self, scope_id, *, reserve_bytes=0, protect_versions=()):
        return history._collect(self, scope_id, reserve_bytes=reserve_bytes, protect_versions=protect_versions)

    def capture(self, scope_id, *, force=False, operation_id=None):
        with self.lock:
            scope = self.scope(scope_id)
            if scope["status"] == "RETIRED":
                raise ProtectionError("PROTECTION_SCOPE_RETIRED")
            if not scope["enabled"] and operation_id is None:
                return {"scope_id": scope_id, "status": "PAUSED"}
            if not force and scope["status"] == "PAUSED" and scope["reason_code"] == "PROTECTION_QUOTA_EXCEEDED":
                return {"scope_id": scope_id, "status": "PAUSED", "reason_code": scope["reason_code"]}
            active = self.conn.execute("""SELECT id FROM protection_operations WHERE scope_id=?
                AND state IN ('QUEUED','RUNNING','COMPENSATING','NEEDS_ATTENTION')""", (scope_id,)).fetchone()
            if active and active[0] != operation_id:
                return {"scope_id": scope_id, "status": "RECOVERY_PENDING"}
            sample_started_at = now()
            try:
                manifest, epoch = self._scan(scope)
            except (OSError, RuntimeError, ValueError) as exc:
                reason = getattr(exc, "reason_code", "PROTECTION_SCAN_UNAVAILABLE")
                with self.db.transaction() as conn:
                    conn.execute("UPDATE protection_scopes SET status='DEGRADED',reason_code=?,pending_digest=NULL,pending_since=NULL WHERE id=?", (reason, scope_id))
                return {"scope_id": scope_id, "status": "DEGRADED", "reason_code": reason}
            sample_completed_at = now()
            clean = self._clean(manifest)
            observed_digest = digest(clean)
            previous = self._manifest(scope["latest_id"]) if scope["latest_id"] else {}
            if scope["latest_id"] and digest(previous) == observed_digest:
                with self.db.transaction() as conn:
                    binding = json.loads(scope["binding_json"])
                    binding["epoch"] = epoch
                    conn.execute("UPDATE protection_scopes SET status='PROTECTED',reason_code='PROTECTION_UNCHANGED',observed_at=?,pending_digest=NULL,pending_since=NULL,binding_json=? WHERE id=?", (now(), encoded(binding), scope_id))
                self.maybe_collect(scope_id)
                with self.db.transaction() as conn:
                    if self._pause_if_over_quota(conn, scope_id):
                        return {"scope_id": scope_id, "status": "PAUSED", "reason_code": "PROTECTION_QUOTA_EXCEEDED", "version_id": scope["latest_id"]}
                return {"scope_id": scope_id, "status": "UNCHANGED", "version_id": scope["latest_id"]}
            if not force and (scope["pending_digest"] != observed_digest or scope["pending_since"] is None or time.time() - scope["pending_since"] < 2):
                with self.db.transaction() as conn:
                    conn.execute("UPDATE protection_scopes SET pending_digest=?,pending_since=?,status='RECORDING',reason_code='PROTECTION_STABILITY_PENDING' WHERE id=?",
                        (observed_digest, scope["pending_since"] if scope["pending_digest"] == observed_digest else time.time(), scope_id))
                return {"scope_id": scope_id, "status": "RECORDING"}
            entries = []
            if scope["latest_id"]:
                own_targets = {}
                if operation_id:
                    operation = self.operation(operation_id)
                    final_key = "before" if operation["state"] == "COMPENSATED" else "after"
                    own_targets = {(step["before"] or step["after"])["file_id"]: step[final_key] for step in operation["steps"]}
                for file_id in sorted(set(previous) | set(clean)):
                    before, after = previous.get(file_id), clean.get(file_id)
                    if all(e is None or e["category"] == "excluded" for e in (before, after)):
                        continue
                    if same_entry(before, after):
                        continue
                    entries.append({"file_id": file_id, "relative_path": (after or before)["relative_path"],
                        "kind": "CREATED" if before is None else "DELETED" if after is None else "MODIFIED",
                        "recoverable": all(x is None or x["category"] == "restorable" for x in (before, after)),
                        "attribution": "ASG_RECOVERY" if file_id in own_targets and same_entry(after, own_targets[file_id]) else "UNATTRIBUTED"})
            summary = self._manifest_summary(clean, len(entries))
            metadata_reserve = len(encoded(clean).encode()) + len(encoded(entries).encode()) + 8192 + 512 * len({e.get("content_digest") for e in clean.values() if e["category"] == "restorable"})
            self.maybe_collect(scope_id, reserve_bytes=self._additional_bytes(scope, manifest) + metadata_reserve)
            version_id = identifier("version")
            timestamp = now()
            try:
                self._check_capacity(scope, manifest, metadata_reserve)
                with self.db.transaction() as conn:
                    clean = self._save_manifest(conn, scope_id, version_id, manifest)
                    if self._usage(scope_id) > scope["quota_bytes"]:
                        raise ProtectionError("PROTECTION_QUOTA_EXCEEDED")
                    conn.execute("INSERT INTO protection_versions(id,scope_id,parent_id,created_at,manifest_json,digest,pinned,available,kind,operation_id,sample_started_at,sample_completed_at,summary_json) VALUES (?,?,?,?,?,?,0,1,?,?,?,?,?)",
                        (version_id, scope_id, scope["latest_id"], timestamp, encoded(clean), digest(clean),
                         "BASELINE" if not scope["baseline_id"] else "CAPTURE", operation_id, sample_started_at, sample_completed_at, encoded(summary)))
                    binding = json.loads(scope["binding_json"])
                    binding["epoch"] = epoch
                    conn.execute("""UPDATE protection_scopes SET baseline_id=COALESCE(baseline_id,?),latest_id=?,
                        observed_at=?,status='PROTECTED',reason_code='PROTECTION_VERSION_SAVED',
                        pending_digest=NULL,pending_since=NULL,binding_json=? WHERE id=?""",
                        (version_id, version_id, timestamp, encoded(binding), scope_id))
                    if scope["latest_id"]:
                        batch_id = identifier("batch")
                        attribution = "ASG_RECOVERY" if entries and all(e["attribution"] == "ASG_RECOVERY" for e in entries) else "UNATTRIBUTED"
                        if entries:
                            conn.execute("INSERT INTO protection_changes(id,scope_id,before_id,after_id,created_at,attribution,operation_id,entries_json,summary_json) VALUES (?,?,?,?,?,?,?,?,?)",
                                (batch_id, scope_id, scope["latest_id"], version_id, timestamp, attribution, operation_id, encoded(entries), encoded({"change_count": len(entries)})))
                        self._event(conn, scope_id, EventType.OBSERVED_CHANGE, "RECORDED", batch_id=batch_id,
                            version_id=version_id, attribution=attribution, operation_id=operation_id, change_count=len(entries))
                    else:
                        self._event(conn, scope_id, EventType.CHECKPOINT_CREATED, "BASELINE_SAVED", version_id=version_id)
                    self._check_capacity(scope, {})
            except ProtectionError as exc:
                with self.db.transaction() as conn:
                    conn.execute("UPDATE protection_scopes SET status='PAUSED',reason_code=? WHERE id=?", (exc.reason_code, scope_id))
                return {"scope_id": scope_id, "status": "PAUSED", "reason_code": exc.reason_code}
            return {"scope_id": scope_id, "status": "RECORDED", "version_id": version_id}

    def _manifest(self, version_id):
        version = self._one("SELECT * FROM protection_versions WHERE id=?", (version_id,))
        if not version["available"]:
            raise ProtectionError("PROTECTION_VERSION_EXPIRED", 410)
        value = json.loads(version["manifest_json"])
        if digest(value) != version["digest"]:
            raise ProtectionError("PROTECTION_MANIFEST_INVALID")
        return value

    def _version_row(self, row, view="full"):
        return history._version_row(self, row, view)

    def version(self, version_id, view="full"):
        return history.version(self, version_id, view)

    @staticmethod
    def _version_fields(view):
        return history._version_fields(view)

    def versions(self, scope_id, limit=50, offset=0, view="full"):
        return history.versions(self, scope_id, limit, offset, view)

    def changes(self, scope_id, limit=50, offset=0, view="full"):
        return history.changes(self, scope_id, limit, offset, view)

    def pin(self, scope_id, version_id, pinned):
        with self.lock, self.db.transaction() as conn:
            row = self.version(version_id)
            if row["scope_id"] != scope_id or not row["available"]:
                raise ProtectionError("PROTECTION_VERSION_UNAVAILABLE")
            conn.execute("UPDATE protection_versions SET pinned=? WHERE id=?", (int(pinned), version_id))
        return {"version_id": version_id, "pinned": pinned}

    def _hydrate(self, entry):
        if entry is None:
            return None
        if entry["category"] != "restorable":
            raise ProtectionError("PROTECTION_FILE_NOT_RESTORABLE")
        value = dict(entry)
        data = self._read_blob(value["content_digest"])
        if data is None:
            raise ProtectionError("PROTECTION_CONTENT_UNAVAILABLE")
        value["content_b64"] = base64.b64encode(data).decode()
        return value

    def diff(self, scope_id, file_id, before_id, after_id):
        before = self.version(before_id)
        after = self.version(after_id)
        if any(v["scope_id"] != scope_id for v in (before, after)):
            raise ProtectionError("PROTECTION_VERSION_SCOPE_MISMATCH")
        a, b = self._manifest(before_id).get(file_id), self._manifest(after_id).get(file_id)
        if a is None and b is None:
            raise ProtectionError("PROTECTION_FILE_NOT_FOUND", 404)
        return self._content_diff(a, b, before_id, after_id)

    def _content_diff(self, a, b, before_id="current", after_id="target"):
        response = {"file_id": (b or a).get("file_id"), "relative_path": (b or a)["relative_path"], "before": a, "after": b, "text_diff": None, "binary": False}
        data = []
        for entry in (a, b):
            if entry is None:
                data.append("")
                continue
            raw = base64.b64decode(self._hydrate(entry)["content_b64"])
            try:
                if b"\x00" in raw:
                    raise UnicodeError()
                data.append(raw.decode("utf-8"))
            except UnicodeError:
                response["binary"] = True
                return response
        # Bounded diff output even for a large single line.
        text = "".join(difflib.unified_diff(data[0].splitlines(True), data[1].splitlines(True), fromfile=before_id, tofile=after_id))
        response["text_diff"] = text[:262144]
        response["truncated"] = len(text) > 262144
        return response

    def preview_diff(self, preview_id, file_id):
        preview = self._one("SELECT * FROM protection_previews WHERE id=?", (preview_id,))
        if preview["expires_at"] <= now() or preview["status"] != "READY":
            raise ProtectionError("PROTECTION_PREVIEW_EXPIRED", 410)
        selected = next((s for s in json.loads(preview["selection_json"]) if s["file_id"] == file_id), None)
        if selected is None:
            raise ProtectionError("PROTECTION_FILE_NOT_FOUND", 404)
        return self._content_diff(selected["before"], selected["after"])

    def file_content(self, scope_id, version_id, file_id):
        version = self.version(version_id)
        if version["scope_id"] != scope_id:
            raise ProtectionError("PROTECTION_VERSION_SCOPE_MISMATCH")
        entry = self._manifest(version_id).get(file_id)
        if not entry:
            raise ProtectionError("PROTECTION_FILE_NOT_FOUND", 404)
        return self._hydrate(entry)

    def import_checkpoint(self, scope_id, checkpoint_id, snapshots):
        from agentguard.discovery.workspace_authority import (
            validate_workspace_root_binding,
        )

        from .manifest import validate_snapshot_v3

        with self.lock:
            scope = self.scope(scope_id)
            binding = json.loads(scope["binding_json"])
            self._scan(scope)
            checkpoint = self.db.get_checkpoint(checkpoint_id)
            if not checkpoint:
                raise ProtectionError("PROTECTION_CHECKPOINT_NOT_FOUND", 404)
            artifact = snapshots.load_recovery_v3(checkpoint["snapshot_path"])
            if not artifact:
                raise ProtectionError("PROTECTION_CHECKPOINT_INVALID")
            workspace = artifact.get("workspace", {})
            domain = workspace.get("execution_domain_id")
            valid, _, artifact_digest = validate_snapshot_v3(artifact, expected_domain=domain)
            if not valid or artifact_digest != checkpoint["hash_sha256"] or binding["kind"] != "NATIVE":
                raise ProtectionError("PROTECTION_CHECKPOINT_INVALID")
            root = validate_workspace_root_binding(binding["root"], execution_domain_id=domain,
                expected_digest=workspace.get("root_digest"))
            if root is None:
                raise ProtectionError("PROTECTION_CHECKPOINT_SCOPE_MISMATCH")
            manifest = {}
            for entry in workspace["coverage"]:
                file_id = "file-" + digest([scope_id, entry["relative_path"]])[:32]
                copied = {**entry, "file_id": file_id}
                if entry["category"] == "restorable":
                    copied["content_b64"] = base64.b64encode(artifact["blobs"][entry["content_digest"]]).decode()
                manifest[file_id] = copied
            version_id = identifier("version")
            metadata_reserve = len(encoded(self._clean(manifest)).encode()) + 8192 + 512 * len(manifest)
            self.maybe_collect(scope_id, reserve_bytes=self._additional_bytes(scope, manifest) + metadata_reserve)
            self._check_capacity(scope, manifest, metadata_reserve)
            with self.db.transaction() as conn:
                clean = self._save_manifest(conn, scope_id, version_id, manifest)
                if self._usage(scope_id) > scope["quota_bytes"]:
                    raise ProtectionError("PROTECTION_QUOTA_EXCEEDED")
                conn.execute("INSERT INTO protection_versions(id,scope_id,parent_id,created_at,manifest_json,digest,pinned,available,kind,operation_id,sample_started_at,sample_completed_at,summary_json) VALUES (?,?,NULL,?,?,?,1,1,'IMPORTED_V3',NULL,NULL,NULL,?)",
                    (version_id, scope_id, now(), encoded(clean), digest(clean), encoded(self._manifest_summary(clean))))
                self._event(conn, scope_id, EventType.MANIFEST_VERIFIED, "V3_IMPORTED", version_id=version_id)
                self._check_capacity(scope, {})
            return {"scope_id": scope_id, "version_id": version_id, "status": "IMPORTED"}

    def file_history(self, scope_id, file_id, limit=50, offset=0):
        return history.file_history(self, scope_id, file_id, limit, offset)

    def preview(self, scope_id, mode, file_ids, *, version_id=None, batch_id=None, operation_id=None):
        with self.lock:
            scope = self.scope(scope_id)
            if scope["status"] == "RETIRED":
                raise ProtectionError("PROTECTION_SCOPE_RETIRED")
            current, epoch = self._scan(scope)
            if not file_ids or len(file_ids) != len(set(file_ids)) or len(file_ids) > 1000:
                raise ProtectionError("PROTECTION_SELECTION_INVALID", 422)
            expected_batch = None
            dependency_versions = []
            if mode == "undo_batch":
                batch = self._one("SELECT * FROM protection_changes WHERE id=? AND scope_id=?", (batch_id, scope_id))
                target = self._manifest(batch["before_id"])
                expected_batch = self._manifest(batch["after_id"])
                dependency_versions = [batch["before_id"], batch["after_id"]]
                allowed = {e["file_id"] for e in json.loads(batch["entries_json"])}
                if not set(file_ids) <= allowed:
                    raise ProtectionError("PROTECTION_SELECTION_INVALID", 422)
            elif mode == "restore_version":
                version = self.version(version_id)
                if version["scope_id"] != scope_id:
                    raise ProtectionError("PROTECTION_VERSION_SCOPE_MISMATCH")
                target = self._manifest(version_id)
                dependency_versions = [version_id]
            elif mode == "undo_operation":
                op = self.operation(operation_id)
                if op["scope_id"] != scope_id or op["state"] != "SUCCEEDED":
                    raise ProtectionError("PROTECTION_OPERATION_NOT_UNDOABLE")
                target, expected_batch = {}, {}
                for step in op["steps"]:
                    before, after = step["before"], step["after"]
                    file_id = (before or after)["file_id"]
                    if before:
                        target[file_id] = before
                    if after:
                        expected_batch[file_id] = after
                if not set(file_ids) <= { (s["before"] or s["after"])["file_id"] for s in op["steps"] }:
                    raise ProtectionError("PROTECTION_SELECTION_INVALID")
            else:
                raise ProtectionError("PROTECTION_MODE_INVALID", 422)
            selection, conflicts, hydrated_targets = [], [], {}
            for file_id in file_ids:
                before, after = current.get(file_id), target.get(file_id)
                if before is None and after is None:
                    raise ProtectionError("PROTECTION_FILE_NOT_FOUND", 404)
                if any(entry is not None and entry["category"] != "restorable" for entry in (before, after)):
                    raise ProtectionError("PROTECTION_FILE_NOT_RESTORABLE")
                if expected_batch is not None and not same_entry(before, expected_batch.get(file_id)):
                    conflicts.append(file_id)
                if after:
                    hydrated_targets["after-" + file_id] = self._hydrate(after)
                selection.append({"file_id": file_id, "relative_path": (before or after)["relative_path"],
                    "before": before, "after": after})
            if conflicts:
                return {"status": "CONFLICT", "reason_code": "PROTECTION_CURRENT_STATE_CONFLICT", "conflicts": conflicts, "preview_id": None}
            preview_id = identifier("preview")
            expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
            content = {"before-"+s["file_id"]: s["before"] for s in selection if s["before"]}
            content.update(hydrated_targets)
            clean_selection = [{**s, "before": self._clean({"e": s["before"]})["e"] if s["before"] else None} for s in selection]
            metadata_reserve = len(encoded(clean_selection).encode()) + 8192 + 512 * len(content)
            self.maybe_collect(scope_id, reserve_bytes=self._additional_bytes(scope, content) + metadata_reserve,
                protect_versions=dependency_versions)
            self._check_capacity(scope, content, metadata_reserve)
            with self.db.transaction() as conn:
                self._save_manifest(conn, scope_id, preview_id, content, owner_kind="preview")
                selection = [{**s, "before": self._clean({"e": s["before"]})["e"] if s["before"] else None} for s in selection]
                preview_digest = digest([scope_id, scope["identity_digest"], mode, epoch, expires, selection])
                conn.execute("INSERT INTO protection_previews VALUES (?,?,?,?,?,?,?,?, 'READY')",
                    (preview_id, scope_id, mode, now(), expires, preview_digest, epoch, encoded(selection)))
                conn.executemany("INSERT INTO protection_version_dependencies VALUES ('preview',?,?)", [(preview_id, v) for v in dependency_versions])
                self._check_capacity(scope, {})
            affected_works = [r[0] for r in self.conn.execute("SELECT DISTINCT work_id FROM protection_work_links WHERE kind='scope' AND object_id=?", (scope_id,))]
            return {"preview_id": preview_id, "digest": preview_digest, "expires_at": expires,
                "status": "READY", "scope_id": scope_id, "mode": mode, "selection": selection,
                "affected_work_ids": affected_works}

    def operation(self, operation_id):
        return history.operation(self, operation_id)

    def _operation_rows(self, rows, view="full"):
        return history._operation_rows(self, rows, view)

    def retained_preimage(self, operation_id, step_id):
        step = self._one("SELECT receipt_json,before_json FROM protection_steps WHERE operation_id=? AND step_id=?", (operation_id, step_id))
        receipt = json.loads(step["receipt_json"])
        for row in self._rows("SELECT detail_json FROM protection_step_events WHERE operation_id=? AND step_id=? ORDER BY sequence", (operation_id, step_id)):
            detail = json.loads(row["detail_json"])
            if "actual_before" in detail:
                receipt = detail
                break
        entry = receipt.get("actual_before", json.loads(step["before_json"]))
        if entry is None:
            return {"entry": None, "meaning": "FILE_ABSENT"}
        if not self.conn.execute("SELECT 1 FROM protection_blob_refs WHERE owner_kind='operation' AND owner_id=? AND sha256=?", (operation_id, entry["content_digest"])).fetchone():
            raise ProtectionError("PROTECTION_VERSION_EXPIRED", 410)
        return {"entry": self._hydrate(entry)}

    def operation_events(self, operation_id, limit=100, after=0):
        return history.operation_events(self, operation_id, limit, after)

    def operations(self, limit=50, offset=0, view="full"):
        return history.operations(self, limit, offset, view)

    def source_action(self, source_id, action, request_id):
        from agentguard.discovery.source_identity import resolve_source
        original_source_id = source_id
        source_id = resolve_source(self.conn, source_id)
        request_digest = digest(["source", source_id, action])
        with self.lock, self.db.transaction() as conn:
            prior = conn.execute("SELECT id,request_digest FROM protection_operations WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior[1] != request_digest:
                    previous = self.operation(prior[0])
                    command = previous.get("result", {})
                    if (command.get("action") != action or
                            resolve_source(self.conn, command.get("source_id", original_source_id)) != source_id):
                        raise ProtectionError("PROTECTION_REQUEST_REUSED")
                return self.operation(prior[0])
            op_id, timestamp = identifier("operation"), now()
            conn.execute("INSERT INTO protection_operations VALUES (?,'SOURCE',NULL,NULL,?,?,'QUEUED','PROTECTION_QUEUED',?,?,?)",
                (op_id, request_id, request_digest, encoded({"source_id": source_id, "action": action}), timestamp, timestamp))
            return self.operation(op_id)

    def execute_source(self, operation_id):
        with self.lock:
            op = self.operation(operation_id)
            if op["state"] != "QUEUED":
                return op
            command = op["result"]
            with self.db.transaction() as conn:
                conn.execute("UPDATE protection_operations SET state='RUNNING',updated_at=? WHERE id=?", (now(), operation_id))
            try:
                if command["action"] == "disconnect":
                    result = self.domains.disconnect(command["source_id"])
                else:
                    result = self.domains.connect(command["source_id"], start=command["action"] == "start-and-connect")
                result = {**result, "action": command["action"]}
                state, reason = "SUCCEEDED", "PROTECTION_SOURCE_ACTION_COMPLETE"
            except (OSError, RuntimeError, ValueError) as exc:
                result = command
                state, reason = "FAILED", getattr(exc, "reason_code", "PROTECTION_SOURCE_UNAVAILABLE")
                try:
                    observed = next((item for item in self.domains.sources() if item["source_id"] == command["source_id"]), None)
                    if observed:
                        result = {**command, "source": observed}
                except (OSError, RuntimeError, ValueError):
                    pass
            with self.db.transaction() as conn:
                conn.execute("UPDATE protection_operations SET state=?,reason_code=?,result_json=?,updated_at=? WHERE id=?",
                    (state, reason, encoded(result), now(), operation_id))
            return self.operation(operation_id)

    def record_sessions(self, source_id, result):
        from agentguard.discovery.catalog import record_sessions
        return record_sessions(self.db, source_id, result)

    def record_sources(self, result):
        from agentguard.discovery.catalog import record_sources
        return record_sources(self.db, result)

    def restore_disconnections(self):
        """Apply saved pause intent before any startup observation; never reconnect."""
        from agentguard.discovery.source_identity import resolve_source
        connections = {}
        for row in self._rows("SELECT result_json FROM protection_operations WHERE kind='SOURCE' AND state='SUCCEEDED' ORDER BY created_at,id"):
            result = json.loads(row["result_json"])
            if result.get("source_id"):
                connections[resolve_source(self.conn, result["source_id"])] = result
        for source_id, result in connections.items():
            if result.get("action") == "disconnect" or result.get("connected") is False:
                try:
                    self.domains.disconnect(source_id)
                except (OSError, RuntimeError, ValueError):
                    # The pause intent remains meaningful while its source is offline.
                    pass

    @staticmethod
    def _restore_reserve(selection):
        metadata = len(encoded(selection).encode())
        contents = sum((item.get(key) or {}).get("size", 0)
            for item in selection for key in ("before", "after"))
        return 8 * contents + 32 * metadata + 65536, 2 * metadata + 8192

    def queue_restore(self, preview_id, preview_digest, request_id):
        request_digest = digest(["restore", preview_id, preview_digest])
        with self.lock:
            prior = self.conn.execute("SELECT id FROM protection_operations WHERE request_id=?", (request_id,)).fetchone()
            if not prior:
                prepared = self._one("SELECT scope_id,selection_json,status,expires_at,digest FROM protection_previews WHERE id=?", (preview_id,))
                if prepared["status"] != "READY" or prepared["expires_at"] <= now():
                    raise ProtectionError("PROTECTION_PREVIEW_EXPIRED")
                if prepared["digest"] != preview_digest:
                    raise ProtectionError("PROTECTION_PREVIEW_MISMATCH")
                selection = json.loads(prepared["selection_json"])
                reserve, metadata_reserve = self._restore_reserve(selection)
                self.maybe_collect(prepared["scope_id"], reserve_bytes=reserve + metadata_reserve)
        with self.lock, self.db.transaction() as conn:
            prior = conn.execute("SELECT id,request_digest FROM protection_operations WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior[1] != request_digest:
                    raise ProtectionError("PROTECTION_REQUEST_REUSED")
                return self.operation(prior[0])
            preview = self._one("SELECT * FROM protection_previews WHERE id=?", (preview_id,))
            if preview["status"] != "READY" or preview["expires_at"] <= now():
                raise ProtectionError("PROTECTION_PREVIEW_EXPIRED")
            if preview["digest"] != preview_digest:
                raise ProtectionError("PROTECTION_PREVIEW_MISMATCH")
            if conn.execute("""SELECT 1 FROM protection_operations WHERE kind='RESTORE'
                AND state IN ('QUEUED','RUNNING','COMPENSATING','NEEDS_ATTENTION')""").fetchone():
                raise ProtectionError("PROTECTION_RECOVERY_BUSY")
            scope = self.scope(preview["scope_id"])
            if conn.execute("""SELECT 1 FROM work_runs WHERE scope_id=? AND state IN
                ('QUEUED','DISPATCHING','RUNNING','WAITING_APPROVAL','WAITING_INPUT','CANCELLING','FINALIZING','UNCERTAIN')""", (scope['id'],)).fetchone():
                raise ProtectionError('PROTECTION_MANAGED_WRITER_BUSY')
            selection = json.loads(preview["selection_json"])
            expected_digest = digest([scope["id"], scope["identity_digest"], preview["mode"], preview["epoch"], preview["expires_at"], selection])
            if expected_digest != preview_digest:
                raise ProtectionError("PROTECTION_PREVIEW_MISMATCH")
            op_id, timestamp = identifier("operation"), now()
            content = {}
            for s in selection:
                if s["before"]:
                    content["before-" + s["file_id"]] = s["before"]
                if s["after"]:
                    content["after-" + s["file_id"]] = s["after"]
            reserve, metadata_reserve = self._restore_reserve(selection)
            if self._usage(scope["id"]) + reserve + metadata_reserve > scope["quota_bytes"]:
                raise ProtectionError("PROTECTION_QUOTA_EXCEEDED")
            self._save_manifest(conn, scope["id"], op_id, content, owner_kind="operation")
            conn.execute("INSERT INTO protection_operation_storage VALUES (?,?,?,'RESERVED')", (op_id, scope["id"], reserve))
            conn.execute("INSERT INTO protection_operations VALUES (?,'RESTORE',?,?,?,?,'QUEUED','PROTECTION_QUEUED','{}',?,?)",
                (op_id, scope["id"], preview_id, request_id, request_digest, timestamp, timestamp))
            for index, s in enumerate(selection):
                conn.execute("INSERT INTO protection_steps VALUES (?,?,?,?,?,'PLANNED','{}')",
                    (op_id, f"{index:06d}", s["relative_path"], encoded(s["before"]), encoded(s["after"])))
            conn.execute("UPDATE protection_previews SET status='CONSUMED' WHERE id=?", (preview_id,))
            conn.execute("INSERT INTO protection_version_dependencies SELECT 'operation',?,version_id FROM protection_version_dependencies WHERE owner_kind='preview' AND owner_id=?", (op_id, preview_id))
            self._release(conn, "preview", preview_id)
            self._event(conn, scope["id"], EventType.USER_APPROVED, "RESTORE_QUEUED", operation_id=op_id)
            return self.operation(op_id)

    def _pause_if_over_quota(self, conn, scope_id):
        if scope_id and self._usage(scope_id) > self.scope(scope_id)["quota_bytes"]:
            conn.execute("UPDATE protection_scopes SET status='PAUSED',reason_code='PROTECTION_QUOTA_EXCEEDED' WHERE id=?", (scope_id,))
            return True
        return False

    def _op_state(self, operation_id, state, reason, result=None):
        storage = None
        if state in {"SUCCEEDED", "COMPENSATED", "REJECTED", "NEEDS_ATTENTION"}:
            scope_id = self._one("SELECT scope_id FROM protection_operations WHERE id=?", (operation_id,))["scope_id"]
            try:
                storage = self.domains.operation_storage(json.loads(self.scope(scope_id)["binding_json"]), operation_id)
            except (OSError, RuntimeError, ValueError):
                pass
        with self.db.transaction() as conn:
            prior = self._one("SELECT scope_id,state,reason_code,result_json FROM protection_operations WHERE id=?", (operation_id,))
            if storage is not None:
                conn.execute("UPDATE protection_operation_storage SET size_bytes=?,state='MEASURED' WHERE operation_id=?", (storage, operation_id))
            changed = (prior["state"], prior["reason_code"], prior["result_json"]) != (state, reason, encoded(result or {}))
            if changed:
                conn.execute("UPDATE protection_operations SET state=?,reason_code=?,result_json=?,updated_at=? WHERE id=?",
                    (state, reason, encoded(result or {}), now(), operation_id))
                if state in {"SUCCEEDED", "COMPENSATED", "REJECTED", "NEEDS_ATTENTION"}:
                    self._event(conn, prior["scope_id"], EventType.VALIDATOR_PASSED if state == "SUCCEEDED" else EventType.RESTORE_FAILED,
                        state, operation_id=operation_id, reason_code=reason)
            self._pause_if_over_quota(conn, prior["scope_id"])

    def _step_state(self, operation_id, step_id, state, receipt=None):
        with self.db.transaction() as conn:
            receipt = dict(receipt or {})
            scope_id = self._one("SELECT scope_id FROM protection_operations WHERE id=?", (operation_id,))["scope_id"]
            for key in ("actual_before", "after"):
                entry = receipt.get(key)
                if isinstance(entry, dict):
                    self._save_manifest(conn, scope_id, operation_id, {key: entry}, owner_kind="operation")
                    receipt[key] = {k: v for k, v in entry.items() if k != "content_b64"}
            prior = self._one("SELECT state,receipt_json FROM protection_steps WHERE operation_id=? AND step_id=?", (operation_id, step_id))
            if prior["state"] == state and prior["receipt_json"] == encoded(receipt):
                self._pause_if_over_quota(conn, scope_id)
                return
            conn.execute("UPDATE protection_steps SET state=?,receipt_json=? WHERE operation_id=? AND step_id=?",
                (state, encoded(receipt or {}), operation_id, step_id))
            conn.execute("INSERT INTO protection_step_events(operation_id,step_id,state,recorded_at,detail_json) VALUES (?,?,?,?,?)",
                (operation_id, step_id, state, now(), encoded({"receipt_state": receipt.get("state"),
                    **{key: receipt[key] for key in ("actual_before", "after") if key in receipt}})))
            self._pause_if_over_quota(conn, scope_id)

    def execute(self, operation_id):
        with recovery_lease(self.db_path), self.lock:
            op = self.operation(operation_id)
            if op["state"] != "QUEUED":
                return op
            scope = self.scope(op["scope_id"])
            binding = json.loads(scope["binding_json"])
            preview = self._one("SELECT * FROM protection_previews WHERE id=?", (op["preview_id"],))
            try:
                current, epoch = self._scan(scope)
                if epoch != preview["epoch"] or preview["expires_at"] <= now():
                    raise ProtectionError("PROTECTION_PREVIEW_EXPIRED")
                if self._usage(scope["id"]) > scope["quota_bytes"]:
                    raise ProtectionError("PROTECTION_QUOTA_EXCEEDED")
                reserve = self._one("SELECT size_bytes FROM protection_operation_storage WHERE operation_id=?", (operation_id,))["size_bytes"]
                if (self.domains.available_bytes(binding) < reserve
                        or shutil.disk_usage(self.db_path.parent).free < reserve):
                    raise ProtectionError("PROTECTION_SPACE_INSUFFICIENT")
                for step in op["steps"]:
                    file_id = (step["before"] or step["after"])["file_id"]
                    if not same_entry(current.get(file_id), step["before"]):
                        raise ProtectionError("PROTECTION_CURRENT_STATE_CONFLICT")
                    # Verify every preimage and desired content before first live write.
                    for entry in (step["before"], step["after"]):
                        if entry:
                            self._hydrate(entry)
            except (OSError, RuntimeError, ValueError) as exc:
                self._op_state(operation_id, "REJECTED", getattr(exc, "reason_code", "PROTECTION_PREFLIGHT_FAILED"))
                return self.operation(operation_id)
            with self.db.transaction() as conn:
                claimed = conn.execute("UPDATE protection_operations SET state='RUNNING',reason_code='PROTECTION_APPLYING',updated_at=? WHERE id=? AND state='QUEUED'", (now(), operation_id))
                if claimed.rowcount != 1:
                    return self.operation(operation_id)
            try:
                for step in op["steps"]:
                    step_id = step["step_id"]
                    self._step_state(operation_id, step_id, "INTENT")
                    self.fault("after_intent", operation_id, step_id)
                    receipt = self.domains.apply(binding, operation_id, step_id, step["relative_path"],
                        self._hydrate(step["before"]), self._hydrate(step["after"]))
                    self.fault("after_apply", operation_id, step_id)
                    self._step_state(operation_id, step_id, "APPLIED", receipt)
                    if receipt.get("state") not in {"APPLIED", "VERIFIED"}:
                        raise ProtectionError("PROTECTION_STEP_UNCERTAIN")
                    self._step_state(operation_id, step_id, "VERIFIED", receipt)
                current, _ = self._scan(scope)
                for step in op["steps"]:
                    file_id = (step["before"] or step["after"])["file_id"]
                    if not same_entry(current.get(file_id), step["after"]):
                        raise ProtectionError("PROTECTION_POSTCHECK_CONFLICT")
                self.fault("before_complete", operation_id)
                captured = self.capture(scope["id"], force=True, operation_id=operation_id)
                if captured["status"] not in {"RECORDED", "UNCHANGED"}:
                    raise ProtectionError(captured.get("reason_code", "PROTECTION_POSTCAPTURE_FAILED"))
                recorded = self._manifest(captured["version_id"])
                for step in op["steps"]:
                    file_id = (step["before"] or step["after"])["file_id"]
                    if not same_entry(recorded.get(file_id), step["after"]):
                        raise ProtectionError("PROTECTION_POSTCAPTURE_CONFLICT")
                self._op_state(operation_id, "SUCCEEDED", "PROTECTION_RESTORED_AND_VERIFIED",
                    {"version_id": captured["version_id"], "verified_at": now()})
            except (OSError, RuntimeError, ValueError) as exc:
                self._compensate(operation_id, getattr(exc, "reason_code", "PROTECTION_APPLY_FAILED"))
            return self.operation(operation_id)

    def _compensate(self, operation_id, reason):
        op = self.operation(operation_id)
        scope = self.scope(op["scope_id"])
        binding = json.loads(scope["binding_json"])
        if op["state"] != "NEEDS_ATTENTION":
            self._op_state(operation_id, "COMPENSATING", reason)
        try:
            for step in reversed(op["steps"]):
                step_id = step["step_id"]
                if step["state"] in {"PLANNED", "NOT_APPLIED", "COMPENSATED"}:
                    continue
                if step["state"] == "COMPENSATION_INTENT":
                    inverse_receipt = self.domains.reconcile(binding, operation_id, "compensate-" + step_id)
                    if inverse_receipt.get("state") == "VERIFIED":
                        self._step_state(operation_id, step_id, "COMPENSATED", inverse_receipt)
                        continue
                    if inverse_receipt.get("state") != "NOT_APPLIED":
                        raise ProtectionError("PROTECTION_COMPENSATION_UNCERTAIN")
                receipt = self.domains.reconcile(binding, operation_id, step_id)
                if receipt.get("state") == "NOT_APPLIED":
                    self._step_state(operation_id, step_id, "NOT_APPLIED", receipt)
                    continue
                if receipt.get("state") not in {"APPLIED", "VERIFIED", "CONFLICT"} or "actual_before" not in receipt:
                    raise ProtectionError("PROTECTION_STEP_UNCERTAIN")
                current, _ = self._scan(scope)
                file_id = (step["before"] or step["after"])["file_id"]
                actual_before = receipt["actual_before"]
                if actual_before is not None:
                    actual_before = {**actual_before, "file_id": file_id, "relative_path": step["relative_path"]}
                    with self.db.transaction() as conn:
                        self._save_manifest(conn, scope["id"], operation_id, {"actual": actual_before}, owner_kind="operation")
                if not same_entry(current.get(file_id), step["after"]):
                    raise ProtectionError("PROTECTION_EXTERNAL_EDIT_PRESERVED")
                self._op_state(operation_id, "COMPENSATING", reason)
                self._step_state(operation_id, step_id, "COMPENSATION_INTENT", receipt)
                inverse = self.domains.apply(binding, operation_id, "compensate-" + step_id,
                    step["relative_path"], self._hydrate(step["after"]), actual_before)
                self.fault("after_compensation", operation_id, step_id)
                if inverse.get("state") not in {"APPLIED", "VERIFIED"}:
                    raise ProtectionError("PROTECTION_COMPENSATION_FAILED")
                self._step_state(operation_id, step_id, "COMPENSATED", receipt)
            self._op_state(operation_id, "COMPENSATED", reason)
            self.capture(scope["id"], force=True, operation_id=operation_id)
        except (OSError, RuntimeError, ValueError) as exc:
            self._op_state(operation_id, "NEEDS_ATTENTION", getattr(exc, "reason_code", "PROTECTION_RECONCILE_UNAVAILABLE"))

    def reconcile(self, operation_id):
        with recovery_lease(self.db_path), self.lock:
            op = self.operation(operation_id)
            if op["kind"] != "RESTORE":
                return op
            if op["state"] == "QUEUED":
                return self.execute(operation_id)
            if op["state"] in {"RUNNING", "COMPENSATING", "NEEDS_ATTENTION"}:
                # A response-loss retry never calls the original mutation blindly.
                self._compensate(operation_id, "PROTECTION_INTERRUPTED")
            return self.operation(operation_id)

    def links(self, work_id):
        self._one("SELECT id FROM works WHERE id=?", (work_id,), missing="WORK_NOT_FOUND")
        items = self._rows("SELECT * FROM protection_work_links WHERE work_id=? ORDER BY created_at", (work_id,))
        for item in items:
            if item["kind"] == "session":
                session = self._one("SELECT * FROM protection_sessions WHERE id=?", (item["object_id"],))
                age = (datetime.now(UTC) - datetime.fromisoformat(session["observed_at"])).total_seconds()
                session["metadata"] = json.loads(session.pop("metadata_json"))
                session["freshness"] = "CURRENT" if -5 <= age <= 120 else "NOT_CURRENT"
                session["process_association"] = None
                item["object"] = session
            elif item["kind"] == "scope":
                scope = self.scope(item["object_id"])
                item["object"] = {key: scope[key] for key in ("id", "source_id", "status", "reason_code", "observed_at", "latest_id")}
                item["object"]["root"] = json.loads(scope["binding_json"])["root"]
        return {"items": items}

    def work_changes(self, work_id, limit=50):
        links = self.links(work_id)["items"]
        scopes = sorted({link["object_id"] for link in links if link["kind"] == "scope"})
        explicit = {}
        for link in links:
            if link["kind"] == "batch":
                explicit.setdefault(link["object_id"], []).append(link["task_id"])
        items = []
        for scope_id in scopes:
            for batch in self.changes(scope_id, limit)["items"]:
                tasks = [value for value in explicit.get(batch["id"], []) if value]
                items.append({**batch, "association": "USER_LINKED" if batch["id"] in explicit else "DIRECTORY_OBSERVATION",
                    "task_ids": tasks, "task_id": tasks[0] if len(tasks) == 1 else None})
        return {"items": sorted(items, key=lambda b: b["created_at"], reverse=True)[:limit], "scope_ids": scopes}

    def link(self, work_id, kind, object_id, task_id, request_id, expected_revision):
        with self.lock:
            def apply(conn):
                work = self._one("SELECT revision FROM works WHERE id=?", (work_id,), missing="WORK_NOT_FOUND")
                if work["revision"] != expected_revision:
                    raise ProtectionError("WORK_REVISION_CONFLICT")
                if task_id:
                    self._one("SELECT id FROM work_tasks WHERE id=? AND work_id=?", (task_id, work_id), missing="WORK_TASK_NOT_IN_WORK")
                tables = {"scope": "protection_scopes", "batch": "protection_changes", "session": "protection_sessions"}
                if kind not in tables:
                    raise ProtectionError("PROTECTION_LINK_KIND_INVALID", 422)
                obj = self._one(f"SELECT * FROM {tables[kind]} WHERE id=?", (object_id,))
                if kind == "session":
                    age = (datetime.now(UTC) - datetime.fromisoformat(obj["observed_at"])).total_seconds()
                    if not -5 <= age <= 120:
                        raise ProtectionError("PROTECTION_SESSION_NOT_CURRENT")
                if kind == "batch" and not conn.execute("SELECT 1 FROM protection_work_links WHERE work_id=? AND kind='scope' AND object_id=?", (work_id, obj["scope_id"])).fetchone():
                    raise ProtectionError("PROTECTION_SCOPE_NOT_LINKED")
                conn.execute("INSERT OR IGNORE INTO protection_work_links VALUES (?,?,?,?, 'USER_LINKED',?)",
                    (work_id, kind, object_id, task_id or "", now()))
                conn.execute("UPDATE works SET revision=revision+1,updated_at=? WHERE id=?", (now(), work_id))
                scope_id = object_id if kind == "scope" else obj.get("scope_id") if kind == "batch" else None
                return {"work_id": work_id, "revision": expected_revision + 1, "basis": "USER_LINKED", "attribution": "UNATTRIBUTED", "scope_id": scope_id}
            return self._request(request_id, ["link", work_id, kind, object_id, task_id, expected_revision], apply)

    def unlink(self, work_id, kind, object_id, task_id, request_id, expected_revision):
        with self.lock:
            def apply(conn):
                work = self._one("SELECT revision FROM works WHERE id=?", (work_id,), missing="WORK_NOT_FOUND")
                if work["revision"] != expected_revision:
                    raise ProtectionError("WORK_REVISION_CONFLICT")
                removed = conn.execute("DELETE FROM protection_work_links WHERE work_id=? AND kind=? AND object_id=? AND task_id=?",
                    (work_id, kind, object_id, task_id or ""))
                if removed.rowcount != 1:
                    raise ProtectionError("PROTECTION_LINK_NOT_FOUND", 404)
                conn.execute("UPDATE works SET revision=revision+1,updated_at=? WHERE id=?", (now(), work_id))
                scope_id = object_id if kind == "scope" else self._one("SELECT scope_id FROM protection_changes WHERE id=?", (object_id,))["scope_id"] if kind == "batch" else None
                return {"work_id": work_id, "revision": expected_revision + 1, "scope_id": scope_id}
            return self._request(request_id, ["unlink", work_id, kind, object_id, task_id, expected_revision], apply)


from .protection_runtime import ProtectionRuntime

__all__ = ["ProtectionError", "ProtectionRuntime", "ProtectionService"]
