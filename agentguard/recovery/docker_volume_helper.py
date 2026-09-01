"""Standalone Linux helper used by the bundled named-volume OCI artifact."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("PATH")
    return value


def _inside(root: Path, path: Path) -> bool:
    root_text = os.path.realpath(root)
    path_text = os.path.realpath(path)
    return path_text == root_text or path_text.startswith(root_text + os.sep)


def _safe_link(root: Path, link: Path, target: str) -> bool:
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        return False
    return _inside(root, link.parent / Path(*target_path.parts))


def _metadata_digest(
    relative: str,
    kind: str,
    size: int,
    mode: str,
    uid: int,
    gid: int,
    content_digest: str | None,
    link_target: str | None,
) -> str:
    value = [relative, kind, size, mode, uid, gid, content_digest, link_target]
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def scan_root(root: Path, limits: dict[str, Any]) -> dict[str, Any]:
    """Scan one bounded root without following links or crossing into other roots."""
    root = Path(os.path.realpath(root))
    if not root.is_dir() or root.is_symlink():
        raise ValueError("ROOT")
    output: list[dict[str, Any]] = []
    complete = True
    reason = "WORKSPACE_SCAN_COMPLETE"
    captured_total = 0
    max_entries = int(limits["max_entries"])
    max_file = int(limits["max_file_bytes"])
    max_total = int(limits["max_total_restorable_bytes"])
    max_depth = int(limits["max_depth"])
    excluded_dirs = {str(value).casefold() for value in limits["excluded_directories"]}
    excluded_suffixes = tuple(str(value).lower() for value in limits["excluded_suffixes"])

    def add(item: dict[str, Any]) -> bool:
        nonlocal complete, reason
        if len(output) >= max_entries:
            complete = False
            reason = "WORKSPACE_SCAN_ENTRY_LIMIT_REACHED"
            return False
        output.append(item)
        return True

    def base_item(
        relative: str,
        kind: str,
        category: str,
        reason_code: str,
        info: os.stat_result,
        *,
        content_digest: str | None = None,
        link_target: str | None = None,
    ) -> dict[str, Any]:
        mode = oct(stat.S_IMODE(info.st_mode))
        size = int(info.st_size) if kind in {"FILE", "SYMLINK"} else 0
        return {
            "relative_path": relative,
            "object_kind": kind,
            "category": category,
            "reason_code": reason_code,
            "size": size,
            "mode": mode,
            "uid": int(info.st_uid),
            "gid": int(info.st_gid),
            "digest": _metadata_digest(
                relative,
                kind,
                size,
                mode,
                int(info.st_uid),
                int(info.st_gid),
                content_digest,
                link_target,
            ),
            "content_digest": content_digest,
            "link_target": link_target,
        }

    def walk(directory: Path, depth: int) -> bool:
        nonlocal complete, reason, captured_total
        if depth > max_depth:
            complete = False
            reason = "WORKSPACE_SCAN_DEPTH_LIMIT_REACHED"
            return False
        try:
            names = sorted(os.listdir(directory), key=lambda value: (value.casefold(), value))
        except OSError:
            complete = False
            reason = "WORKSPACE_SCAN_UNREACHABLE"
            return False
        if len({name.casefold() for name in names}) != len(names):
            complete = False
            reason = "WORKSPACE_PATH_AMBIGUOUS"
            return False
        for name in names:
            full = directory / name
            relative = full.relative_to(root).as_posix()
            try:
                info = full.lstat()
            except OSError:
                placeholder = os.stat_result((0,) * 10)
                if not add(
                    base_item(
                        relative,
                        "SPECIAL",
                        "unreachable",
                        "WORKSPACE_OBJECT_UNREACHABLE",
                        placeholder,
                    )
                ):
                    return False
                continue
            if stat.S_ISLNK(info.st_mode):
                try:
                    target = os.readlink(full)
                except OSError:
                    target = ""
                if target and _safe_link(root, full, target):
                    item = base_item(
                        relative,
                        "SYMLINK",
                        "candidate",
                        "WORKSPACE_SYMLINK_CAPTURED",
                        info,
                        link_target=target,
                    )
                else:
                    item = base_item(
                        relative,
                        "SYMLINK",
                        "excluded",
                        "WORKSPACE_SYMLINK_ESCAPE_EXCLUDED",
                        info,
                        link_target=target or None,
                    )
                if not add(item):
                    return False
            elif stat.S_ISDIR(info.st_mode):
                if name.casefold() in excluded_dirs:
                    if not add(
                        base_item(
                            relative,
                            "DIRECTORY",
                            "excluded",
                            "WORKSPACE_DEPENDENCY_EXCLUDED",
                            info,
                        )
                    ):
                        return False
                else:
                    if not add(
                        base_item(
                            relative,
                            "DIRECTORY",
                            "candidate",
                            "WORKSPACE_DIRECTORY_CAPTURED",
                            info,
                        )
                    ):
                        return False
                    if not walk(full, depth + 1):
                        return False
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                why = (
                    "WORKSPACE_HARDLINK_EXCLUDED"
                    if stat.S_ISREG(info.st_mode)
                    else "WORKSPACE_OBJECT_UNSUPPORTED"
                )
                if not add(base_item(relative, "SPECIAL", "excluded", why, info)):
                    return False
            else:
                digest = hashlib.sha256()
                chunks: list[bytes] = []
                try:
                    descriptor = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    before = os.fstat(descriptor)
                    with os.fdopen(descriptor, "rb") as stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                            if before.st_size <= max_file:
                                chunks.append(chunk)
                    after = full.lstat()
                    if (before.st_ino, before.st_size) != (after.st_ino, after.st_size):
                        raise OSError("RACE")
                except OSError:
                    if not add(
                        base_item(
                            relative,
                            "FILE",
                            "unreachable",
                            "WORKSPACE_CONTENT_UNREACHABLE",
                            info,
                        )
                    ):
                        return False
                    continue
                content_digest = digest.hexdigest()
                if name.lower().endswith(excluded_suffixes):
                    category, why = "excluded", "WORKSPACE_GENERATED_EXCLUDED"
                elif info.st_size > max_file:
                    category, why = "audit_only", "WORKSPACE_FILE_SIZE_LIMIT_AUDIT_ONLY"
                elif captured_total + info.st_size > max_total:
                    category, why = "audit_only", "WORKSPACE_TOTAL_SIZE_LIMIT_AUDIT_ONLY"
                else:
                    category, why = "candidate", "WORKSPACE_CONTENT_CAPTURED"
                item = base_item(
                    relative,
                    "FILE",
                    category,
                    why,
                    info,
                    content_digest=content_digest,
                )
                if category == "candidate":
                    item["content"] = base64.b64encode(b"".join(chunks)).decode("ascii")
                    captured_total += int(info.st_size)
                if not add(item):
                    return False
        return True

    walk(root, 0)
    return {"entries": output, "complete": complete, "reason_code": reason}


def _proof(entry: dict[str, Any]) -> tuple[int, int, int]:
    proof = entry.get("permission_proof")
    if not isinstance(proof, dict) or proof.get("kind") != "POSIX_MODE_OWNER":
        raise ValueError("PROOF")
    values = proof.get("values")
    if not isinstance(values, dict):
        raise TypeError("PROOF")
    return int(str(values["mode"]), 8), int(values["uid"]), int(values["gid"])


def _remove(root: Path, relative: str) -> None:
    target = root / Path(*PurePosixPath(_safe_relative(relative)).parts)
    if not _inside(root, target.parent):
        raise ValueError("PATH")
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        target.rmdir()
    else:
        target.unlink()


def _apply_metadata(path: Path, entry: dict[str, Any]) -> None:
    mode, uid, gid = _proof(entry)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        os.chown(path, uid, gid, follow_symlinks=False)
        if stat.S_IMODE(path.lstat().st_mode) != mode:
            raise OSError("SYMLINK_MODE_UNSUPPORTED")
    else:
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, mode, follow_symlinks=False)


def materialize(
    root: Path,
    artifact: dict[str, Any],
    *,
    restore_paths: tuple[str, ...],
    remove_paths: tuple[str, ...],
) -> int:
    """Apply an already-authorized exact plan without traversing outside root."""
    root = Path(os.path.realpath(root))
    coverage = {
        item["relative_path"]: item
        for item in artifact["workspace"]["coverage"]
        if item.get("category") == "restorable"
    }
    blobs = artifact.get("blobs", {})
    for relative in remove_paths:
        _remove(root, relative)

    selected = [coverage[path] for path in restore_paths]
    directories = sorted(
        (item for item in selected if item["object_kind"] == "DIRECTORY"),
        key=lambda item: len(PurePosixPath(item["relative_path"]).parts),
    )
    others = sorted(
        (item for item in selected if item["object_kind"] != "DIRECTORY"),
        key=lambda item: item["relative_path"],
    )
    for item in directories:
        target = root / Path(*PurePosixPath(_safe_relative(item["relative_path"])).parts)
        if not _inside(root, target.parent):
            raise ValueError("PATH")
        target.mkdir(parents=True, exist_ok=True)
    for item in others:
        relative = _safe_relative(item["relative_path"])
        target = root / Path(*PurePosixPath(relative).parts)
        if not _inside(root, target.parent):
            raise ValueError("PATH")
        target.parent.mkdir(parents=True, exist_ok=True)
        kind = item["object_kind"]
        if kind == "FILE":
            digest = item["content_digest"]
            content = bytes.fromhex(blobs[digest])
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("BLOB")
            descriptor, temporary = tempfile.mkstemp(prefix=".asg-restore-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        elif kind == "SYMLINK":
            link_target = item.get("link_target")
            if not isinstance(link_target, str) or not _safe_link(root, target, link_target):
                raise ValueError("LINK")
            os.symlink(link_target, target)
        else:
            raise ValueError("KIND")
        _apply_metadata(target, item)
    for item in reversed(directories):
        target = root / Path(*PurePosixPath(item["relative_path"]).parts)
        _apply_metadata(target, item)
    return len(selected)


def verify_materialized(root: Path, artifact: dict[str, Any]) -> bool:
    expected = {
        item["relative_path"]: item
        for item in artifact["workspace"]["coverage"]
        if item.get("category") == "restorable"
    }
    observed: set[str] = set()
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in names + files:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            observed.add(relative)
            if path.is_symlink() and name in names:
                names.remove(name)
    if observed != set(expected):
        return False
    for relative, item in expected.items():
        path = root / Path(*PurePosixPath(relative).parts)
        info = path.lstat()
        expected_kind = item["object_kind"]
        actual_kind = (
            "SYMLINK"
            if stat.S_ISLNK(info.st_mode)
            else "DIRECTORY"
            if stat.S_ISDIR(info.st_mode)
            else "FILE"
            if stat.S_ISREG(info.st_mode)
            else "SPECIAL"
        )
        if actual_kind != expected_kind:
            return False
        mode, uid, gid = _proof(item)
        if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (mode, uid, gid):
            return False
        if expected_kind == "FILE":
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["content_digest"]:
                return False
        elif expected_kind == "SYMLINK" and os.readlink(path) != item["link_target"]:
            return False
    return True


def _safe_root(logical_root: object) -> Path:
    if not isinstance(logical_root, str):
        raise TypeError("ROOT")
    logical = PurePosixPath(logical_root)
    if not logical.is_absolute() or ".." in logical.parts:
        raise ValueError("ROOT")
    root = Path(os.path.realpath(Path("/asg-volume", *logical.parts[1:])))
    base = Path(os.path.realpath("/asg-volume"))
    if not _inside(base, root) or root.is_symlink() or not root.is_dir():
        raise ValueError("ROOT")
    return root


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--probe":
        print("ASG_VOLUME_HELPER_READY")
        return 0
    stage = "REQUEST"
    try:
        request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        operation = request["operation"]
        if operation == "probe":
            result = {"status": "OK", "reason_code": "ASG_VOLUME_HELPER_READY"}
        elif operation == "scan":
            stage = "SCAN"
            result = {"status": "OK", **scan_root(_safe_root(request["logical_root"]), request["limits"])}
        elif operation == "test_restore":
            stage = "TEST_MATERIALIZE"
            artifact = request["artifact"]
            paths = tuple(
                item["relative_path"]
                for item in artifact["workspace"]["coverage"]
                if item.get("category") == "restorable"
            )
            with tempfile.TemporaryDirectory(prefix="asg-test-restore-") as temporary:
                target = Path(temporary)
                count = materialize(target, artifact, restore_paths=paths, remove_paths=())
                stage = "TEST_CONVERGENCE"
                if not verify_materialized(target, artifact):
                    raise ValueError("CONVERGENCE")
            result = {"status": "OK", "reason_code": "TEST_RESTORE_VERIFIED", "verified_targets": count}
        elif operation == "restore":
            stage = "RESTORE_MATERIALIZE"
            count = materialize(
                _safe_root(request["logical_root"]),
                request["artifact"],
                restore_paths=tuple(request["restore_paths"]),
                remove_paths=tuple(request["remove_paths"]),
            )
            result = {"status": "OK", "reason_code": "WORKSPACE_RESTORED", "verified_targets": count}
        else:
            raise ValueError("OPERATION")
    except Exception as exc:  # noqa: BLE001 - bounded helper protocol boundary.
        result = {
            "status": "ERROR",
            "reason_code": "DOCKER_VOLUME_HELPER_OPERATION_FAILED",
            "verified_targets": 0,
            "failure_stage": stage,
            "failure_class": type(exc).__name__,
        }
    print(json.dumps(result, separators=(",", ":")))
    # A bounded JSON error is valid helper transport output. Non-zero exit is
    # reserved for failure before the helper can produce its protocol result.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
