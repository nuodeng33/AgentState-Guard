"""Fixed, standalone stdlib Unix Codex canary. No model or caller commands.

Shipped as exact audited source and executed only by the registry's WSL transport.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

SCHEMA = "unix-scope-proof-1"
_DENIED = {errno.EPERM, errno.EACCES, errno.EROFS}
_CHILD = r'''
import errno,json,os,socket,sys
inside,outside,challenge,port,mode=sys.argv[1:]
payload=challenge.encode() if mode=="write" else ("changed-"+challenge).encode()
def write(path,create=False):
    try:
        flags=os.O_WRONLY|(os.O_CREAT|os.O_EXCL if create else os.O_TRUNC)|getattr(os,"O_NOFOLLOW",0)
        fd=os.open(path,flags,0o600)
        try:
            with os.fdopen(fd,"wb") as output: output.write(payload)
        except BaseException:
            raise
        return {"allowed":True}
    except OSError as error: return {"allowed":False,"errno":error.errno}
connection=None
try:
    connection=socket.socket(); connection.settimeout(.5)
    connection.connect(("127.0.0.1",int(port)))
    network={"allowed":True}
except OSError as error: network={"allowed":False,"errno":error.errno}
finally:
    if connection is not None: connection.close()
print(json.dumps({"challenge":challenge,"inside":write(inside),"outside":write(outside),"network":network}))
'''


class ProbeError(RuntimeError):
    def __init__(self, reason_code, *, details=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = details or {}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _profiles(challenge):
    return {"asg_ro_" + challenge: {"extends": ":read-only", "network": {"enabled": False}},
            "asg_write_" + challenge: {"extends": ":read-only", "filesystem": {":workspace_roots": "write"},
                                       "network": {"enabled": False}}}


def _identity(info):
    return {"dev": int(info.st_dev), "ino": int(info.st_ino)}


def _directory(path):
    if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
        raise ProbeError("EXECUTION_SCOPE_ROOT_UNSAFE")
    current = os.path.sep
    for component in path.split(os.path.sep)[1:]:
        if not component or component in {".", ".."}:
            raise ProbeError("EXECUTION_SCOPE_ROOT_UNSAFE")
        current = os.path.join(current, component)
        if not stat.S_ISDIR(os.lstat(current).st_mode):
            raise ProbeError("EXECUTION_SCOPE_ROOT_UNSAFE")
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


_LIMIT_CLEANUP = 256


def _cleanup_generated(directory_fd, device, budget, depth=0):
    """Bounded unlink inside a directory exclusively created for this CLI run.

    Directory descriptors never follow links. Unlinking a generated symlink or
    hardlink removes only the entry in this owned subtree, never its target.
    """
    if depth > 16:
        raise OSError(errno.E2BIG, "owned subtree depth limit")
    for name in os.listdir(directory_fd):
        budget[0] -= 1
        if budget[0] < 0:
            raise OSError(errno.E2BIG, "owned subtree entry limit")
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(before.st_mode):
            if before.st_dev != device:
                raise OSError(errno.EXDEV, "owned subtree crossed device")
            child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                if not os.path.samestat(before, os.fstat(child_fd)):
                    raise OSError(errno.ESTALE, "owned directory changed")
                _cleanup_generated(child_fd, device, budget, depth + 1)
                if not os.path.samestat(before, os.stat(name, dir_fd=directory_fd, follow_symlinks=False)):
                    raise OSError(errno.ESTALE, "owned directory changed")
                os.rmdir(name, dir_fd=directory_fd)
            finally:
                os.close(child_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


class _Scratch:
    """Delete only explicitly created files/directories with their original inode."""
    def __init__(self, parent_path, parent_fd=None, name=None, *, generated=False):
        self.parent = os.dup(parent_fd) if parent_fd is not None else _directory(parent_path)
        self.name = name or ("asg-scope-" + secrets.token_hex(16))
        self.path = os.path.join(parent_path, self.name)
        self.files = {}
        self.children = []
        self.generated = generated
        self.cleanup_failure = None
        self.fd = None
        try:
            os.mkdir(self.name, 0o700, dir_fd=self.parent)
            self.fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            self.identity = _identity(os.fstat(self.fd))
        except BaseException:
            os.close(self.parent)
            raise

    def child(self, name, *, generated=False):
        child = _Scratch(self.path, self.fd, name, generated=generated)
        self.children.append(child)
        return child

    def file(self, name, data):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            self.files[name] = _identity(os.fstat(output.fileno()))
        return os.path.join(self.path, name)

    def read(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or _identity(info) != self.files.get(name)):
                raise ProbeError("EXECUTION_SCOPE_CANARY_INVALID")
            return os.read(fd, 1024)
        finally:
            os.close(fd)

    def cleanup(self):
        clean = True
        try:
            named = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode) or _identity(named) != self.identity
                    or _identity(os.fstat(self.fd)) != self.identity):
                self.cleanup_failure = "directory_identity_changed"
                os.close(self.fd); os.close(self.parent)
                return False
        except OSError:
            self.cleanup_failure = "directory_identity_unavailable"
            os.close(self.fd); os.close(self.parent)
            return False
        for child in reversed(self.children):
            if not child.cleanup():
                clean = False
                self.cleanup_failure = "child:" + (child.cleanup_failure or "unknown")
        for name, expected in self.files.items():
            try:
                info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or _identity(info) != expected:
                    clean = False
                    self.cleanup_failure = "file_identity_changed"
                    continue
                os.unlink(name, dir_fd=self.fd)
            except OSError as error:
                clean = False
                self.cleanup_failure = "file_io_" + str(error.errno)
        if clean and self.generated:
            try:
                _cleanup_generated(self.fd, self.identity['dev'], [_LIMIT_CLEANUP])
            except OSError as error:
                clean = False
                self.cleanup_failure = "generated_io_" + str(error.errno)
        try:
            info = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or _identity(info) != self.identity:
                clean = False
                self.cleanup_failure = "directory_identity_changed"
            elif os.listdir(self.fd):
                clean = False
                self.cleanup_failure = self.cleanup_failure or "untracked_entries"
            else:
                os.rmdir(self.name, dir_fd=self.parent)
        except OSError as error:
            clean = False
            self.cleanup_failure = "directory_io_" + str(error.errno)
        finally:
            os.close(self.fd)
            os.close(self.parent)
        return clean


def _command(argv, environment, timeout=10):
    if not all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "WEXITED", "P_PID")):
        raise ProbeError("EXECUTION_SCOPE_PROCESS_OBSERVATION_UNAVAILABLE")
    process = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    selector = None
    owned_leader = True
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        eof = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("EXECUTION_SCOPE_PROBE_TIMEOUT")
            if eof:
                time.sleep(min(.01, remaining))
            elif selector.select(min(.1, remaining)):
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    eof = True
                    selector.unregister(process.stdout)
                output.extend(chunk)
                if len(output) > 65536:
                    raise ProbeError("EXECUTION_SCOPE_PROBE_OUTPUT_LIMIT")
            try:
                # Observe without reaping: the leader retains its PID/session
                # until its entire newly created process group is cleaned up.
                status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError as error:
                owned_leader = False
                raise ProbeError("EXECUTION_SCOPE_PROCESS_OWNERSHIP_LOST") from error
            if status is not None and eof:
                if status.si_code != os.CLD_EXITED or status.si_status != 0:
                    raise ProbeError("EXECUTION_SCOPE_PROFILE_UNAVAILABLE")
                return output.decode("utf-8")
    finally:
        if selector is not None:
            selector.close()
        try:
            if owned_leader:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # Only now may wait release the PID for reuse. A normal exited
                # leader keeps its original status; SIGKILL only stops survivors.
                process.wait(timeout=2)
        finally:
            process.stdout.close()


def _denied(value):
    return (isinstance(value, dict) and value.get("allowed") is False
            and type(value.get("errno")) is int and value["errno"] in _DENIED)


def probe(request):
    if (set(request) != {"program", "root", "root_identity", "challenge"}
            or not isinstance(request["challenge"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", request["challenge"])):
        raise ProbeError("EXECUTION_SCOPE_REQUEST_INVALID")
    program, root, challenge = request["program"], request["root"], request["challenge"]
    if not isinstance(program, str) or not os.path.isabs(program) or "\x00" in program or not stat.S_ISREG(os.lstat(program).st_mode):
        raise ProbeError("EXECUTION_SCOPE_PROGRAM_UNSAFE")
    root_fd = _directory(root)
    guard_fd = None
    guard_created = False
    guard_identity = None
    inside = outside = None
    listener = None
    report = None
    failure = None
    cleanup = True
    cleanup_details = []
    try:
        if _identity(os.fstat(root_fd)) != request["root_identity"]:
            raise ProbeError("EXECUTION_SCOPE_ROOT_CHANGED")
        try:
            os.mkdir(".agentguard", 0o700, dir_fd=root_fd)
            guard_created = True
        except FileExistsError:
            pass
        guard_fd = os.open(".agentguard", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        info = os.fstat(guard_fd)
        guard_identity = _identity(info)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_dev != os.fstat(root_fd).st_dev:
            raise ProbeError("EXECUTION_SCOPE_PRIVATE_UNSAFE", details={
                "directory_uid": info.st_uid, "effective_uid": os.geteuid(),
                "directory_mode": oct(stat.S_IMODE(info.st_mode)), "directory_dev": info.st_dev,
                "root_dev": os.fstat(root_fd).st_dev})
        inside = _Scratch(os.path.join(root, ".agentguard"), guard_fd)
        outside = _Scratch(tempfile.gettempdir())
        home = outside.child("codex-home", generated=True)
        config = 'default_permissions = "asg_ro_' + challenge + '"\nweb_search = "disabled"\n[analytics]\nenabled = false\n'
        for name, profile in _profiles(challenge).items():
            config += '[permissions.' + name + ']\nextends = ":read-only"\n'
            if "filesystem" in profile:
                config += '[permissions.' + name + '.filesystem]\n":workspace_roots" = "write"\n'
            config += '[permissions.' + name + '.network]\nenabled = false\n'
        home.file("config.toml", config.encode())
        original_outside = ("outside-" + secrets.token_hex(32)).encode()
        outside_path = outside.file("outside-" + challenge, original_outside)
        inside_name = "inside-" + challenge
        inside_path = inside.file(inside_name, ("initial-" + challenge).encode())
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CODEX_HOME": home.path,
                       "HOME": home.path, "LANG": "C.UTF-8", "TMPDIR": home.path,
                       "XDG_CONFIG_HOME": home.path, "XDG_CACHE_HOME": home.path}
        version = _command([program, "--version"], environment, 4).strip()
        if not version.startswith("codex-cli "):
            raise ProbeError("EXECUTION_SCOPE_VERSION_UNVERIFIED")
        version = version.removeprefix("codex-cli ")
        if len(version) > 128 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
            raise ProbeError("EXECUTION_SCOPE_VERSION_UNVERIFIED")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0)); listener.listen(4)
        positive = socket.create_connection(listener.getsockname(), timeout=1)
        positive.close(); accepted, _ = listener.accept(); accepted.close()
        listener.setblocking(False)
        checks = {"network_control": True}
        for mode in ("write", "readonly"):
            profile = ("asg_write_" if mode == "write" else "asg_ro_") + challenge
            output = _command([program, "sandbox", "--permission-profile", profile, "-C", root, "--",
                               sys.executable, "-I", "-B", "-c", _CHILD, inside_path, outside_path,
                               challenge, str(listener.getsockname()[1]), mode], environment)
            result = json.loads(output)
            accepted_connection = False
            try:
                accepted, _ = listener.accept(); accepted.close(); accepted_connection = True
            except BlockingIOError:
                pass
            checks[mode + "_inside"] = (result.get("inside", {}).get("allowed") is True if mode == "write"
                                         else _denied(result.get("inside"))) and inside.read(inside_name) == challenge.encode()
            checks[mode + "_outside"] = _denied(result.get("outside")) and outside.read("outside-" + challenge) == original_outside
            checks[mode + "_network"] = _denied(result.get("network")) and not accepted_connection
            if result.get("challenge") != challenge or not all(checks.values()):
                raise ProbeError("EXECUTION_SCOPE_CANARY_INVALID")
        fresh_fd = _directory(root)
        try:
            if _identity(os.fstat(fresh_fd)) != request["root_identity"]:
                raise ProbeError("EXECUTION_SCOPE_ROOT_CHANGED")
        finally:
            os.close(fresh_fd)
        report = {"schema_version": SCHEMA, "challenge": challenge, "version": version,
                  "root_identity": request["root_identity"], "profile_digest": _digest(_profiles(challenge)),
                  "checks": checks}
    except (OSError, ValueError, ProbeError) as error:
        failure = error
    finally:
        if listener is not None:
            listener.close()
        for area, scratch in (("inside", inside), ("outside", outside)):
            if scratch is not None and not scratch.cleanup():
                cleanup = False
                cleanup_details.append({"area": area, "failure": scratch.cleanup_failure})
        if guard_fd is not None:
            if guard_created:
                try:
                    current = os.stat(".agentguard", dir_fd=root_fd, follow_symlinks=False)
                    if _identity(current) != guard_identity or os.listdir(guard_fd):
                        cleanup = False
                    else:
                        os.rmdir(".agentguard", dir_fd=root_fd)
                except OSError:
                    cleanup = False
            os.close(guard_fd)
        os.close(root_fd)
    if not cleanup:
        raise ProbeError("EXECUTION_SCOPE_CLEANUP_FAILED", details={
            "first_error": (getattr(failure, "reason_code", "EXECUTION_SCOPE_PROBE_FAILED")
                            if failure is not None else None), "cleanup": cleanup_details})
    if failure is not None:
        raise failure
    report["cleanup"] = True
    return report


def main():
    try:
        raw = sys.stdin.buffer.readline(16385)
        if len(raw) > 16384 or not raw.endswith(b"\n"):
            raise ProbeError("EXECUTION_SCOPE_REQUEST_INVALID")
        result = {"ok": True, "result": probe(json.loads(raw))}
    except (OSError, ValueError, KeyError, TypeError, ProbeError) as error:
        result = {"ok": False, "reason_code": getattr(error, "reason_code", "EXECUTION_SCOPE_PROBE_FAILED")}
        if getattr(error, "details", None):
            result["details"] = error.details
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
