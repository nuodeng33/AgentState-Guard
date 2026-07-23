"""Snapshot creation, comparison, and diff logic.

Two file modes:
- audit_only: hash, metadata, sanitized text — no raw content, no restore
- restorable: full file bytes compressed — only for whitelisted, non-sensitive files
"""

import gzip
import json
import os
import stat as stat_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .hasher import hash_file, hash_bytes
from .sanitizer import sanitize_text, contains_sensitive_data

SENSITIVE_FIELDS = {
    "api_key", "apikey", "token", "secret", "password", "passwd",
    "authorization", "auth", "access_token", "refresh_token",
    "client_secret", "cookie", "private_key", "ssh_key",
}


def classify_file(path: Path, restorable_paths: List[str]) -> Tuple[str, str]:
    """Classify a file as audit_only or restorable.

    Returns (mode, reason).
    """
    resolved = path.resolve()
    for rp in restorable_paths:
        allowed = Path(rp).expanduser().resolve()
        try:
            if resolved == allowed or resolved.is_relative_to(allowed):
                # Check content for sensitive data
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    if contains_sensitive_data(text):
                        return ("audit_only", "File contains sensitive data (key/token/secret)")
                except (OSError, PermissionError):
                    pass
                return ("restorable", "Whitelisted, no sensitive data detected")
        except (OSError, ValueError):
            pass
    return ("audit_only", "Not in restore whitelist")


def create_file_snapshot(path: Path, mode: str, max_text_kb: int = 64) -> Dict[str, Any]:
    """Create a file snapshot entry.

    Args:
        path: File path to snapshot.
        mode: "audit_only" or "restorable".
        max_text_kb: Max text size for audit_only sanitized content.
    """
    result: Dict[str, Any] = {
        "path": str(path),
        "mode": mode,
    }

    try:
        st = path.stat()
        result["size"] = st.st_size
        result["mtime"] = st.st_mtime
        result["mode_oct"] = oct(stat_module.S_IMODE(st.st_mode))
        result["sha256"] = hash_file(path)
    except (OSError, PermissionError):
        result["error"] = "Cannot stat file"
        return result

    if mode == "restorable":
        try:
            raw = path.read_bytes()
            result["content_gz"] = gzip.compress(raw).hex()
            result["content_sha256"] = hash_bytes(raw)
        except (OSError, PermissionError) as e:
            result["error"] = f"Cannot read file: {e}"
            return result
    else:
        # audit_only: save sanitized text preview
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_text_kb * 1024:
                text = text[:max_text_kb * 1024] + "\n... [truncated]"
            result["text_sanitized"] = sanitize_text(text)
            result["has_sensitive_data"] = contains_sensitive_data(text)
        except (OSError, PermissionError):
            result["text_sanitized"] = None
            result["has_sensitive_data"] = None

    return result


def restore_file_content(snapshot_entry: Dict[str, Any]) -> Optional[bytes]:
    """Extract original file bytes from a restorable snapshot entry.

    Returns None if the entry is audit_only or corrupt.
    """
    if snapshot_entry.get("mode") != "restorable":
        return None
    content_hex = snapshot_entry.get("content_gz")
    if not content_hex:
        return None
    try:
        compressed = bytes.fromhex(content_hex)
        raw = gzip.decompress(compressed)
        # Verify integrity
        expected_sha = snapshot_entry.get("content_sha256")
        if expected_sha:
            actual = hash_bytes(raw)
            if actual != expected_sha:
                return None
        return raw
    except (gzip.BadGzipFile, ValueError, OSError):
        return None


def create_snapshot(
    label: str,
    file_snapshots: Dict[str, Dict[str, Any]],
    config_snapshot: Dict[str, Any],
    versions: Dict[str, Optional[str]],
    container_state: Dict[str, str],
    security_state: Dict[str, bool],
    git_info: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    """Create a structured snapshot dict with full file entries."""
    now_utc = datetime.now(timezone.utc)
    return {
        "format_version": 2,
        "created_at_utc": now_utc.isoformat(),
        "created_at_local": now_utc.astimezone().isoformat(),
        "label": label,
        "git": git_info,
        "versions": versions,
        "files": file_snapshots,  # dict of path -> file snapshot
        "container": container_state,
        "security": security_state,
        "config": config_snapshot,
    }


def serialize_snapshot(data: Dict[str, Any]) -> bytes:
    """Serialize snapshot to compressed JSON bytes."""
    raw = json.dumps(data, indent=2, default=str).encode("utf-8")
    return gzip.compress(raw)


def deserialize_snapshot(raw: bytes) -> Optional[Dict[str, Any]]:
    """Deserialize compressed JSON bytes to dict. Returns None on failure."""
    try:
        decompressed = gzip.decompress(raw)
        return json.loads(decompressed.decode("utf-8"))
    except (json.JSONDecodeError, gzip.BadGzipFile, UnicodeDecodeError, OSError):
        return None


def snapshot_is_valid(raw: bytes) -> bool:
    """Quick validation that bytes are a valid gzipped snapshot."""
    try:
        data = deserialize_snapshot(raw)
        if data is None:
            return False
        # Check minimal structure
        if "format_version" not in data or "files" not in data:
            return False
        # Validate all restorable file entries have valid content
        for fpath, entry in data.get("files", {}).items():
            if entry.get("mode") == "restorable":
                content_hex = entry.get("content_gz", "")
                if not content_hex:
                    return False
                try:
                    compressed = bytes.fromhex(content_hex)
                    gzip.decompress(compressed)
                except Exception:
                    return False
        return True
    except Exception:
        return False


def diff_snapshots(
    current: Dict[str, Any],
    reference: Dict[str, Any],
) -> List[Dict[str, object]]:
    """Compare two snapshots, return list of changes.

    Works with both v1 (hash-only) and v2 (file-entry) snapshot formats.
    """
    changes: List[Dict[str, object]] = []

    # Determine format version
    cur_files = current.get("files") or {}
    ref_files = reference.get("files") or {}
    cur_hashes = current.get("file_hashes") or {}
    ref_hashes = reference.get("file_hashes") or {}

    # Compare versions
    cur_ver = current.get("versions", {}) or {}
    ref_ver = reference.get("versions", {}) or {}
    for tool in set(list(cur_ver.keys()) + list(ref_ver.keys())):
        before = ref_ver.get(tool)
        after = cur_ver.get(tool)
        if before != after:
            changes.append({
                "type": "version_changed",
                "key": tool,
                "before": before,
                "after": after,
            })

    # Compare files (v2 format)
    if cur_files and ref_files:
        for fpath in set(list(cur_files.keys()) + list(ref_files.keys())):
            cur_entry = cur_files.get(fpath) or {}
            ref_entry = ref_files.get(fpath) or {}
            cur_hash = cur_entry.get("sha256")
            ref_hash = ref_entry.get("sha256")
            if cur_hash != ref_hash:
                changes.append({
                    "type": "hash_changed",
                    "key": fpath,
                    "before": ref_hash,
                    "after": cur_hash,
                    "mode": cur_entry.get("mode", "unknown"),
                })
    # Compare files (v1 hash-only format, fallback)
    elif cur_hashes and ref_hashes:
        for fpath in set(list(cur_hashes.keys()) + list(ref_hashes.keys())):
            before = ref_hashes.get(fpath)
            after = cur_hashes.get(fpath)
            if before != after:
                changes.append({
                    "type": "hash_changed",
                    "key": fpath,
                    "before": before,
                    "after": after,
                    "mode": "audit_only",
                })

    # Compare config
    cur_cfg = _flatten(current.get("config", {}))
    ref_cfg = _flatten(reference.get("config", {}))
    for key in set(list(cur_cfg.keys()) + list(ref_cfg.keys())):
        before = ref_cfg.get(key)
        after = cur_cfg.get(key)
        if before != after:
            changes.append({
                "type": "config_changed",
                "key": key,
                "before": before,
                "after": after,
            })

    return changes


def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten nested dict to dot-separated keys."""
    result: Dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten(v, key))
        else:
            result[key] = v
    return result
