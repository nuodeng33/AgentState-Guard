"""Fixed, dependency-free workspace protocol for connected POSIX domains.

This file is also executed with ``python3 -I -B -c`` inside a container or WSL.
It accepts one JSON request per line and never executes request-supplied code.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

LIMIT_ENTRIES = 10_000
LIMIT_FILE = 4 * 1024 * 1024
LIMIT_TOTAL = 128 * 1024 * 1024
LIMIT_DEPTH = 64
SESSION_LIMIT = 50
SESSION_METADATA_LIMIT = 16 * 1024
SESSION_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
EXCLUDED_DIRS = frozenset({'.agentguard', '.git', '.gradle', '.hg', '.idea', '.svn', '.venv',
                           '__pycache__', 'build', 'dist', 'node_modules', 'target', 'venv'})
EXCLUDED_SUFFIXES = frozenset({'.class', '.dll', '.dylib', '.exe', '.o', '.obj', '.pyc', '.pyo', '.so'})
SENSITIVE_NAMES = frozenset({'.env', '.npmrc', '.pypirc', 'credentials', 'credentials.json',
                              'id_dsa', 'id_ecdsa', 'id_ed25519', 'id_rsa', 'secrets.json'})
SENSITIVE_SUFFIXES = frozenset({'.key', '.p12', '.pfx', '.pem'})
SENSITIVE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r'(api[_-]?key|apikey)\s*[=:]\s*[\'\"]?(sk-[a-zA-Z0-9]{10,})',
    r'(api[_-]?key|apikey)\s*[=:]\s*[\'\"]?([a-zA-Z0-9_-]{20,})',
    r'(token|secret|password|passwd)\s*[=:]\s*[\'\"]?([a-zA-Z0-9_\-.]{8,})',
    r'(authorization|auth)\s*[=:]\s*[\'\"]?(bearer\s+[a-zA-Z0-9_\-.]+)',
    r'sk-ant-[a-zA-Z0-9]{10,}', r'sk-test-[a-zA-Z0-9_-]{10,}',
    r'(ghp_|gho_|ghu_|ghs_|ghr_)[a-zA-Z0-9]{36,}', r'xox[baprs]-[a-zA-Z0-9-]{10,}',
    r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----',
    r'(api[_-]?key|apikey|apiKey)\s*[=:]\s*[\'\"]?([a-zA-Z0-9_\-.]{10,})',
))


class HelperError(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or (os.name == 'nt' and bool(
        info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def _load_json(path: Path, limit: int = 16 * 1024 * 1024) -> dict:
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_nlink != 1 or info.st_size > limit:
            raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
        raw = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    return result


def _safe_root(raw: object) -> Path:
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        raise HelperError('WORKSPACE_ROOT_INVALID')
    path = Path(raw)
    if not path.is_absolute() or str(path) in {'/', '/home', '/root', '/mnt', '/tmp', '/var', '/usr'}:
        raise HelperError('WORKSPACE_ROOT_UNSUPPORTED')
    if os.name == 'posix' and len(path.parts) > 1 and path.parts[1] in {
        'bin', 'boot', 'dev', 'etc', 'lib', 'lib64', 'proc', 'run', 'sbin', 'sys', 'usr', 'var',
    }:
        raise HelperError('WORKSPACE_ROOT_UNSUPPORTED')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        info = current.lstat()
        if _is_reparse(info):
            raise HelperError('WORKSPACE_ROOT_SYMLINK')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise HelperError('WORKSPACE_ROOT_UNSUPPORTED')
    return path


def _identity(root: Path) -> dict:
    info = root.lstat()
    return {'dev': int(info.st_dev), 'ino': int(info.st_ino)}


def _assert_root_identity(root: Path, identity: dict) -> None:
    try:
        current = _identity(root)
    except OSError:
        raise HelperError('WORKSPACE_ROOT_IDENTITY_CHANGED') from None
    if current != identity:
        raise HelperError('WORKSPACE_ROOT_IDENTITY_CHANGED')


def _verify_root(request: dict) -> Path:
    root = _safe_root(request.get('root'))
    if _identity(root) != request.get('identity'):
        raise HelperError('WORKSPACE_ROOT_IDENTITY_CHANGED')
    return root


@contextmanager
def _pin_directory(root: Path, identity: dict, parts: tuple[str, ...], *, create: bool = False):
    if os.name == 'posix':
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors = []
        try:
            fd = os.open(root, flags)
            descriptors.append(fd)
            info = os.fstat(fd)
            if {'dev': info.st_dev, 'ino': info.st_ino} != identity:
                raise HelperError('WORKSPACE_ROOT_IDENTITY_CHANGED')
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child_fd = os.open(part, flags, dir_fd=fd)
                descriptors.append(child_fd)
                child_info = os.fstat(child_fd)
                if child_info.st_dev != info.st_dev:
                    raise HelperError('WORKSPACE_CROSS_MOUNT_UNSAFE')
                if create and (child_info.st_uid != os.geteuid() or stat.S_IMODE(child_info.st_mode) & 0o077):
                    raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
                fd = child_fd
            yield Path('/proc/self/fd') / str(fd)
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
        return

    # Windows directory handles omit FILE_SHARE_DELETE, preventing a checked
    # ancestor from being renamed while path-based ReplaceFileW runs.
    import ctypes.wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.GetFileInformationByHandle.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]

    class _FileTime(ctypes.Structure):
        _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]

    class _FileInfo(ctypes.Structure):
        _fields_ = [('attributes', ctypes.c_uint32), ('created', _FileTime),
                    ('accessed', _FileTime), ('written', _FileTime),
                    ('volume', ctypes.c_uint32), ('size_high', ctypes.c_uint32),
                    ('size_low', ctypes.c_uint32), ('links', ctypes.c_uint32),
                    ('index_high', ctypes.c_uint32), ('index_low', ctypes.c_uint32)]

    handles = []
    current = Path(root.anchor)
    components = list(root.parts[1:]) + list(parts)
    try:
        for index, part in enumerate([None, *components]):
            if part is not None:
                current = current / part
            if create and index > len(root.parts) - 1:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            handle = kernel.CreateFileW(str(current), 0x80, 0x3, None, 3, 0x02200000, None)
            if handle in (None, ctypes.c_void_p(-1).value):
                if ctypes.get_last_error() in {2, 3}:
                    raise FileNotFoundError(str(current))
                raise HelperError('WORKSPACE_DIRECTORY_PIN_UNAVAILABLE')
            handles.append(handle)
            info = _FileInfo()
            if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise HelperError('WORKSPACE_DIRECTORY_PIN_UNAVAILABLE')
            if info.attributes & 0x400 or not info.attributes & 0x10:
                raise HelperError('WORKSPACE_PARENT_UNSAFE')
            observed = current.lstat()
            file_index = (info.index_high << 32) | info.index_low
            if file_index != observed.st_ino:
                raise HelperError('WORKSPACE_DIRECTORY_IDENTITY_CHANGED')
            if index == len(root.parts) - 1 and {'dev': observed.st_dev, 'ino': observed.st_ino} != identity:
                raise HelperError('WORKSPACE_ROOT_IDENTITY_CHANGED')
        yield current
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


def _proof(path: Path, *, allow_two_links: bool = False) -> dict:
    if os.name == 'nt':
        from agentguard.recovery.workspace_permissions import (
            current_user_permission_backend,
        )
        return current_user_permission_backend().capture(path).to_dict()
    info = path.lstat()
    if not _posix_metadata_supported(path, info, allow_two_links=allow_two_links):
        raise HelperError('WORKSPACE_METADATA_UNSUPPORTED')
    mode = oct(stat.S_IMODE(info.st_mode))
    material = {'kind': 'POSIX_MODE', 'values': {'mode': mode}}
    return {**material, 'digest': _digest(_json_bytes(material))}


def _posix_metadata_supported(path: Path, info: os.stat_result, *, allow_two_links: bool = False) -> bool:
    if os.name != 'posix':
        return True
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid() or info.st_mode & 0o7000
            or info.st_nlink not in ({1, 2} if allow_two_links else {1})):
        return False
    try:
        return not os.listxattr(path, follow_symlinks=False)
    except OSError:
        return False


def _metadata(relative: str, kind: str, category: str, reason: str, size: int, info=None) -> dict:
    material = {'category': category, 'kind': kind, 'mtime_ns': getattr(info, 'st_mtime_ns', None),
                'reason_code': reason, 'relative_path': relative, 'size': size}
    return {'relative_path': relative, 'object_kind': kind, 'category': category,
            'reason_code': reason, 'size': size, 'content_digest': None,
            'observation_digest': _digest(_json_bytes(material)), 'permission_proof': None}


def _scan(root: Path) -> dict:
    entries: list[dict] = []
    total = 0
    complete = True
    reason = 'WORKSPACE_SCAN_COMPLETE'

    def add(entry: dict) -> bool:
        nonlocal complete, reason
        if len(entries) >= LIMIT_ENTRIES:
            complete, reason = False, 'WORKSPACE_SCAN_ENTRY_LIMIT_REACHED'
            return False
        entries.append(entry)
        return True

    def walk(directory: Path, depth: int) -> bool:
        nonlocal complete, reason, total
        if depth > LIMIT_DEPTH:
            complete, reason = False, 'WORKSPACE_SCAN_DEPTH_LIMIT_REACHED'
            return False
        try:
            children = sorted(os.scandir(directory), key=lambda c: (c.name.casefold(), c.name))
        except OSError:
            relative = directory.relative_to(root).as_posix()
            if relative != '.':
                return add(_metadata(relative, 'DIRECTORY', 'unreachable', 'WORKSPACE_DIRECTORY_UNREACHABLE', 0))
            complete, reason = False, 'WORKSPACE_ROOT_UNREACHABLE'
            return False
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            if len(entries) >= LIMIT_ENTRIES:
                complete, reason = False, 'WORKSPACE_SCAN_ENTRY_LIMIT_REACHED'
                return False
            try:
                info = child.stat(follow_symlinks=False)
            except OSError:
                if not add(_metadata(relative, 'SPECIAL', 'unreachable', 'WORKSPACE_OBJECT_UNREACHABLE', 0)):
                    return False
                continue
            if _is_reparse(info):
                entry = _metadata(relative, 'SPECIAL', 'excluded', 'WORKSPACE_REPARSE_POINT_EXCLUDED', 0)
            elif stat.S_ISDIR(info.st_mode):
                if child.name.casefold() in EXCLUDED_DIRS:
                    entry = _metadata(relative, 'DIRECTORY', 'excluded', 'WORKSPACE_DEPENDENCY_EXCLUDED', 0)
                else:
                    if not walk(path, depth + 1):
                        return False
                    continue
            elif not stat.S_ISREG(info.st_mode):
                entry = _metadata(relative, 'SPECIAL', 'excluded', 'WORKSPACE_OBJECT_UNSUPPORTED', info.st_size)
            elif path.suffix.casefold() in EXCLUDED_SUFFIXES:
                entry = _metadata(relative, 'FILE', 'excluded', 'WORKSPACE_GENERATED_OUTPUT_EXCLUDED', info.st_size)
            elif info.st_size > LIMIT_FILE:
                entry = _metadata(relative, 'FILE', 'audit_only', 'WORKSPACE_SIZE_LIMIT_AUDIT_ONLY', info.st_size, info)
            else:
                try:
                    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
                    try:
                        before = os.fstat(fd)
                        content = os.read(fd, LIMIT_FILE + 1)
                        after = os.fstat(fd)
                    finally:
                        os.close(fd)
                    if (not stat.S_ISREG(before.st_mode) or before.st_dev != after.st_dev
                            or before.st_ino != after.st_ino or before.st_size != after.st_size
                            or before.st_mtime_ns != after.st_mtime_ns
                            or (os.name == 'posix' and before.st_ctime_ns != after.st_ctime_ns)
                            or after.st_size != len(content)):
                        raise OSError('changed')
                except OSError:
                    entry = _metadata(relative, 'FILE', 'unreachable', 'WORKSPACE_CONTENT_UNREACHABLE', info.st_size, info)
                else:
                    digest = _digest(content)
                    text = content.decode('utf-8', errors='replace')
                    sensitive = (path.name.casefold() in SENSITIVE_NAMES
                                 or path.suffix.casefold() in SENSITIVE_SUFFIXES
                                 or any(len(m.group(0)) >= 10 for p in SENSITIVE for m in p.finditer(text)))
                    if sensitive or total + len(content) > LIMIT_TOTAL:
                        why = 'WORKSPACE_SENSITIVE_AUDIT_ONLY' if sensitive else 'WORKSPACE_TOTAL_SIZE_LIMIT_AUDIT_ONLY'
                        entry = {'relative_path': relative, 'object_kind': 'FILE', 'category': 'audit_only',
                                 'reason_code': why, 'size': len(content), 'content_digest': digest,
                                 'observation_digest': digest, 'permission_proof': None}
                    elif not _posix_metadata_supported(path, after):
                        entry = {'relative_path': relative, 'object_kind': 'FILE', 'category': 'audit_only',
                                 'reason_code': 'WORKSPACE_METADATA_AUDIT_ONLY', 'size': len(content),
                                 'content_digest': digest, 'observation_digest': digest,
                                 'permission_proof': None}
                    else:
                        current = path.lstat()
                        if (current.st_dev != after.st_dev or current.st_ino != after.st_ino
                                or current.st_size != after.st_size or current.st_mtime_ns != after.st_mtime_ns
                                or (os.name == 'posix' and current.st_ctime_ns != after.st_ctime_ns)):
                            entry = _metadata(relative, 'FILE', 'unreachable', 'WORKSPACE_CONTENT_CHANGED_DURING_SCAN', after.st_size, after)
                        else:
                            total += len(content)
                            entry = {'relative_path': relative, 'object_kind': 'FILE', 'category': 'restorable',
                                     'reason_code': 'WORKSPACE_RESTORABLE', 'size': len(content),
                                     'content_digest': digest, 'observation_digest': digest,
                                     'permission_proof': _proof(path),
                                     'content_b64': base64.b64encode(content).decode('ascii')}
            if not add(entry):
                return False
        return True

    walk(root, 0)
    return {'entries': sorted(entries, key=lambda e: (e['relative_path'].casefold(), e['relative_path'])),
            'complete': complete, 'reason_code': reason}


def _fingerprint(root: Path) -> dict:
    records = []
    root_device = root.lstat().st_dev

    def walk(directory: Path, depth: int) -> None:
        if depth > LIMIT_DEPTH:
            raise HelperError('WORKSPACE_FINGERPRINT_DEPTH_LIMIT')
        for child in sorted(os.scandir(directory), key=lambda item: (item.name.casefold(), item.name)):
            if len(records) >= LIMIT_ENTRIES:
                raise HelperError('WORKSPACE_FINGERPRINT_ENTRY_LIMIT')
            path = Path(child.path)
            info = path.lstat()
            if _is_reparse(info):
                raise HelperError('WORKSPACE_FINGERPRINT_REPARSE_POINT')
            if info.st_dev != root_device:
                raise HelperError('WORKSPACE_FINGERPRINT_CROSS_MOUNT')
            relative = path.relative_to(root).as_posix()
            records.append((relative, stat.S_IFMT(info.st_mode), info.st_ino,
                            info.st_size, info.st_mtime_ns, info.st_mode))
            if stat.S_ISDIR(info.st_mode) and child.name.casefold() not in EXCLUDED_DIRS:
                walk(path, depth + 1)

    walk(root, 0)
    return {'digest': _digest(_json_bytes(records)), 'entry_count': len(records)}


def _session_store(provider: object) -> dict:
    if '_asg_sessions' in globals():
        sessions = globals()['_asg_sessions']
    else:
        from agentguard.discovery import session_metadata as sessions
    candidates = sessions.default_stores()
    candidate = next((item for item in candidates if item['provider'] == provider), None)
    if candidate is None:
        return {'schema_version': 'discovery-sessions-1', 'provider': provider,
                'status': 'UNSUPPORTED', 'reason_code': 'SESSION_PROVIDER_UNSUPPORTED',
                'sessions': [], 'has_more': False}
    return sessions.read_store(provider, candidate['path'])


def _processes() -> dict:
    if '_asg_processes' in globals():
        return globals()['_asg_processes'].collect_processes(globals()['_asg_applications'])
    from agentguard.discovery import application_probe
    from agentguard.discovery.probes.container_processes import collect_processes
    return collect_processes(application_probe)


def _safe_relative(raw: object) -> Path:
    if not isinstance(raw, str) or not raw or '\\' in raw or '\x00' in raw:
        raise HelperError('WORKSPACE_PATH_INVALID')
    value = PurePosixPath(raw)
    if value.is_absolute() or any(p in {'.', '..', ''} for p in raw.split('/')) or '.agentguard' in value.parts:
        raise HelperError('WORKSPACE_PATH_INVALID')
    return Path(*value.parts)


def _target(root: Path, relative: Path, *, allow_two_links: bool = False) -> Path:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise HelperError('WORKSPACE_PARENT_UNSAFE')
    target = current / relative.name
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target
    if (not stat.S_ISREG(info.st_mode) or _is_reparse(info)
            or info.st_nlink not in ({1, 2} if allow_two_links else {1})):
        raise HelperError('WORKSPACE_TARGET_UNSAFE')
    return target


def _validate_entry(entry: object, relative: str) -> bytes | None:
    if entry is None:
        return None
    if not isinstance(entry, dict) or entry.get('relative_path') != relative or entry.get('category') != 'restorable':
        raise HelperError('WORKSPACE_ENTRY_INVALID')
    try:
        content = base64.b64decode(entry['content_b64'], validate=True)
        proof = entry['permission_proof']
        if os.name == 'nt':
            from agentguard.recovery.workspace_permissions import PermissionProof
            PermissionProof.from_dict(proof)
        else:
            mode = proof['values']['mode']
            assert proof['kind'] == 'POSIX_MODE' and re.fullmatch(r'0o[0-7]+', mode)
            assert proof['digest'] == _digest(_json_bytes({'kind': 'POSIX_MODE', 'values': {'mode': mode}}))
    except (KeyError, TypeError, ValueError, AssertionError) as exc:
        raise HelperError('WORKSPACE_ENTRY_INVALID') from exc
    if len(content) > LIMIT_FILE or entry.get('size') != len(content) or entry.get('content_digest') != _digest(content):
        raise HelperError('WORKSPACE_ENTRY_INVALID')
    return content


def _read_live(target: Path, *, allow_two_links: bool = False) -> tuple[dict, bytes] | None:
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or _is_reparse(info)
            or info.st_nlink not in ({1, 2} if allow_two_links else {1}) or info.st_size > LIMIT_FILE):
        raise HelperError('WORKSPACE_TARGET_UNSAFE')
    fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        before = os.fstat(fd)
        content = os.read(fd, LIMIT_FILE + 1)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev != after.st_dev or before.st_ino != after.st_ino
            or before.st_size != after.st_size or len(content) != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or (os.name == 'posix' and before.st_ctime_ns != after.st_ctime_ns)):
        raise HelperError('WORKSPACE_TARGET_CHANGED')
    current = target.lstat()
    if (current.st_dev != after.st_dev or current.st_ino != after.st_ino or current.st_size != after.st_size
            or current.st_mtime_ns != after.st_mtime_ns
            or (os.name == 'posix' and current.st_ctime_ns != after.st_ctime_ns)):
        raise HelperError('WORKSPACE_TARGET_CHANGED')
    facts = {'content_digest': _digest(content), 'size': len(content),
             'permission_proof': _proof(target, allow_two_links=allow_two_links)}
    again = target.lstat()
    if (again.st_dev != after.st_dev or again.st_ino != after.st_ino or again.st_size != after.st_size
            or again.st_mtime_ns != after.st_mtime_ns
            or (os.name == 'posix' and again.st_ctime_ns != after.st_ctime_ns)):
        raise HelperError('WORKSPACE_TARGET_CHANGED')
    return facts, content


def _live(target: Path) -> dict | None:
    observed = _read_live(target)
    return observed[0] if observed else None


def _live_entry(target: Path, relative: str, *, allow_two_links: bool = False) -> dict | None:
    observed = _read_live(target, allow_two_links=allow_two_links)
    if observed is None:
        return None
    facts, content = observed
    return {'relative_path': relative, 'object_kind': 'FILE', 'category': 'restorable',
            'reason_code': 'WORKSPACE_RESTORABLE', 'size': facts['size'],
            'content_digest': facts['content_digest'], 'observation_digest': facts['content_digest'],
            'permission_proof': facts['permission_proof'],
            'content_b64': base64.b64encode(content).decode('ascii')}


def _matches(live: dict | None, entry: dict | None) -> bool:
    if entry is None:
        return live is None
    if not live or live['content_digest'] != entry['content_digest'] or live['size'] != entry['size']:
        return False
    if os.name == 'nt':
        from agentguard.recovery.workspace_permissions import PermissionProof
        return PermissionProof.from_dict(live['permission_proof']).matches(
            PermissionProof.from_dict(entry['permission_proof']))
    return live['permission_proof']['digest'] == entry['permission_proof']['digest']


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(_json_bytes(value))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _sync_dir(path: Path) -> None:
    if os.name != 'posix':
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            raise HelperError('WORKSPACE_DIRECTORY_SYNC_UNAVAILABLE') from exc
    finally:
        os.close(fd)


def _renameat2(first: Path, second: Path, flags: int) -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, 'renameat2', None)
    if renameat2 is None:
        return False
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(first), -100, os.fsencode(second), flags) == 0:
        return True
    if ctypes.get_errno() in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
        return False
    raise HelperError('WORKSPACE_RENAME_FAILED')


def _no_replace(first: Path, second: Path) -> None:
    if not _renameat2(first, second, 1):
        raise HelperError('WORKSPACE_NO_REPLACE_UNAVAILABLE')


def _entry_state(entry: dict | None) -> dict | None:
    return {key: value for key, value in entry.items() if key != 'content_b64'} if entry else None


def _apply(root: Path, request: dict) -> dict:
    relative = _safe_relative(request.get('relative_path'))
    operation_id, step_id = request.get('operation_id'), request.get('step_id')
    if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', v) for v in (operation_id, step_id)):
        raise HelperError('WORKSPACE_OPERATION_INVALID')
    operation_part = _digest(operation_id.encode())[:32]
    with (
        _pin_directory(root, request['identity'], relative.parts[:-1]) as parent,
        _pin_directory(root, request['identity'], ('.agentguard', 'protection', operation_part), create=True) as isolated,
    ):
        return _apply_at(root, request, relative, operation_id, step_id,
                         parent / relative.name, isolated)


def _apply_at(root: Path, request: dict, relative: Path, operation_id: str, step_id: str,
              target: Path, isolated: Path) -> dict:
    expected, desired = request.get('expected'), request.get('target')
    _validate_entry(expected, relative.as_posix())
    content = _validate_entry(desired, relative.as_posix())
    _target(target.parent, Path(relative.name))
    owner = isolated / 'owner.json'
    ownership = {'operation_id': operation_id, 'root_identity': request['identity']}
    if owner.exists() or owner.is_symlink():
        if _is_reparse(owner.lstat()) or _load_json(owner, 4096) != ownership:
            raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    else:
        _atomic_json(owner, ownership)
    stem = _digest(step_id.encode())[:32]
    intent = isolated / (stem + '.intent.json')
    receipt_path = isolated / (stem + '.receipt.json')
    staged = isolated / (stem + '.staged')
    backup = isolated / (stem + '.old')
    request_digest = _digest(_json_bytes({'relative_path': relative.as_posix(), 'expected': expected, 'target': desired}))
    if intent.exists() or intent.is_symlink():
        if _is_reparse(intent.lstat()):
            raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
        previous = _load_json(intent, 8192)
        if previous.get('request_digest') != request_digest:
            raise HelperError('WORKSPACE_STEP_CONFLICT')
        return _reconcile(root, request)
    before = _live_entry(target, relative.as_posix())
    if not _matches(before, expected):
        raise HelperError('WORKSPACE_EXPECTED_MISMATCH')
    if desired is None and expected is None:
        raise HelperError('WORKSPACE_NOOP_UNSUPPORTED')
    if backup.exists() or backup.is_symlink():
        raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    if content is not None:
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        if os.name == 'nt':
            from agentguard.recovery.workspace_permissions import (
                PermissionProof,
                current_user_permission_backend,
            )
            current_user_permission_backend().apply(staged, PermissionProof.from_dict(desired['permission_proof']))
        else:
            mode = int(desired['permission_proof']['values']['mode'], 8)
            os.chmod(staged, mode)
        if not _matches(_live(staged), desired):
            raise HelperError('WORKSPACE_STAGING_MISMATCH')
    record = {'operation_id': operation_id, 'request_digest': request_digest,
                          'relative_path': relative.as_posix(),
                          'expected': _entry_state(expected), 'target': _entry_state(desired),
                          'expected_digest': expected['content_digest'] if expected else None,
                          'target_digest': desired['content_digest'] if desired else None}
    _atomic_json(intent, record)
    if expected is None:
        os.link(staged, target, follow_symlinks=False)
        staged.unlink()
    elif desired is None:
        if os.name == 'posix':
            if not _renameat2(target, backup, 1):
                os.rename(target, backup)
        else:
            os.rename(target, backup)
    elif os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        if not kernel.ReplaceFileW(str(target), str(staged), str(backup), 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        if _renameat2(target, staged, 2):
            _no_replace(staged, backup)
        else:
            if not _renameat2(target, backup, 1):
                # Some bind mounts reject renameat2. Move the object currently
                # at the name into this operation's private, unused slot.
                # A prior read cannot authorize unlinking the name here.
                os.rename(target, backup)
            _sync_dir(target.parent)
            _sync_dir(isolated)
            # A crash after this marker may be indistinguishable from another
            # process deleting the newly published target. Keep that case for
            # attention instead of resurrecting an older version.
            record['phase'] = 'PUBLISHING'
            _atomic_json(intent, record)
            if not _renameat2(staged, target, 1):
                os.link(staged, target, follow_symlinks=False)
                staged.unlink()
    _sync_dir(target.parent)
    _sync_dir(isolated)
    actual_before = _live_entry(backup, relative.as_posix()) if expected is not None else None
    after = _live_entry(target, relative.as_posix())
    verified = _matches(actual_before, expected) and _matches(after, desired)
    receipt = {'state': 'VERIFIED' if verified else 'CONFLICT', 'operation_id': operation_id, 'step_id': step_id,
               'relative_path': relative.as_posix(), 'request_digest': request_digest,
               'target_digest': desired['content_digest'] if desired else None,
               'old_retained': expected is not None, 'old_digest': expected['content_digest'] if expected else None,
               'actual_before': actual_before, 'after': after}
    _atomic_json(receipt_path, receipt)
    return receipt


def _reconcile(root: Path, request: dict) -> dict:
    operation_id, step_id = request.get('operation_id'), request.get('step_id')
    if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', v) for v in (operation_id, step_id)):
        raise HelperError('WORKSPACE_OPERATION_INVALID')
    try:
        with _pin_directory(root, request['identity'],
                            ('.agentguard', 'protection', _digest(operation_id.encode())[:32])) as isolated:
            return _reconcile_at(root, request, operation_id, step_id, isolated)
    except FileNotFoundError:
        _assert_root_identity(root, request['identity'])
        return {'state': 'NOT_APPLIED', 'operation_id': operation_id, 'step_id': step_id,
                'actual_before': None, 'after': None}


def _reconcile_at(root: Path, request: dict, operation_id: str, step_id: str,
                  isolated: Path) -> dict:
    stem = _digest(step_id.encode())[:32]
    intent = isolated / (stem + '.intent.json')
    _operation_files(root, operation_id, isolated=isolated, identity=request['identity'])
    if not intent.exists():
        return {'state': 'NOT_APPLIED', 'operation_id': operation_id, 'step_id': step_id,
                'actual_before': None, 'after': None}
    if _is_reparse(intent.lstat()):
        raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    record = _load_json(intent, 8192)
    relative = _safe_relative(request.get('relative_path', record.get('relative_path')))
    if record.get('relative_path') != relative.as_posix():
        raise HelperError('WORKSPACE_STEP_CONFLICT')
    with _pin_directory(root, request['identity'], relative.parts[:-1]) as parent:
        return _reconcile_target(root, request, operation_id, step_id, isolated,
                                 relative, record, parent / relative.name)


def _reconcile_target(root: Path, request: dict, operation_id: str, step_id: str,
                      isolated: Path, relative: Path, record: dict, target: Path) -> dict:
    stem = _digest(step_id.encode())[:32]
    intent = isolated / (stem + '.intent.json')
    receipt_path = isolated / (stem + '.receipt.json')
    old = isolated / (stem + '.old')
    staged = isolated / (stem + '.staged')
    expected_state = record.get('expected')
    target_state = record.get('target')
    # An exclusive hardlink publication can be interrupted before its staging
    # name is removed. The two links must be exactly the recorded pair.
    if (staged.exists() and target.exists() and staged.stat().st_nlink == 2
            and os.path.samestat(staged.stat(), target.lstat()) and _matches(
                _live_entry(staged, relative.as_posix(), allow_two_links=True), target_state)):
        staged.unlink()
        _sync_dir(isolated)
    if (old.exists() and target.exists() and old.stat().st_nlink == 2
            and record.get('phase') == 'ROLLBACK_LINKED' and os.path.samestat(old.stat(), target.lstat())):
        old.unlink()
        _sync_dir(isolated)
    target = _target(target.parent, Path(relative.name))
    live = _live(target)
    if target_state is not None and old.exists() and staged.exists() and live is None and record.get('phase') != 'PUBLISHING':
        # The source was moved but the replacement was never published. An
        # exclusive link restores exactly that captured object, or leaves an
        # independently created target untouched.
        if record.get('phase') != 'ROLLBACK_LINKED':
            record['phase'] = 'ROLLBACK_LINKED'
            _atomic_json(intent, record)
        try:
            os.link(old, target, follow_symlinks=False)
        except FileExistsError:
            pass
        else:
            _sync_dir(target.parent)
        return _reconcile(root, request)
    if record.get('phase') == 'ROLLBACK_LINKED' and not old.exists() and staged.exists():
        return {'state': 'NOT_APPLIED', 'operation_id': operation_id, 'step_id': step_id,
                'relative_path': relative.as_posix(), 'actual_before': None,
                'after': _live_entry(target, relative.as_posix())}
    retained = old if old.exists() else staged if staged.exists() and _matches(_live(staged), expected_state) else None
    actual_before = _live_entry(retained, relative.as_posix()) if retained else None
    after = _live_entry(target, relative.as_posix())
    if receipt_path.exists():
        if _is_reparse(receipt_path.lstat()):
            raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
        receipt = _load_json(receipt_path)
        receipt['live_matches_target'] = _matches(live, receipt.get('after'))
        old_matches = _matches(actual_before, receipt.get('actual_before'))
        if receipt.get('state') == 'CONFLICT':
            receipt['state'] = 'CONFLICT'
        elif receipt['live_matches_target'] and old_matches:
            receipt['state'] = 'VERIFIED'
        else:
            receipt['state'] = 'NEEDS_ATTENTION'
        return receipt
    if _matches(live, target_state) and _matches(actual_before, expected_state):
        state = 'APPLIED'
    elif actual_before is not None and not _matches(actual_before, expected_state):
        state = 'CONFLICT'
    elif _matches(live, expected_state) and retained is None:
        state = 'NOT_APPLIED'
    else:
        state = 'NEEDS_ATTENTION'
    return {'state': state, 'operation_id': operation_id, 'step_id': step_id,
            'relative_path': relative.as_posix(), 'live_digest': live['content_digest'] if live else None,
            'target_digest': record['target_digest'], 'old_digest': record['expected_digest'],
            'old_retained': retained is not None,
            'actual_before': actual_before, 'after': after}


def _operation_files(root: Path, operation_id: object, *, isolated: Path | None = None,
                     identity: dict | None = None) -> tuple[Path | None, list[Path]]:
    if not isinstance(operation_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', operation_id) is None:
        raise HelperError('WORKSPACE_OPERATION_INVALID')
    pinned = isolated is not None
    if isolated is None:
        isolated = root / '.agentguard' / 'protection' / _digest(operation_id.encode())[:32]
    if not isolated.exists():
        return None, []
    identity = identity or _identity(root)
    if ((not pinned and any(path.is_symlink() for path in
                            (root / '.agentguard', root / '.agentguard' / 'protection', isolated)))
            or isolated.stat().st_dev != identity['dev']):
        raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    owner = isolated / 'owner.json'
    if not owner.is_file() or _is_reparse(owner.lstat()):
        raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    if _load_json(owner, 4096) != {
        'operation_id': operation_id, 'root_identity': identity,
    }:
        raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
    files = []
    allowed = re.compile(r'[0-9a-f]{32}\.(?:intent\.json|receipt\.json|staged|old)(?:\.tmp)?\Z')
    for child in isolated.iterdir():
        if child.name != 'owner.json' and allowed.fullmatch(child.name) is None:
            raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
        info = child.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_dev != identity['dev']:
            raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
        if info.st_nlink == 2 and child.suffix in {'.staged', '.old'}:
            stem = child.name.split('.')[0]
            intent = isolated / (stem + '.intent.json')
            if not intent.is_file() or intent.is_symlink():
                raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
            if _is_reparse(intent.lstat()):
                raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
            record = _load_json(intent, 8192)
            if child.suffix == '.old' and record.get('phase') != 'ROLLBACK_LINKED':
                raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
            relative = _safe_relative(record.get('relative_path'))
            with _pin_directory(root, identity, relative.parts[:-1]) as target_parent:
                target = _target(target_parent, Path(relative.name), allow_two_links=True)
                try:
                    target_info = target.lstat()
                except FileNotFoundError:
                    raise HelperError('WORKSPACE_ISOLATION_UNSAFE') from None
            if target_info.st_nlink != 2 or not os.path.samestat(info, target_info):
                raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
        elif info.st_nlink != 1:
            raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
        files.append(child)
    return isolated, files


def _operation_dir(root: Path, operation_id: object) -> tuple[str, ...]:
    if not isinstance(operation_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', operation_id) is None:
        raise HelperError('WORKSPACE_OPERATION_INVALID')
    return ('.agentguard', 'protection', _digest(operation_id.encode())[:32])


def _operation_storage(root: Path, operation_id: object, identity: dict) -> dict:
    try:
        with _pin_directory(root, identity, _operation_dir(root, operation_id)) as isolated:
            _, files = _operation_files(root, operation_id, isolated=isolated, identity=identity)
            return {'bytes': sum(path.lstat().st_size for path in files)}
    except FileNotFoundError:
        _assert_root_identity(root, identity)
        return {'bytes': 0}


def _cleanup_operation(root: Path, operation_id: object, identity: dict) -> dict:
    parts = _operation_dir(root, operation_id)
    try:
        with _pin_directory(root, identity, parts) as isolated:
            _, files = _operation_files(root, operation_id, isolated=isolated, identity=identity)
            total = sum(path.lstat().st_size for path in files)
            for path in files:
                if path.is_symlink() or not path.is_file():
                    raise HelperError('WORKSPACE_ISOLATION_UNSAFE')
                path.unlink()
            if os.name == 'posix':
                os.rmdir(root.joinpath(*parts))
                _sync_dir(root.joinpath(*parts[:-1]))
        if os.name == 'nt':
            original = root.joinpath(*parts)
            if _identity(root) != identity:
                raise HelperError('WORKSPACE_ROOT_IDENTITY_CHANGED')
            original.rmdir()
        return {'removed': True, 'bytes': total}
    except FileNotFoundError:
        _assert_root_identity(root, identity)
        return {'removed': False, 'bytes': 0}


def handle(request: dict) -> dict:
    action = request.get('action')
    if action in {'application_inventory', 'application_entry', 'session_stores',
                  'inspect_session_store', 'read_session_store'}:
        return _discovery_query(request)
    if action == 'ping':
        return {'pid': os.getpid(), 'platform': os.name}
    if action == 'processes':
        return _processes()
    if action == 'sessions':
        return _session_store(request.get('provider'))
    if action == 'register':
        root = _safe_root(request.get('root'))
        return {'root': str(root), 'identity': _identity(root)}
    root = _verify_root(request)
    if action == 'scan':
        return _scan(root)
    if action == 'fingerprint':
        return _fingerprint(root)
    if action == 'available_bytes':
        return {'bytes': shutil.disk_usage(root).free}
    if action == 'apply':
        return _apply(root, request)
    if action == 'reconcile':
        return _reconcile(root, request)
    if action == 'operation_storage':
        return _operation_storage(root, request.get('operation_id'), request['identity'])
    if action == 'cleanup_operation':
        return _cleanup_operation(root, request.get('operation_id'), request['identity'])
    raise HelperError('WORKSPACE_ACTION_UNSUPPORTED')


def _discovery_query(request: dict) -> dict:
    if '_asg_applications' in globals():
        applications, sessions = globals()['_asg_applications'], globals()['_asg_sessions']
    else:
        from agentguard.discovery import application_probe as applications
        from agentguard.discovery import session_metadata as sessions
    action = request['action']
    if action == 'application_inventory':
        names = request.get('names', ())
        return applications.inventory(names, paths=request.get('paths', ()))
    if action == 'application_entry':
        return applications.probe_entry(request.get('path'))
    if action == 'session_stores':
        coverage = {}
        candidates = sessions.default_stores(process_hints=request.get('process_hints', ()), coverage=coverage)
        return {'items': [{**candidate, **sessions.inspect_store(candidate['provider'], candidate['path'])}
                          for candidate in candidates], 'coverage': coverage}
    if action == 'inspect_session_store':
        return sessions.inspect_store(request.get('provider'), request.get('path'))
    return sessions.read_store(request.get('provider'), request.get('path'),
                               limit=request.get('limit', 50), offset=request.get('offset', 0),
                               known_revision=request.get('known_revision'))


def main() -> None:
    for line in sys.stdin:
        try:
            result = {'ok': True, 'result': handle(json.loads(line))}
        except HelperError as exc:
            result = {'ok': False, 'reason_code': exc.reason_code}
        except OSError as exc:
            result = {'ok': False, 'reason_code': 'WORKSPACE_FILESYSTEM_ERROR_' + str(exc.errno or 0)}
        except (ValueError, KeyError, TypeError):
            result = {'ok': False, 'reason_code': 'WORKSPACE_HELPER_FAILED'}
        sys.stdout.write(json.dumps(result, separators=(',', ':'), ensure_ascii=True) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
