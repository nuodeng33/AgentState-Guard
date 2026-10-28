"""Bounded history queries and retention for the ProtectionService facade."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


def candidates(service, limit=50, offset=0):
    """Read a bounded page of observed metadata, never filesystem authority."""
    rows = service._rows("""SELECT id,source_id,observed_at,json_extract(metadata_json,'$.cwd') AS path
        FROM protection_sessions WHERE json_extract(metadata_json,'$.cwd') IS NOT NULL
        AND json_extract(metadata_json,'$.cwd')<>'' ORDER BY observed_at DESC,id DESC LIMIT ? OFFSET ?""", (limit + 1, offset))
    candidates = []
    for row in rows[:limit]:
        age = (datetime.now(UTC) - datetime.fromisoformat(row["observed_at"])).total_seconds()
        candidates.append({"source_id": row["source_id"], "path": row["path"],
            "basis": "PROVIDER_SESSION_METADATA", "session_id": row["id"],
            "observed_at": row["observed_at"], "current": -5 <= age <= 120,
            "registration_required": True})
    return {"sources": service.domains.sources(), "items": candidates, "manual_registration": True,
        "limit": limit, "offset": offset, "has_more": len(rows) > limit}


def _usage(service, scope_id):
    return service.space(scope_id)["logical_retained_bytes"]


def space(service, scope_id):
    scope = service.scope(scope_id)
    blobs = service.conn.execute("""SELECT COALESCE(SUM(compressed_size),0) FROM blobs WHERE sha256 IN
        (SELECT sha256 FROM protection_blob_refs WHERE scope_id=?)""", (scope_id,)).fetchone()[0]
    recovery = service.conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM protection_operation_storage WHERE scope_id=?", (scope_id,)).fetchone()[0]
    # Scope fields are small and mutable; all child rows use transactionally
    # maintained counters, including immutable journals and evidence.
    metadata = scope["metadata_bytes"] + 32 + sum(len(str(v).encode()) for k, v in scope.items()
        if v is not None and k != "metadata_bytes")
    return {"content_bytes": blobs, "recovery_bytes": recovery, "metadata_bytes": metadata,
        "logical_retained_bytes": blobs + recovery + metadata, "quota_bytes": scope["quota_bytes"],
        "quota_kind": "LOGICAL_RETAINED", "over_quota_bytes": max(0, blobs + recovery + metadata - scope["quota_bytes"])}


def shared_storage(service):
    page_size = service.conn.execute("PRAGMA page_size").fetchone()[0]
    return {"database_bytes": service.db_path.stat().st_size,
        "wal_bytes": Path(str(service.db_path) + "-wal").stat().st_size if Path(str(service.db_path) + "-wal").exists() else 0,
        "free_page_bytes": service.conn.execute("PRAGMA freelist_count").fetchone()[0] * page_size,
        "page_bytes": service.conn.execute("PRAGMA page_count").fetchone()[0] * page_size,
        "quota_kind": "SHARED_PHYSICAL_NOT_SCOPE_QUOTA"}


def maybe_collect(service, scope_id, *, reserve_bytes=0, protect_versions=(), protect_operations=()):
    scope = service.scope(scope_id)
    if (scope["last_collected_at"] is None or time.time() - scope["last_collected_at"] >= 300
            or (reserve_bytes and service._usage(scope_id) + reserve_bytes > scope["quota_bytes"])):
        return service.collect(scope_id, reserve_bytes=reserve_bytes, protect_versions=protect_versions,
                               protect_operations=protect_operations)
    return None


def collect(service, scope_id, *, reserve_bytes=0, protect_versions=(), protect_operations=()):
    with service.lock:
        return service._collect(scope_id, reserve_bytes=reserve_bytes, protect_versions=protect_versions,
                                protect_operations=protect_operations)


def _collect(service, scope_id, *, reserve_bytes=0, protect_versions=(), protect_operations=()):
    """Release content under pressure; compact details only after retention."""
    from .protection import encoded, now
    scope = service.scope(scope_id)
    cutoff = (datetime.now(UTC) - timedelta(days=scope["retention_days"])).isoformat()
    protected_operations = set(protect_operations)
    with service.db.transaction() as conn:
        for preview in service._rows("""SELECT id FROM protection_previews WHERE scope_id=? AND
            ((expires_at<? AND status='READY') OR status='EXPIRED')""", (scope_id, now())):
            service._release(conn, "preview", preview["id"])
            conn.execute("UPDATE protection_previews SET status='EXPIRED' WHERE id=?", (preview["id"],))
        # Release terminal dependencies before considering their versions.
        for op in service._rows("""SELECT * FROM protection_operations WHERE scope_id=?
            AND state IN ('SUCCEEDED','COMPENSATED','REJECTED') ORDER BY created_at""", (scope_id,)):
            if op["id"] not in protected_operations and (
                    op["created_at"] < cutoff or service._usage(scope_id) + reserve_bytes > scope["quota_bytes"]):
                service._release(conn, "operation", op["id"])
        protected = set(protect_versions) | {scope["baseline_id"], scope["latest_id"]} | {r[0] for r in conn.execute("""SELECT id
            FROM protection_versions v WHERE scope_id=? AND (pinned=1 OR EXISTS
            (SELECT 1 FROM protection_version_dependencies d WHERE d.version_id=v.id))""", (scope_id,))}
        for version in service._rows("SELECT id,created_at,available,metadata_available FROM protection_versions WHERE scope_id=? AND (available=1 OR metadata_available=1) ORDER BY created_at,id", (scope_id,)):
            if version["id"] in protected:
                continue
            if version["created_at"] >= cutoff and service._usage(scope_id) + reserve_bytes <= scope["quota_bytes"]:
                continue
            service._release(conn, "version", version["id"])
            conn.execute("UPDATE protection_versions SET available=0 WHERE id=?", (version["id"],))
            if version["created_at"] < cutoff and version["metadata_available"]:
                # Validate the original immutable manifest before dropping its
                # detail; its original digest and summary survive unchanged.
                service.version(version["id"])
                conn.execute("UPDATE protection_versions SET manifest_json='{}',metadata_available=0 WHERE id=?", (version["id"],))
        conn.execute("""UPDATE protection_changes SET entries_json='[]',metadata_available=0
            WHERE scope_id=? AND created_at<? AND metadata_available=1
            AND (before_id IN (SELECT id FROM protection_versions WHERE available=0)
                 OR after_id IN (SELECT id FROM protection_versions WHERE available=0))
            AND before_id NOT IN (SELECT value FROM json_each(?))
            AND after_id NOT IN (SELECT value FROM json_each(?))""",
            (scope_id, cutoff, encoded(list(protected - {None})), encoded(list(protected - {None}))))
        conn.execute("UPDATE protection_scopes SET last_collected_at=? WHERE id=?", (time.time(), scope_id))
    for row in service._rows("""SELECT s.operation_id FROM protection_operation_storage s
        JOIN protection_operations o ON o.id=s.operation_id WHERE s.scope_id=? AND s.state<>'CLEANED'
        AND o.state IN ('SUCCEEDED','COMPENSATED','REJECTED') AND NOT EXISTS
        (SELECT 1 FROM protection_blob_refs r WHERE r.owner_kind='operation' AND r.owner_id=o.id)""", (scope_id,)):
        try:
            service.domains.cleanup_operation(json.loads(scope["binding_json"]), row["operation_id"])
            with service.db.transaction() as conn:
                conn.execute("UPDATE protection_operation_storage SET size_bytes=0,state='CLEANED' WHERE operation_id=?", (row["operation_id"],))
        except (OSError, RuntimeError, ValueError):
            # Offline or unproved isolation remains counted and retained.
            continue
    from agentguard.storage.gc import GCPlan, execute_gc

    plan = GCPlan()
    plan.blobs_to_remove = service.blobs.find_orphans(service.conn)
    if plan.blobs_to_remove:
        execute_gc(service.conn, service.blobs, plan, dry_run=False)
    return {"scope_id": scope_id, "storage_bytes": service._usage(scope_id), "quota_bytes": scope["quota_bytes"]}


def _version_row(service, row, view="full"):
    from .protection import ProtectionError, digest

    manifest_json = row.pop("manifest_json", None)
    row.update(json.loads(row.pop("summary_json")))
    row["metadata_available"] = bool(row["metadata_available"])
    row["history_status"] = ("DETAILS_EXPIRED" if not row["metadata_available"] else
        "FULL" if row["available"] else "CONTENT_EXPIRED")
    row["details_included"] = view == "full" and row["metadata_available"]
    row["files"] = []
    if row["details_included"]:
        manifest = json.loads(manifest_json)
        if digest(manifest) != row["digest"]:
            raise ProtectionError("PROTECTION_MANIFEST_INVALID")
        row["files"] = list(manifest.values())
    return row


def version(service, version_id, view="full"):
    fields = service._version_fields(view)
    return service._version_row(service._one(f"SELECT {fields} FROM protection_versions WHERE id=?", (version_id,)), view)


def _version_fields(view):
    fields = "id,scope_id,parent_id,created_at,digest,pinned,available,kind,operation_id,sample_started_at,sample_completed_at,summary_json,metadata_available"
    return fields + (",manifest_json" if view == "full" else "")


def versions(service, scope_id, limit=50, offset=0, view="full"):
    service.scope(scope_id)
    rows = service._rows(f"SELECT {service._version_fields(view)} FROM protection_versions WHERE scope_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (scope_id, limit, offset))
    return {"items": [service._version_row(row, view) for row in rows]}


def changes(service, scope_id, limit=50, offset=0, view="full"):
    service.scope(scope_id)
    fields = "c.id,c.scope_id,c.before_id,c.after_id,c.created_at,c.attribution,c.operation_id,c.summary_json,c.metadata_available"
    if view == "full":
        fields += ",c.entries_json"
    items = service._rows(f"""SELECT {fields},a.sample_started_at,a.sample_completed_at,
        (a.available AND b.available) AS content_available FROM protection_changes c
        JOIN protection_versions a ON a.id=c.after_id JOIN protection_versions b ON b.id=c.before_id
        WHERE c.scope_id=? ORDER BY c.created_at DESC,c.id DESC LIMIT ? OFFSET ?""", (scope_id, limit, offset))
    for item in items:
        item.update(json.loads(item.pop("summary_json")))
        item["metadata_available"] = bool(item["metadata_available"])
        item["content_available"] = bool(item["content_available"])
        item["history_status"] = ("DETAILS_EXPIRED" if not item["metadata_available"] else
            "FULL" if item["content_available"] else "CONTENT_EXPIRED")
        item["details_included"] = view == "full" and item["metadata_available"]
        item["entries"] = json.loads(item.pop("entries_json", "[]")) if item["details_included"] else []
        item.pop("entries_json", None)
        if not item["content_available"]:
            item["entries"] = [{**entry, "recoverable": False} for entry in item["entries"]]
    return {"items": items}


def file_history(service, scope_id, file_id, limit=50, offset=0):
    service.scope(scope_id)
    rows = service._rows("""SELECT id AS version_id,created_at,available,metadata_available,
        CASE WHEN metadata_available=1 THEN json_extract(manifest_json,?) END AS entry_json
        FROM protection_versions WHERE scope_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
        ("$." + json.dumps(file_id), scope_id, limit, offset))
    for row in rows:
        raw = row.pop("entry_json")
        row["file"] = json.loads(raw) if raw else None
        row["available"] = bool(row["available"])
        row["metadata_available"] = bool(row["metadata_available"])
        row["history_status"] = ("DETAILS_EXPIRED" if not row["metadata_available"] else
            "FULL" if row["available"] else "CONTENT_EXPIRED")
        row["file_state"] = "UNKNOWN_DETAILS_EXPIRED" if not row["metadata_available"] else "PRESENT" if raw else "ABSENT"
    return {"items": rows}


def operation(service, operation_id):
    return service._operation_rows([service._one("SELECT * FROM protection_operations WHERE id=?", (operation_id,))])[0]


def _operation_rows(service, rows, view="full"):
    from .protection import encoded

    ids = encoded([r["id"] for r in rows])
    counts = dict(service.conn.execute("SELECT operation_id,count(*) FROM protection_steps WHERE operation_id IN (SELECT value FROM json_each(?)) GROUP BY operation_id", (ids,)))
    steps_by_id, evidence = {}, {}
    if view == "full":
        for step in service._rows("SELECT * FROM protection_steps WHERE operation_id IN (SELECT value FROM json_each(?)) ORDER BY operation_id,step_id", (ids,)):
            step["before"] = json.loads(step.pop("before_json"))
            step["after"] = json.loads(step.pop("after_json"))
            # Adapter receipts can contain paths and bytes: never project them.
            receipt = json.loads(step.pop("receipt_json"))
            step["receipt_state"] = receipt.get("state")
            actual = receipt.get("actual_before")
            step["retained_preimage"] = service._clean({"e": actual})["e"] if actual else None
            steps_by_id.setdefault(step["operation_id"], []).append(step)
        for operation_id, event_id in service.conn.execute("SELECT transaction_id,event_id FROM evidence_ledger_events WHERE transaction_id IN (SELECT value FROM json_each(?)) ORDER BY sequence", (ids,)):
            evidence.setdefault(operation_id, []).append(event_id)
    for op in rows:
        op["result"] = json.loads(op.pop("result_json"))
        op.pop("request_digest")
        op.update(steps=steps_by_id.get(op["id"], []), evidence_refs=evidence.get(op["id"], []),
            step_count=counts.get(op["id"], 0), details_included=view == "full",
            metadata_available=True, history_status="FULL")
    return rows


def operation_events(service, operation_id, limit=100, after=0):
    service._one("SELECT id FROM protection_operations WHERE id=?", (operation_id,))
    items = service._rows("SELECT * FROM protection_step_events WHERE operation_id=? AND sequence>? ORDER BY sequence LIMIT ?", (operation_id, after, limit))
    for item in items:
        item["detail"] = json.loads(item.pop("detail_json"))
    return {"items": items}


def operations(service, limit=50, offset=0, view="full"):
    rows = service._rows("SELECT * FROM protection_operations ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (limit, offset))
    return {"items": service._operation_rows(rows, view)}
