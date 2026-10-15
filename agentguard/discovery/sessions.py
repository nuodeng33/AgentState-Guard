"""Bounded provider-store session queries without Agent execution authority."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentguard.core.sanitizer import contains_sensitive_data, sanitize_text

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_LOCAL_NPIPE = re.compile(r"npipe:////\./pipe/[A-Za-z0-9_.-]{1,128}\Z", re.IGNORECASE)
_SESSION_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_VERSION = re.compile(r"[A-Za-z0-9_.-]{1,32}\Z")
_PRIVATE_TITLE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|(?:^|\s)/\S|(?:https?|wss?)://|\b[^\s@]+@[^\s@]+\.[^\s@]+)",
    re.IGNORECASE,
)
_MAX_OUTPUT_BYTES = 256 * 1024
_MAX_SESSIONS = 50
_TIMEOUT_SECONDS = 8.0
_AUTH_ERROR_CODES = frozenset({
    "401", "403", "UNAUTHORIZED", "FORBIDDEN", "UNAUTHENTICATED", "AUTH_REQUIRED",
    "AUTHENTICATION_REQUIRED", "PERMISSION_DENIED",
})


class _QueryFailure(Exception):
    def __init__(self, status: str, reason_code: str) -> None:
        self.status = status
        self.reason_code = reason_code


class _JsonLineChannel:
    def __init__(self, process: Any, *, deadline: float, max_output_bytes: int) -> None:
        self.process = process
        self.deadline = deadline
        self.max_output_bytes = max_output_bytes
        self.events: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
        self.pending = bytearray()
        self.total_bytes = 0
        self.lock = threading.Lock()
        self.threads = [
            threading.Thread(target=self._pump, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=self._pump, args=("stderr", process.stderr), daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def _pump(self, source: str, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    self.events.put((f"{source}_eof", None))
                    return
                if not isinstance(chunk, bytes):
                    self.events.put(("io_error", None))
                    return
                with self.lock:
                    self.total_bytes += len(chunk)
                    exceeded = self.total_bytes > self.max_output_bytes
                if exceeded:
                    self.events.put(("output_limited", None))
                    return
                if source == "stdout":
                    self.events.put(("stdout", chunk))
        except (OSError, ValueError):
            self.events.put(("io_error", None))

    def send(self, message: Mapping[str, Any]) -> None:
        if time.monotonic() >= self.deadline:
            raise _QueryFailure("UNREACHABLE", "SESSION_QUERY_TIMEOUT")
        try:
            line = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AttributeError):
            raise _QueryFailure("UNREACHABLE", "SESSION_PROTOCOL_IO_FAILED") from None

    def response(self, expected_id: int, *, jsonrpc: bool) -> Mapping[str, Any]:
        while True:
            line = self._next_line()
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _QueryFailure("ERROR", "SESSION_PROTOCOL_INVALID") from None
            if not isinstance(message, dict) or (jsonrpc and message.get("jsonrpc") != "2.0"):
                raise _QueryFailure("ERROR", "SESSION_PROTOCOL_INVALID")
            if "method" in message:
                if "id" in message:
                    raise _QueryFailure("UNSUPPORTED", "SESSION_SERVER_REQUEST_UNSUPPORTED")
                continue
            if type(message.get("id")) is not int or message["id"] != expected_id:
                raise _QueryFailure("ERROR", "SESSION_RESPONSE_ID_INVALID")
            if "error" in message:
                error = message["error"]
                if not isinstance(error, dict):
                    raise _QueryFailure("ERROR", "SESSION_PROTOCOL_INVALID")
                code = error.get("code")
                if (type(code) is int and code in {401, 403}) or (
                    isinstance(code, str) and code.upper() in _AUTH_ERROR_CODES
                ):
                    raise _QueryFailure("AUTH_REQUIRED", "SESSION_AUTH_REQUIRED")
                if type(code) is int and code == -32601:
                    reason = (
                        "SESSION_INITIALIZE_UNSUPPORTED"
                        if expected_id == 1 else "SESSION_LIST_UNSUPPORTED"
                    )
                    raise _QueryFailure("UNSUPPORTED", reason)
                reason = (
                    "SESSION_INITIALIZE_REJECTED"
                    if expected_id == 1 else "SESSION_LIST_FAILED"
                )
                raise _QueryFailure("ERROR", reason)
            result = message.get("result")
            if not isinstance(result, dict):
                raise _QueryFailure("ERROR", "SESSION_PROTOCOL_INVALID")
            return result

    def _next_line(self) -> bytes:
        while True:
            boundary = self.pending.find(b"\n")
            if boundary >= 0:
                line = bytes(self.pending[:boundary]).strip()
                del self.pending[: boundary + 1]
                if line:
                    return line
                continue
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise _QueryFailure("UNREACHABLE", "SESSION_QUERY_TIMEOUT")
            try:
                kind, chunk = self.events.get(timeout=remaining)
            except queue.Empty:
                raise _QueryFailure("UNREACHABLE", "SESSION_QUERY_TIMEOUT") from None
            if kind == "stdout":
                assert chunk is not None
                self.pending.extend(chunk)
            elif kind == "output_limited":
                raise _QueryFailure("ERROR", "SESSION_OUTPUT_LIMITED")
            elif kind == "stdout_eof":
                raise _QueryFailure("UNREACHABLE", "SESSION_PROTOCOL_EOF")
            elif kind == "io_error":
                raise _QueryFailure("UNREACHABLE", "SESSION_PROTOCOL_IO_FAILED")


def _safe_title(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if contains_sensitive_data(value) or _PRIVATE_TITLE.search(value):
        return "[REDACTED]"
    clean = " ".join(sanitize_text(value).split())
    if not clean or any(ord(character) < 32 for character in clean):
        return None
    return clean[:120]


def _safe_updated_at(value: object) -> str | None:
    try:
        if isinstance(value, str) and 0 < len(value) <= 64:
            parsed = datetime.fromisoformat(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, tz=UTC)
        else:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC).isoformat()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _safe_cwd(value: object) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= 4096:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if value.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', value):
        return value
    return None


def _version(provider: str, result: Mapping[str, Any]) -> str | None:
    info = result.get("serverInfo" if provider == "CODEX" else "agentInfo")
    value = info.get("version") if isinstance(info, dict) else None
    text = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    return text if _VERSION.fullmatch(text) else None


def _protocol_version(result: Mapping[str, Any]) -> int | None:
    value = result.get("protocolVersion")
    return value if type(value) is int and 0 < value <= 65535 else None


def _sessions(provider: str, result: Mapping[str, Any]) -> tuple[list[dict[str, str | None]], bool]:
    raw = result.get("data" if provider == "CODEX" else "sessions")
    if not isinstance(raw, list):
        raise _QueryFailure("ERROR", "SESSION_LIST_INVALID")
    selected: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in raw[:_MAX_SESSIONS]:
        if not isinstance(item, dict):
            raise _QueryFailure("ERROR", "SESSION_LIST_INVALID")
        session_id = item.get("id" if provider == "CODEX" else "sessionId")
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise _QueryFailure("ERROR", "SESSION_LIST_INVALID")
        if session_id in seen:
            raise _QueryFailure("ERROR", "SESSION_LIST_INVALID")
        seen.add(session_id)
        selected.append(
            {
                "session_id": session_id,
                "title": _safe_title(item.get("title")),
                "updated_at": _safe_updated_at(item.get("updatedAt")),
                "cwd": _safe_cwd(item.get("cwd")),
            }
        )
    if isinstance(result.get("hasMore"), bool):
        has_more = result["hasMore"] or len(raw) > _MAX_SESSIONS
    elif "nextCursor" in result:
        has_more = result["nextCursor"] is not None or len(raw) > _MAX_SESSIONS
    else:
        has_more = len(raw) >= _MAX_SESSIONS
    return selected, has_more


def _close_process(process: Any, channel: _JsonLineChannel | None) -> None:
    try:
        process.stdin.close()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            process.terminate()
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass
    except OSError:
        pass
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except (OSError, ValueError, AttributeError):
            pass
    if channel is not None:
        for thread in channel.threads:
            thread.join(timeout=0.1)


class SessionReader:
    """Query an allowlisted local protocol and discard its temporary broker."""

    def __init__(
        self,
        *,
        process_factory: Callable[..., Any] | None = None,
        which: Callable[[str], str | None] | None = None,
        timeout_seconds: float = _TIMEOUT_SECONDS,
        max_output_bytes: int = _MAX_OUTPUT_BYTES,
    ) -> None:
        if not 0 < timeout_seconds <= _TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds is outside the supported range")
        if not 0 < max_output_bytes <= _MAX_OUTPUT_BYTES:
            raise ValueError("max_output_bytes is outside the supported range")
        self._process_factory = process_factory or subprocess.Popen
        self._which = which or shutil.which
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    def read(
        self,
        provider: str,
        *,
        container_id: str | None = None,
        docker_executable: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized = provider if provider in {"CODEX", "KIMI_CODE"} else "UNKNOWN"
        base: dict[str, Any] = {
            "schema_version": "discovery-sessions-1",
            "status": "UNSUPPORTED",
            "reason_code": "SESSION_SOURCE_UNSUPPORTED",
            "provider": normalized,
            "scope": "PROVIDER_STORE",
            "identity_basis": None,
            "version": None,
            "protocol_version": None,
            "sessions": [],
            "has_more": False,
        }
        if normalized == "UNKNOWN":
            return base
        if normalized == "CODEX":
            if container_id is not None or docker_executable is not None or environment is not None:
                return base
            executable = self._which("codex")
            if not isinstance(executable, str) or not os.path.isabs(executable):
                return {**base, "status": "NOT_PRESENT", "reason_code": "SESSION_CLI_NOT_PRESENT"}
            if Path(executable).name.casefold() not in {
                "codex", "codex.exe", "codex.cmd", "codex.bat"
            }:
                return base
            argv = [executable, "app-server", "--listen", "stdio://"]
        else:
            if (
                not isinstance(container_id, str)
                or not _CONTAINER_ID.fullmatch(container_id)
                or not isinstance(docker_executable, str)
                or not os.path.isabs(docker_executable)
                or Path(docker_executable).name.casefold() not in {"docker", "docker.exe"}
                or not isinstance(environment, Mapping)
            ):
                return base
            pinned_env = dict(environment)
            if (
                any(
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    or "\0" in key
                    or "\0" in value
                    or "=" in key
                    for key, value in pinned_env.items()
                )
                or any(
                    key.casefold() in {"docker_context", "docker_host"}
                    and key != "DOCKER_HOST"
                    for key in pinned_env
                )
                or not isinstance(pinned_env.get("DOCKER_HOST"), str)
                or _LOCAL_NPIPE.fullmatch(pinned_env["DOCKER_HOST"]) is None
            ):
                return base
            argv = [docker_executable, "exec", "-i", container_id, "kimi", "acp"]

        deadline = time.monotonic() + self._timeout_seconds
        process: Any | None = None
        channel: _JsonLineChannel | None = None
        try:
            process_options: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "shell": False,
                "bufsize": 0,
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            }
            if normalized == "KIMI_CODE":
                process_options["env"] = pinned_env
            process = self._process_factory(argv, **process_options)
            channel = _JsonLineChannel(
                process, deadline=deadline, max_output_bytes=self._max_output_bytes
            )
            if normalized == "CODEX":
                channel.send(
                    {
                        "id": 1,
                        "method": "initialize",
                        "params": {"clientInfo": {"name": "agentstate_guard", "version": "0.1.0"}},
                    }
                )
            else:
                channel.send(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": 1,
                            "clientCapabilities": {},
                            "clientInfo": {"name": "agentstate_guard", "version": "0.1.0"},
                        },
                    }
                )
            handshake = channel.response(1, jsonrpc=normalized == "KIMI_CODE")
            version = _version(normalized, handshake)
            protocol_version = _protocol_version(handshake)
            if normalized == "CODEX":
                channel.send({"method": "initialized", "params": {}})
                channel.send({"id": 2, "method": "thread/list", "params": {"limit": 50}})
            else:
                channel.send({"jsonrpc": "2.0", "id": 2, "method": "session/list", "params": {}})
            listed = channel.response(2, jsonrpc=normalized == "KIMI_CODE")
            sessions, has_more = _sessions(normalized, listed)
            return {
                **base,
                "status": "AVAILABLE" if sessions else "EMPTY",
                "reason_code": "SESSION_LIST_AVAILABLE" if sessions else "SESSION_LIST_EMPTY",
                "identity_basis": "PROTOCOL_HANDSHAKE",
                "version": version,
                "protocol_version": protocol_version,
                "sessions": sessions,
                "has_more": has_more,
            }
        except _QueryFailure as failure:
            return {**base, "status": failure.status, "reason_code": failure.reason_code}
        except FileNotFoundError:
            return {**base, "status": "NOT_PRESENT", "reason_code": "SESSION_CLI_NOT_PRESENT"}
        except PermissionError:
            return {**base, "status": "UNREACHABLE", "reason_code": "SESSION_CLI_PERMISSION_DENIED"}
        except OSError:
            return {**base, "status": "UNREACHABLE", "reason_code": "SESSION_CLI_FAILED"}
        finally:
            if process is not None:
                _close_process(process, channel)


__all__ = ["SessionReader"]
