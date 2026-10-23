"""Bounded, read-only provider-store metadata for native and fixed helpers.

Remote helpers use only the standard library under ``python -I``. Native
Windows process hints optionally use psutil. This module never starts a provider.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

PROVIDERS = frozenset({"CODEX", "KIMI_CODE", "ZCODE", "CLOUDCLI"})
MAX_PAGE = 50
MAX_OFFSET = 100_000
MAX_ENTRIES = 25_000
MAX_INDEX_BYTES = 1024 * 1024
# Codex session_meta can include base_instructions; only selected metadata is returned.
MAX_METADATA_BYTES = 256 * 1024
QUERY_SECONDS = 5.0
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


class _StoreFailure(Exception):
    def __init__(self, reason_code: str, *, partial: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.partial = partial


def default_stores(process_hints: object = (), *, coverage: dict | None = None) -> list[dict[str, str]]:
    """Observe service-home and OS-user defaults; neither implies a PID binding."""
    home = Path.home()
    homes = [home]
    if os.name == "nt":
        import ctypes
        profile = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 40, None, 0, profile) == 0:
            current_profile = Path(profile.value)
            if os.path.normcase(str(current_profile)) != os.path.normcase(str(home)):
                homes.append(current_profile)
    stores = []
    for root in homes:
        for provider, directory in (("CODEX", ".codex"), ("KIMI_CODE", ".kimi-code"),
                                    ("KIMI_CODE", ".kimi"),
                                    ("ZCODE", ".zcode"), ("CLOUDCLI", ".cloudcli")):
            stores.append({"provider": provider, "path": str(root / directory),
                           "locator_basis": "SERVICE_HOME" if root == home else "OS_USER_PROFILE"})
    for provider, key in (("CODEX", "CODEX_HOME"), ("KIMI_CODE", "KIMI_CODE_HOME"),
                          ("KIMI_CODE", "KIMI_SHARE_DIR"), ("CLOUDCLI", "DATABASE_PATH")):
        if os.environ.get(key):
            stores.append({"provider": provider, "path": os.environ[key], "locator_basis": "EXPLICIT_ENV"})
    requested = verified = inspected = 0
    if isinstance(process_hints, (list, tuple)):
        requested = len(process_hints)
        for hint in process_hints[:64]:
            inspected += 1
            if not isinstance(hint, dict) or hint.get("provider") not in PROVIDERS:
                continue
            provider = hint["provider"]
            environment = _process_environment(hint)
            if environment is None:
                continue
            verified += 1
            keys = {"CODEX": ("CODEX_HOME",), "KIMI_CODE": ("KIMI_SHARE_DIR", "KIMI_CODE_HOME"),
                    "ZCODE": (), "CLOUDCLI": ("DATABASE_PATH",)}[provider]
            for key in keys:
                value = environment.get(key)
                if _candidate_path(provider, value):
                    stores.append({"provider": provider, "path": value,
                                   "locator_basis": "PROCESS_ENV"})
            home_value = environment.get("USERPROFILE") or environment.get("HOME")
            if _candidate_path(provider, home_value):
                for directory in ({"CODEX": (".codex",), "KIMI_CODE": (".kimi", ".kimi-code"),
                                   "ZCODE": (".zcode",), "CLOUDCLI": (".cloudcli",)}[provider]):
                    stores.append({"provider": provider, "path": str(Path(home_value) / directory),
                                   "locator_basis": "PROCESS_HOME"})
    if coverage is not None:
        not_inspected = requested - inspected
        coverage.update(requested=requested, verified=verified,
                        unavailable=inspected - verified, not_inspected=not_inspected,
                        limit_reason="PROCESS_HINT_LIMIT" if not_inspected else None)
    unique = {}
    for item in stores:
        path = Path(item["path"])
        try:
            _checked_path(path, directory=not (item["provider"] == "CLOUDCLI" and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}))
            canonical = str(path.resolve(strict=True))
        except (_StoreFailure, OSError, RuntimeError):
            canonical = os.path.abspath(item["path"])
        unique[(item["provider"], os.path.normcase(os.path.normpath(canonical)))] = item
    return list(unique.values())


def _candidate_path(provider: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _locator(provider, value)
    except _StoreFailure:
        return False
    return True


def _process_environment(hint: dict) -> dict[str, str] | None:
    pid = hint.get("pid")
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == "nt":
        expected = hint.get("create_time")
        if isinstance(expected, str):
            try:
                parsed = datetime.fromisoformat(expected)
                if parsed.tzinfo is None:
                    return None
                expected = parsed.timestamp()
            except ValueError:
                return None
        if (not isinstance(expected, (int, float)) or isinstance(expected, bool)
                or not math.isfinite(expected)):
            return None
        try:
            import psutil
        except ImportError:
            return None
        try:
            process = psutil.Process(pid)
            if round(process.create_time(), 6) != round(float(expected), 6):
                return None
            environment = process.environ()
            if round(psutil.Process(pid).create_time(), 6) != round(float(expected), 6):
                return None
            return {key: value for key, value in environment.items()
                    if key in {"CODEX_HOME", "KIMI_SHARE_DIR", "KIMI_CODE_HOME",
                               "DATABASE_PATH", "HOME", "USERPROFILE"}}
        except (psutil.Error, OSError, ValueError):
            return None
    expected = hint.get("start_ticks")
    if type(expected) is not int or expected <= 0:
        return None
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        def start_ticks() -> int:
            raw = stat_path.read_bytes()
            return int(raw[raw.rfind(b")") + 2:].split()[19])

        if start_ticks() != expected:
            return None
        with (Path("/proc") / str(pid) / "environ").open("rb") as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024 or start_ticks() != expected:
            return None
        names = {b"CODEX_HOME", b"KIMI_SHARE_DIR", b"KIMI_CODE_HOME", b"DATABASE_PATH",
                 b"HOME", b"USERPROFILE"}
        values = {}
        for field in raw.split(b"\0"):
            key, separator, value = field.partition(b"=")
            if separator and key in names:
                values[key.decode("ascii")] = value.decode("utf-8", errors="ignore")
        return values
    except (OSError, ValueError, IndexError):
        return None


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        os.name == "nt" and getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _locator(provider: str, raw_path: str | os.PathLike[str]) -> tuple[Path, Path, Path]:
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise _StoreFailure("SESSION_PROVIDER_UNSUPPORTED")
    try:
        value = os.fspath(raw_path)
    except TypeError:
        raise _StoreFailure("SESSION_STORE_PATH_UNSAFE") from None
    if not isinstance(value, str) or not 0 < len(value) <= 4096 or "\x00" in value:
        raise _StoreFailure("SESSION_STORE_PATH_UNSAFE")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise _StoreFailure("SESSION_STORE_PATH_UNSAFE")
    if provider == "CLOUDCLI" and (path.name != ".cloudcli" and
                                   (path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
                                    or not path.exists() or path.is_file())):
        return path, path.parent, path
    root = path
    database = root / "auth.db" if provider == "CLOUDCLI" else root
    return path, root, database


def _checked_path(path: Path, *, directory: bool) -> os.stat_result:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            raise _StoreFailure("SESSION_STORE_NOT_PRESENT") from None
        except OSError:
            raise _StoreFailure("SESSION_STORE_UNREACHABLE") from None
        if _is_reparse(info):
            raise _StoreFailure("SESSION_STORE_UNSAFE")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise _StoreFailure("SESSION_STORE_UNSAFE")
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise _StoreFailure("SESSION_STORE_NOT_PRESENT") from None
    except OSError:
        raise _StoreFailure("SESSION_STORE_UNREACHABLE") from None
    if _is_reparse(info) or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise _StoreFailure("SESSION_STORE_UNSAFE")
    return info


def _identity(info: os.stat_result) -> str:
    physical = f"{info.st_dev}:{info.st_ino}".encode("ascii")
    return hashlib.sha256(physical).hexdigest()[:32]


def _locations(provider: str, root: Path, database: Path) -> tuple[Path, ...]:
    if provider == "CODEX":
        return (root / "sessions",)
    if provider == "KIMI_CODE":
        if (root / "session_index.jsonl").exists():
            return (root / "sessions", root / "session_index.jsonl")
        return (root / "kimi.json",)
    if provider == "ZCODE":
        return (root / "v2" / "tasks-index.sqlite", root / "cli" / "db" / "db.sqlite")
    return (database,)


def inspect_store(provider: str, path: str | os.PathLike[str]) -> dict[str, object]:
    """Validate a fixed locator and bind it to its directory, not DB inode."""
    result: dict[str, object] = {"path": None, "identity": None,
                                 "status": "UNKNOWN", "reason_code": "SESSION_STORE_NOT_PRESENT"}
    try:
        locator, root, database = _locator(provider, path)
        result["path"] = str(locator)
        result["identity"] = _identity(_checked_path(root, directory=True))
        if provider == "CODEX":
            found = False
            for item in (root / "sessions", root / "archived_sessions"):
                try:
                    _checked_path(item, directory=True)
                    found = True
                except _StoreFailure as failure:
                    if failure.reason_code != "SESSION_STORE_NOT_PRESENT":
                        raise
            if not found:
                result.update(status="EMPTY", reason_code="SESSION_STORE_EMPTY")
                return result
            result.update(status="AVAILABLE", reason_code="SESSION_STORE_AVAILABLE")
            return result
        locations = _locations(provider, root, database)
        missing = 0
        for item in locations:
            try:
                _checked_path(item, directory=provider == "CODEX" or
                              (provider == "KIMI_CODE" and item.name == "sessions"))
            except _StoreFailure as failure:
                if provider == "ZCODE" and failure.reason_code == "SESSION_STORE_NOT_PRESENT":
                    missing += 1
                    continue
                raise
        if provider == "ZCODE" and missing:
            if missing == len(locations):
                raise _StoreFailure("SESSION_STORE_NOT_PRESENT")
            result.update(status="DEGRADED", reason_code="SESSION_STORE_PARTIAL")
        else:
            result.update(status="AVAILABLE", reason_code="SESSION_STORE_AVAILABLE")
    except _StoreFailure as failure:
        result.update(status="UNKNOWN", reason_code=failure.reason_code)
    return result


def _envelope(provider: str, *, status: str = "UNKNOWN", reason: str = "SESSION_STORE_NOT_PRESENT",
              sessions: list[dict] | None = None, has_more: bool = False,
              next_offset: int | None = None, store_identity: str | None = None) -> dict:
    return {"schema_version": "discovery-sessions-1", "provider": provider,
            "scope": "PROVIDER_STORE", "identity_basis": "STORE_METADATA",
            "version": None, "protocol_version": None, "status": status,
            "reason_code": reason, "sessions": sessions or [], "has_more": has_more,
            "next_offset": next_offset, "store_identity": store_identity,
            "revision": None, "not_modified": False}


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _cwd(value: object) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= 4096:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value):
        return value
    return None


def _timestamp(value: object) -> str | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if abs(number) > 10**14:
                number /= 10**6
            elif abs(number) > 10**11:
                number /= 1000
            parsed = datetime.fromtimestamp(number, UTC)
        elif isinstance(value, str) and 0 < len(value) <= 64:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        else:
            return None
        return parsed.astimezone(UTC).isoformat()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _title(value: object) -> str | None:
    return value[:4096] if isinstance(value, str) and value else None


def _page(items: list[dict], limit: int, offset: int, *, partial: bool = False,
          reason: str = "SESSION_STORE_METADATA_AVAILABLE", more_unknown: bool = False) -> tuple:
    selected = items[offset:offset + limit]
    more = offset + limit < len(items) or more_unknown
    next_offset = offset + len(selected) if more and selected else None
    if not items and not partial:
        return "EMPTY", "SESSION_STORE_EMPTY", selected, False, None
    if partial:
        return ("DEGRADED" if items else "UNKNOWN"), reason, selected, more, next_offset
    return "AVAILABLE", reason, selected, more, next_offset


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _StoreFailure("SESSION_QUERY_LIMIT")


def _read_exact(path: Path, maximum: int) -> bytes:
    info = _checked_path(path, directory=False)
    if info.st_size > maximum or info.st_nlink != 1:
        raise _StoreFailure("SESSION_INDEX_LIMIT")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError:
        raise _StoreFailure("SESSION_INDEX_UNREACHABLE") from None
    if len(raw) > maximum:
        raise _StoreFailure("SESSION_INDEX_LIMIT")
    return raw


def _codex_inventory(root: Path, identity: str, limit: int, offset: int,
                     deadline: float) -> tuple[str, list[tuple[int, Path]]]:
    digest = hashlib.sha256()
    digest.update(repr(("CODEX", identity, limit, offset)).encode("utf-8"))
    root_info = _checked_path(root, directory=True)
    device = root_info.st_dev

    def add(path: Path, info: os.stat_result) -> None:
        if _is_reparse(info) or info.st_dev != device:
            raise _StoreFailure("SESSION_STORE_UNSAFE")
        digest.update(repr((str(path), info.st_dev, info.st_ino, info.st_mode,
                            info.st_nlink, info.st_size, info.st_mtime_ns,
                            info.st_ctime_ns)).encode("utf-8"))

    add(root, root_info)
    pending: list[tuple[Path, int]] = []
    for base in (root / "sessions", root / "archived_sessions"):
        try:
            info = _checked_path(base, directory=True)
        except _StoreFailure as failure:
            if failure.reason_code != "SESSION_STORE_NOT_PRESENT":
                raise
            digest.update(repr((str(base), None)).encode("utf-8"))
            continue
        add(base, info)
        pending.append((base, 0))
    if not pending:
        return digest.hexdigest(), []
    candidates: list[tuple[int, Path]] = []
    seen = 0
    while pending:
        _check_deadline(deadline)
        directory, depth = pending.pop()
        try:
            children = []
            with os.scandir(directory) as stream:
                for child in stream:
                    seen += 1
                    if seen > MAX_ENTRIES:
                        raise _StoreFailure("SESSION_INDEX_LIMIT")
                    children.append(child)
            children.sort(key=lambda child: child.name)
        except OSError:
            raise _StoreFailure("SESSION_STORE_UNREACHABLE") from None
        for child in children:
            _check_deadline(deadline)
            try:
                path = Path(child.path)
                info = path.lstat()
            except OSError:
                raise _StoreFailure("SESSION_STORE_UNREACHABLE") from None
            add(path, info)
            if stat.S_ISDIR(info.st_mode):
                if child.name == "subagents":
                    continue
                if depth < 3:
                    pending.append((path, depth + 1))
                else:
                    raise _StoreFailure("SESSION_INDEX_LIMIT")
            elif stat.S_ISREG(info.st_mode) and child.name.startswith("rollout-") and child.name.endswith(".jsonl"):
                candidates.append((info.st_mtime_ns, path))
    candidates.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    return digest.hexdigest(), candidates


def _codex(root: Path, limit: int, offset: int, deadline: float,
           candidates: list[tuple[int, Path]]) -> tuple:
    entries: list[dict] = []
    ids: set[str] = set()
    partial = False
    limited = False
    metadata_limited = False
    page_end = min(offset + limit, len(candidates))
    cursor = offset
    for modified, candidate in candidates[offset:page_end]:
        if time.monotonic() >= deadline:
            limited = True
            break
        cursor += 1
        try:
            _checked_path(candidate, directory=False)
            fd = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            with os.fdopen(fd, "rb") as stream:
                raw = stream.readline(MAX_METADATA_BYTES + 1)
            if len(raw) > MAX_METADATA_BYTES:
                metadata_limited = True
                continue
            if not raw.endswith(b"\n"):
                partial = True
                continue
            record = json.loads(raw)
            metadata = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(record, dict) or record.get("type") != "session_meta" or not isinstance(metadata, dict):
                raise ValueError
            session_id = metadata.get("id")
            if not _valid_id(session_id):
                raise ValueError
            if session_id in ids:
                partial = True
                continue
            ids.add(session_id)
            entries.append({"session_id": session_id, "provider": "CODEX", "title": None,
                            "updated_at": _timestamp(modified / 10**9),
                            "cwd": _cwd(metadata.get("cwd"))})
        except (OSError, ValueError, TypeError, UnicodeDecodeError, _StoreFailure):
            partial = True
    reason = ("SESSION_INDEX_LIMIT" if limited else "SESSION_METADATA_LIMIT" if metadata_limited
              else "SESSION_METADATA_PARTIAL" if partial else "SESSION_STORE_METADATA_AVAILABLE")
    incomplete = partial or limited or metadata_limited
    status = ("DEGRADED" if entries else "UNKNOWN") if incomplete else (
        "EMPTY" if not candidates else "AVAILABLE")
    if status == "EMPTY":
        reason = "SESSION_STORE_EMPTY"
    has_more = cursor < len(candidates) or limited
    next_offset = cursor if has_more and cursor > offset else None
    coverage = {"metadata_scope": "PAGE", "candidate_count": len(candidates),
                "candidate_offset": offset, "candidate_end": cursor}
    return status, reason, entries, has_more, next_offset, coverage


def _kimi(root: Path, limit: int, offset: int, deadline: float) -> tuple:
    if not (root / "session_index.jsonl").exists():
        return _kimi_official(root, limit, offset, deadline)
    sessions_root = root / "sessions"
    _checked_path(sessions_root, directory=True)
    raw = _read_exact(root / "session_index.jsonl", MAX_INDEX_BYTES)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise _StoreFailure("SESSION_INDEX_UNREACHABLE") from None
    if len(lines) > MAX_ENTRIES:
        raise _StoreFailure("SESSION_INDEX_LIMIT")
    entries: dict[str, tuple[Path, str]] = {}
    partial = bool(raw and not raw.endswith(b"\n"))
    if partial:
        lines = lines[:-1]
    for line in lines:
        if time.monotonic() >= deadline:
            raise _StoreFailure("SESSION_QUERY_LIMIT")
        try:
            item = json.loads(line)
        except ValueError:
            partial = True
            continue
        if not isinstance(item, dict) or not _valid_id(item.get("sessionId")):
            partial = True
            continue
        session_id = item["sessionId"]
        if item.get("deleted") is True:
            entries.pop(session_id, None)
            continue
        raw_dir, work_dir = item.get("sessionDir"), _cwd(item.get("workDir"))
        if not isinstance(raw_dir, str) or work_dir is None:
            partial = True
            continue
        candidate = Path(raw_dir)
        try:
            parts = candidate.relative_to(sessions_root).parts
        except ValueError:
            partial = True
            continue
        if len(parts) != 2 or parts[1] != session_id or any(part in {"", ".", ".."} for part in parts):
            partial = True
            continue
        entries[session_id] = (candidate, work_dir)
    candidates = []
    for session_id, (directory, work_dir) in entries.items():
        if time.monotonic() >= deadline:
            raise _StoreFailure("SESSION_QUERY_LIMIT")
        try:
            _checked_path(directory, directory=True)
            modified = directory.stat().st_mtime_ns
            for name in ("context.jsonl", "wire.jsonl", f"{session_id}.jsonl"):
                file = directory / name
                if file.exists():
                    modified = max(modified, _checked_path(file, directory=False).st_mtime_ns)
            candidates.append((modified, session_id, work_dir))
        except _StoreFailure:
            partial = True
    candidates.sort(reverse=True)
    sessions = [{"session_id": session_id, "provider": "KIMI_CODE", "title": None,
                 "updated_at": _timestamp(modified / 10**9), "cwd": work_dir}
                for modified, session_id, work_dir in candidates]
    reason = "SESSION_INDEX_STALE" if partial else "SESSION_STORE_METADATA_AVAILABLE"
    return _page(sessions, limit, offset, partial=partial, reason=reason)


def _kimi_official(root: Path, limit: int, offset: int, deadline: float) -> tuple:
    raw = _read_exact(root / "kimi.json", MAX_INDEX_BYTES)
    try:
        metadata = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        raise _StoreFailure("SESSION_INDEX_UNREACHABLE") from None
    work_dirs = metadata.get("work_dirs") if isinstance(metadata, dict) else None
    if not isinstance(work_dirs, list):
        raise _StoreFailure("SESSION_SCHEMA_UNRECOGNIZED")
    if len(work_dirs) > MAX_ENTRIES:
        raise _StoreFailure("SESSION_INDEX_LIMIT")
    sessions_root = root / "sessions"
    entries: list[tuple[int, str, str]] = []
    seen_ids: set[str] = set()
    partial = False
    visited = 0
    for work in work_dirs:
        _check_deadline(deadline)
        if not isinstance(work, dict) or (cwd := _cwd(work.get("path"))) is None:
            partial = True
            continue
        kaos = work.get("kaos", "local")
        if not isinstance(kaos, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", kaos):
            partial = True
            continue
        digest = hashlib.md5(work["path"].encode("utf-8")).hexdigest()
        bucket = sessions_root / (digest if kaos == "local" else f"{kaos}_{digest}")
        try:
            _checked_path(bucket, directory=True)
            with os.scandir(bucket) as stream:
                for child in stream:
                    _check_deadline(deadline)
                    visited += 1
                    if visited > MAX_ENTRIES:
                        raise _StoreFailure("SESSION_INDEX_LIMIT", partial=True)
                    legacy_file = child.is_file(follow_symlinks=False) and child.name.endswith(".jsonl")
                    if legacy_file:
                        session_id = child.name[:-6]
                        if not _valid_id(session_id) or (bucket / session_id).exists():
                            continue
                        try:
                            info = _checked_path(Path(child.path), directory=False)
                        except _StoreFailure:
                            partial = True
                            continue
                        if session_id in seen_ids:
                            partial = True
                            continue
                        seen_ids.add(session_id)
                        entries.append((info.st_mtime_ns, session_id, cwd))
                        continue
                    if not child.is_dir(follow_symlinks=False) or not _valid_id(child.name):
                        partial = True
                        continue
                    session_id = child.name
                    if session_id in seen_ids:
                        partial = True
                        continue
                    directory = Path(child.path)
                    try:
                        _checked_path(directory, directory=True)
                        context = directory / "context.jsonl"
                        if not context.exists():
                            context = bucket / f"{session_id}.jsonl"
                        info = _checked_path(context, directory=False)
                        if info.st_nlink != 1:
                            raise _StoreFailure("SESSION_STORE_UNSAFE")
                        modified = info.st_mtime_ns
                        wire = directory / "wire.jsonl"
                        if wire.exists():
                            modified = max(modified, _checked_path(wire, directory=False).st_mtime_ns)
                    except _StoreFailure:
                        partial = True
                        continue
                    seen_ids.add(session_id)
                    entries.append((modified, session_id, cwd))
        except _StoreFailure as failure:
            if failure.reason_code == "SESSION_INDEX_LIMIT":
                raise
            partial = True
        except OSError:
            partial = True
    entries.sort(reverse=True)
    sessions = [{"session_id": session_id, "provider": "KIMI_CODE", "title": None,
                 "updated_at": _timestamp(modified / 10**9), "cwd": cwd}
                for modified, session_id, cwd in entries]
    return _page(sessions, limit, offset, partial=partial,
                 reason="SESSION_INDEX_STALE" if partial else "SESSION_STORE_METADATA_AVAILABLE")


def _sqlite_open(path: Path, deadline: float) -> sqlite3.Connection:
    _check_deadline(deadline)
    _checked_path(path, directory=False)
    uri = "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"
    connection = None
    ready = False
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=250")
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        _check_deadline(deadline)
        ready = True
        return connection
    except sqlite3.Error:
        raise _StoreFailure("SESSION_QUERY_LIMIT" if time.monotonic() >= deadline
                            else "SESSION_STORE_UNREACHABLE") from None
    finally:
        if connection is not None and not ready:
            connection.close()


def _columns(connection: sqlite3.Connection, table: str, required: set[str], deadline: float) -> None:
    _check_deadline(deadline)
    try:
        names = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        raise _StoreFailure("SESSION_QUERY_LIMIT" if time.monotonic() >= deadline
                            else "SESSION_SCHEMA_UNRECOGNIZED") from None
    _check_deadline(deadline)
    if not required <= names:
        raise _StoreFailure("SESSION_SCHEMA_UNRECOGNIZED")


def _cloudcli(database: Path, limit: int, offset: int, deadline: float) -> tuple:
    connection = _sqlite_open(database, deadline)
    try:
        _columns(connection, "sessions", {"session_id", "provider", "provider_session_id",
                                           "custom_name", "project_path", "isArchived",
                                           "created_at", "updated_at"}, deadline)
        total = connection.execute("SELECT COUNT(*) FROM sessions WHERE isArchived=0").fetchone()[0]
        _check_deadline(deadline)
        rows = connection.execute(
            """SELECT session_id, provider, provider_session_id, custom_name, project_path,
                      COALESCE(updated_at,created_at) FROM sessions WHERE isArchived=0
               ORDER BY datetime(COALESCE(updated_at,created_at)) DESC, session_id DESC
               LIMIT ? OFFSET ?""", (limit, offset),
        ).fetchall()
        _check_deadline(deadline)
    except sqlite3.Error:
        raise _StoreFailure("SESSION_QUERY_LIMIT" if time.monotonic() >= deadline
                            else "SESSION_SCHEMA_UNRECOGNIZED") from None
    finally:
        connection.close()
    sessions = []
    partial = False
    for app_id, native_provider, native_id, title, project_path, updated in rows:
        if not _valid_id(app_id):
            partial = True
            continue
        native = native_provider.upper() if isinstance(native_provider, str) and native_provider.lower() in {
            "claude", "codex", "cursor", "opencode"} else None
        valid_native_id = native_id if _valid_id(native_id) else None
        if native is None or (native_id is not None and valid_native_id is None):
            partial = True
        sessions.append({"session_id": app_id, "provider": "CLOUDCLI", "title": _title(title),
                         "updated_at": _timestamp(updated), "cwd": _cwd(project_path),
                         "native_provider": native, "native_session_id": valid_native_id,
                         "app_session_id": app_id, "index_provenance": "CLOUDCLI_SQLITE",
                         "mapping_status": "PROVIDER_SESSION" if native and valid_native_id else "APP_ONLY"})
    more = offset + limit < total
    status = "DEGRADED" if partial and sessions else "UNKNOWN" if partial else "AVAILABLE" if total else "EMPTY"
    reason = "SESSION_METADATA_PARTIAL" if partial else "SESSION_STORE_METADATA_AVAILABLE" if total else "SESSION_STORE_EMPTY"
    return status, reason, sessions, more, offset + len(rows) if more and rows else None


def _same_directory(left: object, right: object) -> bool:
    if _cwd(left) is None or _cwd(right) is None:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _zcode(root: Path, limit: int, offset: int, deadline: float) -> tuple:
    task_db, session_db = _locations("ZCODE", root, root)
    connection = _sqlite_open(task_db, deadline)
    linked = False
    try:
        _columns(connection, "tasks", {"task_id", "title", "updated_at", "workspace_path", "archived", "deleted"}, deadline)
        try:
            _checked_path(session_db, directory=False)
        except _StoreFailure as failure:
            if failure.reason_code != "SESSION_STORE_NOT_PRESENT":
                raise
        else:
            uri = "file:" + quote(session_db.as_posix(), safe="/:") + "?mode=ro"
            connection.execute("ATTACH DATABASE ? AS linked", (uri,))
            names = {row[1] for row in connection.execute("PRAGMA linked.table_info(session)")}
            if not {"id", "directory"} <= names:
                raise _StoreFailure("SESSION_SCHEMA_UNRECOGNIZED", partial=True)
            linked = True
            connection.create_function("same_directory", 2, _same_directory)
        total = connection.execute("SELECT COUNT(*) FROM tasks WHERE COALESCE(deleted,0)=0").fetchone()[0]
        _check_deadline(deadline)
        if linked:
            unmatched = connection.execute(
                """SELECT EXISTS(SELECT 1 FROM tasks AS t
                   LEFT JOIN linked.session AS s ON s.id=t.task_id
                   WHERE COALESCE(t.deleted,0)=0 AND
                     (s.id IS NULL OR NOT same_directory(s.directory,t.workspace_path)))"""
            ).fetchone()[0]
            _check_deadline(deadline)
            rows = connection.execute(
                """SELECT t.task_id,t.title,t.updated_at,t.workspace_path,t.archived,s.id,s.directory
                   FROM tasks AS t LEFT JOIN linked.session AS s ON s.id=t.task_id
                   WHERE COALESCE(t.deleted,0)=0 ORDER BY t.updated_at DESC,t.task_id DESC
                   LIMIT ? OFFSET ?""", (limit, offset),
            ).fetchall()
        else:
            unmatched = total
            rows = connection.execute(
                """SELECT task_id,title,updated_at,workspace_path,archived,NULL,NULL
                   FROM tasks WHERE COALESCE(deleted,0)=0
                   ORDER BY updated_at DESC,task_id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        _check_deadline(deadline)
    except sqlite3.Error:
        raise _StoreFailure("SESSION_QUERY_LIMIT" if time.monotonic() >= deadline
                            else "SESSION_SCHEMA_UNRECOGNIZED") from None
    finally:
        connection.close()
    sessions = []
    partial = unmatched > 0 or not linked
    for task_id, title, updated, workspace, archived, session_id, directory in rows:
        if not _valid_id(task_id):
            partial = True
            continue
        cwd = _cwd(workspace)
        matched = session_id == task_id and _same_directory(directory, workspace)
        if not matched:
            partial = True
        sessions.append({"session_id": task_id, "provider": "ZCODE", "title": _title(title),
                         "updated_at": _timestamp(updated), "cwd": cwd,
                         "mapping_status": "MATCHED" if matched else "UNMATCHED",
                         "archived": bool(archived)})
    more = offset + limit < total
    if partial:
        status, reason = ("DEGRADED" if total else "UNKNOWN"), "SESSION_LINK_INCOMPLETE"
    elif total:
        status, reason = "AVAILABLE", "SESSION_STORE_METADATA_AVAILABLE"
    else:
        status, reason = "EMPTY", "SESSION_STORE_EMPTY"
    return status, reason, sessions, more, offset + len(rows) if more and rows else None


def _revision(provider: str, root: Path, database: Path, identity: str,
              limit: int, offset: int, deadline: float) -> str | None:
    """Hash a bounded metadata inventory; uncertainty disables the fast path."""
    digest = hashlib.sha256()
    digest.update(repr((provider, identity, limit, offset)).encode("utf-8"))
    count = 0
    enumerated = 0

    def add(path: Path, *, optional: bool = False) -> os.stat_result | None:
        nonlocal count
        _check_deadline(deadline)
        count += 1
        if count > MAX_ENTRIES:
            raise _StoreFailure("SESSION_INDEX_LIMIT")
        try:
            info = path.lstat()
        except FileNotFoundError:
            if not optional:
                raise _StoreFailure("SESSION_STORE_NOT_PRESENT") from None
            digest.update(repr((str(path), None)).encode("utf-8"))
            return None
        if _is_reparse(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise _StoreFailure("SESSION_STORE_UNSAFE")
        digest.update(repr((str(path), info.st_dev, info.st_ino, info.st_mode,
                            info.st_nlink, info.st_size, info.st_mtime_ns,
                            info.st_ctime_ns)).encode("utf-8"))
        return info

    def tree(base: Path, max_depth: int, files: tuple[str, ...], *, optional: bool = False) -> None:
        nonlocal enumerated
        info = add(base, optional=optional)
        if info is None:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise _StoreFailure("SESSION_STORE_UNSAFE")
        pending = [(base, 0)]
        while pending:
            directory, depth = pending.pop()
            with os.scandir(directory) as stream:
                children = []
                for child in stream:
                    enumerated += 1
                    if enumerated > MAX_ENTRIES:
                        raise _StoreFailure("SESSION_INDEX_LIMIT")
                    children.append(child)
            children.sort(key=lambda child: child.name)
            for child in children:
                _check_deadline(deadline)
                path = Path(child.path)
                if child.is_dir(follow_symlinks=False):
                    if child.name == "subagents":
                        continue
                    if depth >= max_depth:
                        if child.name in {"context.jsonl", "wire.jsonl"} or child.name.endswith(".jsonl"):
                            raise _StoreFailure("SESSION_STORE_UNSAFE")
                        continue
                    add(path)
                    pending.append((path, depth + 1))
                elif any(child.name == name or
                         (name == "rollout-*.jsonl" and child.name.startswith("rollout-") and
                          child.name.endswith(".jsonl")) or
                         (name == "*.jsonl" and child.name.endswith(".jsonl"))
                         for name in files):
                    add(path)

    try:
        add(root)
        if provider == "KIMI_CODE":
            if (root / "session_index.jsonl").exists():
                add(root / "session_index.jsonl")
                tree(root / "sessions", 2, ("context.jsonl", "wire.jsonl", "*.jsonl"))
            else:
                add(root / "kimi.json")
                tree(root / "sessions", 2, ("context.jsonl", "wire.jsonl", "*.jsonl"), optional=True)
        elif provider == "ZCODE":
            for path in _locations(provider, root, database):
                add(path, optional=True)
                add(Path(str(path) + "-wal"), optional=True)
        else:
            add(database)
            add(Path(str(database) + "-wal"), optional=True)
    except (OSError, _StoreFailure):
        return None
    return digest.hexdigest()


def _identity_changed(root: Path, expected: object, provider: str) -> dict | None:
    try:
        actual = _identity(_checked_path(root, directory=True))
    except _StoreFailure:
        actual = None
    if actual != expected:
        return _envelope(provider, reason="SESSION_STORE_IDENTITY_CHANGED", store_identity=actual)
    return None


def read_store(provider: str, path: str | os.PathLike[str], limit: int = 50,
               offset: int = 0, *, known_revision: str | None = None) -> dict:
    """Return bounded metadata, never transcripts, prompts, or provider secrets."""
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE or type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        return _envelope(provider, reason="SESSION_PAGE_INVALID")
    inspection = inspect_store(provider, path)
    if inspection["status"] == "UNKNOWN":
        return _envelope(provider, reason=str(inspection["reason_code"]),
                         store_identity=inspection["identity"])
    root = None
    try:
        _, root, database = _locator(provider, path)
        deadline = time.monotonic() + QUERY_SECONDS
        changed = _identity_changed(root, inspection["identity"], provider)
        if changed is not None:
            return changed
        candidates = None
        if provider == "CODEX":
            before, candidates = _codex_inventory(root, str(inspection["identity"]),
                                                   limit, offset, deadline)
        else:
            before = _revision(provider, root, database, str(inspection["identity"]),
                               limit, offset, deadline)
        if before is not None and isinstance(known_revision, str) and known_revision == before:
            verified = (_codex_inventory(root, str(inspection["identity"]),
                                         limit, offset, deadline)[0] if provider == "CODEX" else
                        _revision(provider, root, database, str(inspection["identity"]),
                                  limit, offset, deadline))
            changed = _identity_changed(root, inspection["identity"], provider)
            if changed is not None:
                return changed
            if verified != before:
                return _envelope(provider, status="DEGRADED", reason="SESSION_STORE_UNSTABLE",
                                 store_identity=inspection["identity"])
            response = _envelope(provider, status="AVAILABLE", reason="SESSION_STORE_NOT_MODIFIED",
                                 store_identity=inspection["identity"])
            response.update(revision=before, not_modified=True)
            if provider == "CODEX":
                response.update(metadata_scope="PAGE", candidate_offset=offset)
            return response
        if provider == "CODEX":
            result = _codex(root, limit, offset, deadline, candidates)
        elif provider == "KIMI_CODE":
            result = _kimi(root, limit, offset, deadline)
        elif provider == "ZCODE":
            result = _zcode(root, limit, offset, deadline)
        else:
            result = _cloudcli(database, limit, offset, deadline)
        status, reason, sessions, has_more, next_offset = result[:5]
        response = _envelope(provider, status=status, reason=reason, sessions=sessions,
                             has_more=has_more, next_offset=next_offset,
                             store_identity=inspection["identity"])
        if provider == "CODEX":
            response.update(result[5])
        if status in {"AVAILABLE", "EMPTY"}:
            after = (_codex_inventory(root, str(inspection["identity"]),
                                      limit, offset, deadline)[0] if provider == "CODEX" else
                     _revision(provider, root, database, str(inspection["identity"]),
                               limit, offset, deadline))
        else:
            after = None
        changed = _identity_changed(root, inspection["identity"], provider)
        if changed is not None:
            return changed
        if status in {"AVAILABLE", "EMPTY"} and (before is None or after is None or before != after):
            response.update(status="DEGRADED", reason_code="SESSION_STORE_UNSTABLE")
        response.update(revision=before if response["status"] in {"AVAILABLE", "EMPTY"}
                        and before is not None and before == after else None)
        return response
    except _StoreFailure as failure:
        if root is not None:
            changed = _identity_changed(root, inspection["identity"], provider)
            if changed is not None:
                return changed
        return _envelope(provider, reason=failure.reason_code,
                         store_identity=inspection["identity"])


__all__ = ["default_stores", "inspect_store", "read_store"]
