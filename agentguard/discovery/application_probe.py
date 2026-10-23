"""Bounded, read-only installed-entry observation using only the standard library."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


def entry_from_argv(executable: str, argv: tuple[str, ...] | list[str], *, cwd: str | None = None) -> dict:
    """Extract only a fixed script entry or Electron role from bounded arguments."""
    result = {"entry_path": None, "electron_role": None}
    if not isinstance(executable, str) or not isinstance(argv, (tuple, list)):
        return result
    arguments = argv[:32]
    if any(not isinstance(item, str) for item in arguments) or sum(
        len(item) for item in arguments
    ) > 8192:
        return result
    basename = executable.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if basename in {"zcode", "zcode.exe"}:
        for argument in arguments[1:]:
            if argument in {"--type=renderer", "--type=utility", "--type=gpu-process", "--type=crashpad-handler"}:
                result["electron_role"] = argument.removeprefix("--type=")
                break
    elif basename in {"node", "node.exe", "python", "python.exe", "python3", "python3.exe"} and len(arguments) > 1:
        candidate = arguments[1]
        # Interpreter entry files need not have an extension (npm bin links
        # commonly do not). This is only a locator, never an agent signature;
        # downstream file/package observation still determines identity.
        if candidate and not candidate.startswith("-") and all(
            ord(char) >= 32 and ord(char) != 127 for char in candidate
        ):
            import ntpath
            import posixpath
            windows = ntpath.isabs(candidate) and (":" in candidate or "\\" in candidate)
            if windows or posixpath.isabs(candidate):
                result["entry_path"] = candidate
            elif cwd and (ntpath.isabs(cwd) or posixpath.isabs(cwd)):
                paths = ntpath if ":" in cwd or "\\" in cwd else posixpath
                result["entry_path"] = paths.normpath(paths.join(cwd, candidate))
    return result


def _ordinary_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _resolve_entry(path: Path) -> Path:
    current = path
    for _ in range(8):
        if not current.is_symlink():
            return current
        target = Path(os.readlink(current))
        current = Path(_ordinary_path(os.path.abspath(
            target if target.is_absolute() else current.parent / target
        )))
    raise RuntimeError("SYMLINK_LIMIT_EXCEEDED")


def _package_metadata(entry: Path) -> tuple[str | None, str | None]:
    for parent in (entry.parent, *tuple(entry.parents)[:6]):
        if parent.name == "node_modules":
            break
        manifest = parent / "package.json"
        try:
            if not manifest.is_file() or manifest.stat().st_size > 65536:
                continue
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(data, dict):
            name = data.get("name")
            version = data.get("version")
            return (
                name if isinstance(name, str) and len(name) <= 256 else None,
                version if isinstance(version, str) and len(version) <= 128 else None,
            )
    return None, None


def probe_entry(path: str | os.PathLike[str]) -> dict:
    """Describe one explicit PATH or user-supplied entry without executing it."""
    canonical = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    result = {
        "path": str(canonical),
        "executable_basename": canonical.name,
        "entry_path": None,
        "package_name": None,
        "version": None,
        "identity": None,
        "status": "UNAVAILABLE",
        "reason_code": "ENTRY_NOT_FOUND",
    }
    try:
        entry = _resolve_entry(canonical)
        if not entry.is_file():
            return result
        info = entry.stat()
        package_name, version = _package_metadata(entry)
    except RuntimeError:
        result["reason_code"] = "SYMLINK_LIMIT_EXCEEDED"
        return result
    except (OSError, ValueError):
        result["reason_code"] = "ENTRY_UNREADABLE"
        return result
    identity_material = "\x1f".join(
        str(item) for item in (
            os.path.normcase(str(entry)), info.st_dev, info.st_ino,
            info.st_size, info.st_mtime_ns,
        )
    )
    result.update(
        entry_path=str(entry),
        package_name=package_name,
        version=version,
        identity="file-stat-sha256:" + hashlib.sha256(identity_material.encode()).hexdigest(),
        status="AVAILABLE",
        reason_code="ENTRY_OBSERVED",
    )
    return result


def inventory(names: tuple[str, ...] | list[str], paths: tuple[str, ...] | list[str] = ()) -> dict:
    """Discover bounded PATH and OS application entries, then classify elsewhere."""
    requested_names = tuple(dict.fromkeys(names))[:100]
    requested_paths = tuple(dict.fromkeys(paths))[:256]
    candidates = [path for path in requested_paths if isinstance(path, str) and path]
    missing = 0
    for name in requested_names:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            missing += 1
            continue
        found = shutil.which(name)
        if found:
            candidates.append(found)
        else:
            missing += 1
    truncated = False
    scanned = 0
    for directory in dict.fromkeys(os.get_exec_path()):
        if scanned >= 2048 or len(candidates) >= 512:
            truncated = True
            break
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > 2048 or len(candidates) >= 512:
                        truncated = True
                        break
                    if os.name == "nt":
                        accepted = entry.name.lower().endswith((".exe", ".cmd", ".bat", ".com"))
                    else:
                        accepted = os.access(entry.path, os.X_OK)
                    if accepted and entry.is_file():
                        candidates.append(entry.path)
        except OSError:
            continue
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths") as key:
                    for index in range(min(winreg.QueryInfoKey(key)[0], 256)):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as application:
                                value = winreg.QueryValueEx(application, "")[0]
                                if isinstance(value, str) and Path(value.strip('"')).is_absolute():
                                    candidates.append(value.strip('"'))
                        except OSError:
                            continue
            except OSError:
                continue
    unique = dict.fromkeys(candidates)
    items = [probe_entry(path) for path in list(unique)[:768]]
    bounded = []
    output_bytes = 0
    for item in items:
        output_bytes += len(json.dumps(item, ensure_ascii=True).encode())
        if output_bytes > 220 * 1024:
            truncated = True
            break
        bounded.append(item)
    items = bounded
    observed = sum(item["status"] == "AVAILABLE" for item in items)
    return {"items": items, "coverage": {"status": "PARTIAL" if truncated or len(unique) > 768 else "AVAILABLE",
        "requested": len(requested_names) + len(requested_paths), "observed": observed,
        "missing": missing, "entries_examined": scanned, "truncated": truncated or len(unique) > 768,
        "methods": ["VISIBLE_PROCESSES", "PATH_ENTRIES", "REGISTERED_PATHS"] + (["WINDOWS_APP_PATHS"] if os.name == "nt" else []),
        "reason_code": "DISCOVERY_INVENTORY_LIMIT" if truncated else "BOUNDED_ENTRY_OBSERVATION"}}
