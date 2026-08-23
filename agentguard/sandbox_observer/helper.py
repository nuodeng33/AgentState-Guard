"""ASG transient sandbox observer helper (single-file, POSIX, stdlib only).

Runs INSIDE a disposable helper container that shares the target sandbox's
PID namespace (``--pid=container:<id>``), network namespace
(``--network container:<id>``) and volumes (``--volumes-from <id>``). The
target sandbox is never modified: this script only reads /proc, receives
inotify notifications, and writes JSON lines to its own stdout.

What it observes (bounded):
- process start/exit in the shared PID namespace, at poll cadence
  (sub-poll-lifetime processes may be missed — reported honestly);
- file create/modify/rename/delete under configured workspace roots via
  inotify (metadata only, never file contents);
- newly ESTABLISHED remote endpoints in the shared network namespace
  (address/port only; no payloads, no attribution claims).

What it never does: read file contents, read or emit environment
variables, emit full unrestricted argv (only the argv[0] basename and a
token count leave the sandbox), write anywhere, or outlive its budget.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import signal
import socket
import struct
import sys
import time

_IN_CREATE = 0x00000100
_IN_MODIFY = 0x00000002
_IN_MOVED_FROM = 0x00000040
_IN_MOVED_TO = 0x00000080
_IN_DELETE = 0x00000200
_IN_ISDIR = 0x40000000
_EVENT_STRUCT = struct.Struct("iIII")

_stop = False


def _request_stop(_signum, _frame) -> None:
    global _stop
    _stop = True


def emit(kind: str, **fields) -> None:
    payload = {"kind": kind, "ts": round(time.time(), 3)}
    payload.update(fields)
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _bounded_process_fact(pid: str) -> dict | None:
    """Bounded per-process facts from /proc; None when unreadable."""
    base = f"/proc/{pid}"
    try:
        with open(f"{base}/cmdline", "rb") as handle:
            raw = handle.read(4096)
        tokens = [chunk for chunk in raw.split(b"\x00") if chunk]
        argv0 = os.path.basename(tokens[0].decode("utf-8", "replace")) if tokens else ""
        token_count = len(tokens)
    except (OSError, ValueError):
        argv0, token_count = "", 0
    try:
        exe = os.path.basename(os.readlink(f"{base}/exe"))
    except OSError:
        exe = ""
    try:
        with open(f"{base}/stat") as handle:
            parts = handle.read().rsplit(") ", 1)[1].split()
        ppid = int(parts[1])
        start_ticks = int(parts[19])
    except (OSError, ValueError, IndexError):
        return None
    return {
        "pid": int(pid),
        "ppid": ppid,
        "argv0": argv0[:64],
        "token_count": token_count,
        "exe": exe[:64],
        "start_ticks": start_ticks,
    }


def scan_processes(known: dict[int, dict]) -> tuple[list[dict], list[dict], dict[int, dict]]:
    """Diff the shared PID namespace; return (started, exited, seen)."""
    seen: dict[int, dict] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        fact = _bounded_process_fact(entry)
        if fact is None:
            continue
        seen[fact["pid"]] = fact
    started = [fact for pid, fact in seen.items() if pid not in known]
    exited = [{"pid": pid} for pid in known if pid not in seen]
    return started, exited, seen  # type: ignore[return-value]


def established_remotes() -> set[tuple[str, int]]:
    """Remote endpoints currently ESTABLISHED in this network namespace."""
    remotes: set[tuple[str, int]] = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as handle:
                lines = handle.readlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "01":
                continue
            try:
                host_hex, port_hex = fields[2].split(":")
                port = int(port_hex, 16)
            except ValueError:
                continue
            if port == 0:
                continue
            ip = _decode_hex_ip(host_hex)
            if ip is not None:
                remotes.add((ip, port))
    return remotes


def _decode_hex_ip(host_hex: str) -> str | None:
    try:
        raw = bytes.fromhex(host_hex)
    except ValueError:
        return None
    if len(raw) == 4:
        return socket.inet_ntop(socket.AF_INET, raw[::-1])
    if len(raw) == 16:
        return socket.inet_ntop(socket.AF_INET6, bytes(reversed(raw)))
    return None


class WorkspaceWatch:
    """Recursive inotify watch over workspace roots (metadata events only)."""

    def __init__(self, roots: list[str]) -> None:
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        self._libc = ctypes.CDLL(libc_name, use_errno=True)
        self._libc.inotify_init1.argtypes = [ctypes.c_int]
        self._libc.inotify_init1.restype = ctypes.c_int
        self._libc.inotify_add_watch.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        self._libc.inotify_add_watch.restype = ctypes.c_int
        self._fd = self._libc.inotify_init1(os.O_NONBLOCK)
        if self._fd < 0:
            raise OSError("inotify_init1 failed")
        self._wd_to_path: dict[int, str] = {}
        self._roots = roots
        for root in roots:
            self._add_tree(root)

    def _add_tree(self, path: str) -> None:
        if not os.path.isdir(path):
            return
        if not self._add_one(path):
            return
        try:
            entries = os.listdir(path)
        except OSError:
            return
        for name in entries:
            child = os.path.join(path, name)
            if os.path.isdir(child) and not os.path.islink(child):
                self._add_tree(child)

    def _add_one(self, path: str) -> bool:
        mask = (
            _IN_CREATE | _IN_MODIFY | _IN_MOVED_FROM | _IN_MOVED_TO | _IN_DELETE
        )
        wd = self._libc.inotify_add_watch(
            self._fd, path.encode("utf-8", "replace"), mask
        )
        if wd < 0:
            return False
        self._wd_to_path[wd] = path
        return True

    def poll(self) -> list[dict]:
        events: list[dict] = []
        try:
            data = os.read(self._fd, 65536)
        except BlockingIOError:
            return events
        offset = 0
        while offset + _EVENT_STRUCT.size <= len(data):
            wd, mask, _cookie, name_len = _EVENT_STRUCT.unpack_from(data, offset)
            offset += _EVENT_STRUCT.size + name_len
            raw_name = data[offset - name_len : offset]
            name = raw_name.split(b"\x00", 1)[0].decode("utf-8", "replace")[:128]
            root = self._wd_to_path.get(wd)
            if root is None:
                continue
            rel = os.path.relpath(os.path.join(root, name), self._roots[0])
            is_dir = bool(mask & _IN_ISDIR)
            if mask & _IN_CREATE:
                action = "CREATE_DIR" if is_dir else "CREATE"
            elif mask & _IN_MODIFY:
                action = "MODIFY"
            elif mask & _IN_MOVED_TO:
                action = "RENAME_INTO" if not is_dir else "CREATE_DIR"
            elif mask & _IN_MOVED_FROM:
                action = "RENAME_FROM" if not is_dir else "DELETE_DIR"
            elif mask & _IN_DELETE:
                action = "DELETE_DIR" if is_dir else "DELETE"
            else:
                continue
            events.append({"root": root, "relpath": rel[:256], "action": action})
            if is_dir and mask & _IN_CREATE:
                self._add_tree(os.path.join(root, name))
        return events

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", action="append", default=[])
    parser.add_argument("--max-seconds", type=int, default=60)
    parser.add_argument("--poll-ms", type=int, default=120)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    emit(
        "observer_hello",
        workspaces=args.workspace,
        max_seconds=args.max_seconds,
        poll_ms=args.poll_ms,
        uid=os.getuid(),
    )

    known: dict[int, dict] = {}
    started, _exited, known = scan_processes(known)
    for fact in started:
        emit("process_snapshot", **fact)

    watch: WorkspaceWatch | None = None
    if args.workspace:
        try:
            watch = WorkspaceWatch(args.workspace)
        except OSError as exc:
            emit("observer_degraded", reason="INOTIFY_UNAVAILABLE", detail=str(exc)[:80])

    known_remotes: set[tuple[str, int]] = established_remotes()
    deadline = time.monotonic() + args.max_seconds
    while not _stop and time.monotonic() < deadline:
        started, exited, known = scan_processes(known)
        for fact in started:
            emit("process_started", **fact)
        for fact in exited:
            emit("process_exited", **fact)
        if watch is not None:
            for change in watch.poll():
                emit("file_activity", **change)
        remotes = established_remotes()
        for ip, port in sorted(remotes - known_remotes):
            emit("network_established", remote_ip=ip, remote_port=port)
        known_remotes = remotes
        time.sleep(max(args.poll_ms, 20) / 1000.0)

    emit("observer_bye", stopped=_stop)
    if watch is not None:
        watch.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
