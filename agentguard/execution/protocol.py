"""Bounded JSONL RPC transport for one explicitly launched provider process."""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


class ProtocolError(RuntimeError):
    def __init__(self, reason_code: str, *, request_id=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.request_id = request_id


class RpcRemoteError(ProtocolError):
    def __init__(self, error_code: int | None, *, request_id=None):
        super().__init__("EXECUTION_RPC_REJECTED", request_id=request_id)
        self.error_code = error_code


class RpcTimeout(ProtocolError):
    def __init__(self, *, request_id=None):
        super().__init__("EXECUTION_RPC_TIMEOUT", request_id=request_id)


@dataclass(frozen=True)
class RpcLimits:
    stdin_bytes: int = 16 * 1024 * 1024
    stdout_bytes: int = 64 * 1024 * 1024
    stderr_bytes: int = 256 * 1024
    message_bytes: int = 1024 * 1024
    queue_bytes: int = 4 * 1024 * 1024
    pending_requests: int = 32
    queued_messages: int = 256

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in self.__dict__.values()):
            raise ValueError("RPC limits must be positive integers")


@dataclass
class _Write:
    data: bytes
    sent: Future
    deadline: float


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("RPC timeout must be positive and finite")
    return time.monotonic() + value


def _valid_id(value) -> bool:
    return type(value) is int or isinstance(value, str) and 0 < len(value) <= 256


def _reject_constant(_):
    raise ValueError("Nonfinite JSON constant")


class RpcProcess:
    """Responses bypass callbacks, so a callback can safely make an RPC request.

    A timeout never retries a write. The caller owns durable intent and decides
    whether the provider accepted it before issuing any further mutation.
    """

    def __init__(self, argv: Sequence[str], cwd=None, env: Mapping[str, str] | None = None,
                 on_message: Callable[[dict], None] | None = None, *, limits: RpcLimits | None = None,
                 jsonrpc: bool = True):
        if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv):
            raise ValueError("RPC argv must be a nonempty sequence of strings")
        self.limits = limits or RpcLimits()
        if type(jsonrpc) is not bool:
            raise ValueError("jsonrpc must be a boolean")
        self.jsonrpc = jsonrpc
        self.on_message = on_message
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._pending: dict[int, Future] = {}
        self._next_id = 0
        self._failure_reason: str | None = None
        self._stdin_bytes = self._stdout_bytes = self._stderr_bytes = 0
        self._queued_input_bytes = self._queued_event_bytes = 0
        self._writes: queue.Queue[_Write] = queue.Queue(self.limits.queued_messages)
        self._events: queue.Queue[tuple[dict | Future, int]] = queue.Queue(self.limits.queued_messages)
        try:
            self._process = subprocess.Popen(
                list(argv), cwd=cwd, env=dict(env) if env is not None else None,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            raise ProtocolError("EXECUTION_PROCESS_START_FAILED") from None
        self._threads = [threading.Thread(target=fn, name="asg-rpc-" + name, daemon=True)
                         for name, fn in (("write", self._write), ("stdout", self._read),
                                          ("stderr", self._drain_stderr), ("events", self._dispatch))]
        for thread in self._threads:
            thread.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def alive(self) -> bool:
        return not self._closed.is_set() and self._process.poll() is None

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def byte_counts(self) -> dict[str, int]:
        with self._lock:
            return {"stdin": self._stdin_bytes, "stdout": self._stdout_bytes, "stderr": self._stderr_bytes}

    def _check_open(self):
        if self._closed.is_set():
            raise ProtocolError(self._failure_reason or "EXECUTION_PROTOCOL_CLOSED")

    def _send(self, frame: dict, deadline: float) -> Future:
        if not self.jsonrpc:
            frame = {key: value for key, value in frame.items() if key != "jsonrpc"}
        try:
            data = (json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise ProtocolError("EXECUTION_RPC_INPUT_INVALID") from None
        sent = Future()
        with self._lock:
            self._check_open()
            if len(data) > self.limits.message_bytes or self._stdin_bytes + self._queued_input_bytes + len(data) > self.limits.stdin_bytes:
                raise ProtocolError("EXECUTION_STDIN_LIMITED")
            if self._queued_input_bytes + len(data) > self.limits.queue_bytes:
                raise ProtocolError("EXECUTION_INPUT_QUEUE_LIMITED")
            try:
                self._writes.put_nowait(_Write(data, sent, deadline))
            except queue.Full:
                raise ProtocolError("EXECUTION_INPUT_QUEUE_LIMITED") from None
            self._queued_input_bytes += len(data)
        return sent

    @staticmethod
    def _wait(future: Future, deadline: float, request_id=None):
        try:
            return future.result(timeout=max(0, deadline - time.monotonic()))
        except FutureTimeout:
            raise RpcTimeout(request_id=request_id) from None

    def request(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        deadline = _timeout(timeout)
        self._validate_method(method, params)
        with self._lock:
            self._check_open()
            if len(self._pending) >= self.limits.pending_requests:
                raise ProtocolError("EXECUTION_PENDING_LIMITED")
            self._next_id += 1
            request_id = self._next_id
            response = self._pending[request_id] = Future()
        try:
            sent = self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}, deadline)
            self._wait(sent, deadline, request_id)
            return self._wait(response, deadline, request_id)
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    @staticmethod
    def _validate_method(method, params):
        if not isinstance(method, str) or not 0 < len(method) <= 256 or "\0" in method or params is not None and not isinstance(params, dict):
            raise ProtocolError("EXECUTION_RPC_INPUT_INVALID")

    def notify(self, method: str, params: dict | None = None, timeout: float = 5):
        deadline = _timeout(timeout)
        self._validate_method(method, params)
        self._wait(self._send({"jsonrpc": "2.0", "method": method, "params": params or {}}, deadline), deadline)

    def respond(self, request_id, result: dict | None = None, error: dict | None = None, timeout: float = 5):
        if not _valid_id(request_id) or result is not None and error is not None:
            raise ProtocolError("EXECUTION_RPC_INPUT_INVALID")
        deadline = _timeout(timeout)
        frame = {"jsonrpc": "2.0", "id": request_id}
        frame["error" if error is not None else "result"] = error if error is not None else result or {}
        self._wait(self._send(frame, deadline), deadline, request_id)

    def flush_events(self, timeout: float = 5):
        """Wait until callbacks preceding the current response have been delivered."""
        if threading.current_thread() is self._threads[-1]:
            raise ProtocolError("EXECUTION_EVENT_BARRIER_REENTRANT")
        deadline = _timeout(timeout)
        barrier = Future()
        with self._lock:
            self._check_open()
            try:
                self._events.put_nowait((barrier, 0))
            except queue.Full:
                raise ProtocolError("EXECUTION_EVENT_QUEUE_LIMITED") from None
        self._wait(barrier, deadline)

    def _write(self):
        while not self._closed.is_set():
            try:
                item = self._writes.get(timeout=0.05)
            except queue.Empty:
                continue
            with self._lock:
                self._queued_input_bytes -= len(item.data)
            if time.monotonic() >= item.deadline:
                item.sent.set_exception(RpcTimeout())
                continue
            try:
                with self._lock:
                    self._check_open()
                    self._stdin_bytes += len(item.data)
                view = memoryview(item.data)
                while view:
                    written = self._process.stdin.write(view)
                    if not written:
                        raise OSError("closed stdin")
                    view = view[written:]
                self._process.stdin.flush()
                item.sent.set_result(None)
            except (OSError, ValueError, ProtocolError):
                if not item.sent.done():
                    item.sent.set_exception(ProtocolError("EXECUTION_STDIN_CLOSED"))
                self._fail("EXECUTION_STDIN_CLOSED")
                return

    def _read(self):
        try:
            while not self._closed.is_set():
                raw = self._process.stdout.readline(self.limits.message_bytes + 1)
                if not raw:
                    self._fail("EXECUTION_PROTOCOL_EOF")
                    return
                with self._lock:
                    self._stdout_bytes += len(raw)
                if len(raw) > self.limits.message_bytes or self._stdout_bytes > self.limits.stdout_bytes:
                    self._fail("EXECUTION_STDOUT_LIMITED")
                    return
                if not raw.endswith(b"\n"):
                    self._fail("EXECUTION_JSONL_TRUNCATED")
                    return
                frame = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
                if not isinstance(frame, dict) or frame.get("jsonrpc", "2.0") != "2.0":
                    raise ValueError("invalid RPC frame")
                if "method" in frame:
                    self._validate_method(frame["method"], frame.get("params"))
                    if "id" in frame and not _valid_id(frame["id"]):
                        raise ValueError("invalid request id")
                    with self._lock:
                        if self._queued_event_bytes + len(raw) > self.limits.queue_bytes:
                            raise ProtocolError("EXECUTION_EVENT_QUEUE_LIMITED")
                        try:
                            self._events.put_nowait((frame, len(raw)))
                        except queue.Full:
                            raise ProtocolError("EXECUTION_EVENT_QUEUE_LIMITED") from None
                        self._queued_event_bytes += len(raw)
                else:
                    if not _valid_id(frame.get("id")) or ("result" in frame) == ("error" in frame):
                        raise ValueError("invalid RPC response")
                    with self._lock:
                        pending = self._pending.get(frame["id"])
                        if pending is not None and not pending.done():
                            if "error" in frame:
                                error = frame["error"]
                                pending.set_exception(RpcRemoteError(error.get("code") if isinstance(error, dict) else None, request_id=frame["id"]))
                            elif frame["result"] is None or isinstance(frame["result"], dict):
                                pending.set_result(frame["result"] or {})
                            else:
                                pending.set_exception(ProtocolError("EXECUTION_RPC_RESULT_INVALID", request_id=frame["id"]))
        except ProtocolError as exc:
            self._fail(exc.reason_code)
        except (OSError, ValueError, UnicodeError, RecursionError):
            self._fail("EXECUTION_RPC_FRAME_INVALID")

    def _drain_stderr(self):
        try:
            while not self._closed.is_set():
                chunk = self._process.stderr.read(8192)
                if not chunk:
                    return
                with self._lock:
                    self._stderr_bytes += len(chunk)
                if self._stderr_bytes > self.limits.stderr_bytes:
                    self._fail("EXECUTION_STDERR_LIMITED")
                    return
        except (OSError, ValueError):
            if not self._closed.is_set():
                self._fail("EXECUTION_STDERR_CLOSED")

    def _dispatch(self):
        while True:
            try:
                frame, size = self._events.get(timeout=0.05)
            except queue.Empty:
                if self._closed.is_set():
                    if self._failure_reason and self.on_message:
                        try:
                            self.on_message({"method": "asg/protocolFailure", "params": {"reason_code": self._failure_reason}})
                        except Exception:  # noqa: BLE001 - isolate caller callbacks from transport cleanup.
                            _LOGGER.warning("EXECUTION_FAILURE_CALLBACK_FAILED")
                    return
                continue
            with self._lock:
                self._queued_event_bytes -= size
            if isinstance(frame, Future):
                frame.set_result(None)
                continue
            try:
                if self.on_message:
                    self.on_message(frame)
                elif "id" in frame:
                    self.respond(frame["id"], error={"code": -32601, "message": "Unsupported client method"})
            except Exception:  # noqa: BLE001 - caller callbacks must fail the client closed.
                self._fail("EXECUTION_CALLBACK_FAILED")

    def _fail(self, reason: str, *, notify=True):
        with self._lock:
            if self._closed.is_set():
                return
            self._failure_reason = reason if notify else None
            self._closed.set()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ProtocolError(reason))
            while True:
                try:
                    item = self._writes.get_nowait()
                except queue.Empty:
                    break
                if not item.sent.done():
                    item.sent.set_exception(ProtocolError(reason))
        self._terminate()

    def _terminate(self):
        if self._process.poll() is None:
            try:
                self._process.terminate()
            except OSError:
                pass

    def close(self):
        self._fail("EXECUTION_PROTOCOL_CLOSED", notify=False)
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        current = threading.current_thread()
        for thread in self._threads:
            if thread is not current:
                thread.join(timeout=2)
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
