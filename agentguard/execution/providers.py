"""Codex app-server and Kimi ACP adapters with conservative control boundaries."""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
import re
import threading
from collections.abc import Callable

from agentguard.core.sanitizer import sanitize_text

from .protocol import ProtocolError, RpcProcess, RpcRemoteError
from .scope_readiness import ScopeReadinessError, UnixScopeProof


class ProviderError(ProtocolError):
    pass


def _object(value):
    return value if isinstance(value, dict) else {}


def _text(value, maximum=32_768):
    return sanitize_text(value[:maximum]) if isinstance(value, str) else ""


def _id(value):
    if type(value) is int:
        return value
    return value if isinstance(value, str) and 0 < len(value) <= 256 and "\0" not in value else None


def _native_id(value):
    return _id(value) if isinstance(value, str) else None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value or not (ntpath.isabs(value) or posixpath.isabs(value)):
        raise ProviderError("EXECUTION_PROVIDER_CWD_INVALID")
    return value


def _path_key(value):
    return ntpath.normcase(ntpath.normpath(value)) if ntpath.splitdrive(value)[0] else posixpath.normpath(value)


def _within(value, root):
    path_module = ntpath if ntpath.splitdrive(root)[0] else posixpath
    try:
        return path_module.commonpath([_path_key(value), _path_key(root)]) == _path_key(root)
    except ValueError:
        return False


def _event(kind, payload, params, request_id=None):
    turn = _object(params.get("turn"))
    return {"kind": kind, "payload": payload,
            "native_session_id": _native_id(params.get("threadId", params.get("sessionId"))),
            "native_turn_id": _native_id(params.get("turnId", turn.get("id"))),
            "native_request_id": _id(request_id)}


def _text_events(kind, text, payload, params):
    safe = sanitize_text(text) if isinstance(text, str) else ""
    chunks = [safe[offset:offset + 8192] for offset in range(0, len(safe), 8192)] or [""]
    return [_event(kind, {**payload, "text": chunk, "chunk_index": index, "chunk_count": len(chunks)}, params)
            for index, chunk in enumerate(chunks)]


def _questions(params):
    result = []
    questions = params.get("questions", [])
    if not isinstance(questions, list):
        return result
    for raw in questions[:3]:
        value = _object(raw)
        question_id = _native_id(value.get("id"))
        if question_id is None:
            continue
        options = value.get("options", [])
        result.append({"id": question_id, "header": _text(value.get("header"), 120),
                       "question": _text(value.get("question"), 4000), "secret": value.get("isSecret") is True,
                       "options": [{"label": _text(_object(option).get("label"), 200),
                                    "description": _text(_object(option).get("description"), 1000)}
                                   for option in options[:20]] if isinstance(options, list) else []})
    return result


def normalize(provider: str, raw: dict) -> list[dict]:
    """Select safe public events; raw reasoning, commands and environments stay out."""
    if not isinstance(raw, dict):
        return []
    method = raw.get("method")
    params = _object(raw.get("params"))
    if method == "asg/protocolFailure":
        reason = params.get("reason_code", "")
        reason = reason if isinstance(reason, str) and re.fullmatch(r"EXECUTION_[A-Z_]+", reason) else "EXECUTION_PROTOCOL_FAILED"
        return [_event("connection.failed", {"reason_code": reason}, params)]
    if provider == "CODEX":
        if method == "item/agentMessage/delta":
            return _text_events("output.delta", params.get("delta"), {"item_id": _id(params.get("itemId"))}, params)
        if method in {"turn/started", "turn/completed"}:
            turn = _object(params.get("turn"))
            status = turn.get("status")
            mapped = {"inProgress": "running", "completed": "completed", "interrupted": "interrupted", "failed": "failed"}.get(status, "unknown")
            return [_event("turn.started" if method.endswith("started") else "turn.completed", {"status": mapped}, params)]
        if method in {"item/started", "item/completed"}:
            item = _object(params.get("item"))
            item_type = item.get("type")
            if item_type == "reasoning":
                return []
            payload = {"item_id": _id(item.get("id")), "item_type": _text(item_type, 100)}
            if item_type == "agentMessage" and method == "item/completed":
                return _text_events("output.completed", item.get("text"), payload, params)
            status = item.get("status")
            if status in {"inProgress", "completed", "failed", "declined"}:
                payload["status"] = status
            return [_event("tool.started" if method.endswith("started") else "tool.completed", payload, params)]
        approvals = {"item/commandExecution/requestApproval": "command_execution",
                     "item/fileChange/requestApproval": "file_change"}
        if method in approvals and _id(raw.get("id")) is not None:
            kind = approvals[method]
            payload = {"approval_kind": kind, "title": "Command approval" if kind == "command_execution" else "File change approval",
                       "summary": "A command requires approval" if kind == "command_execution" else "File changes require approval",
                       "allowed_decisions": ["DENY", "CANCEL"], "request_digest": _digest(params),
                       "item_id": _id(params.get("itemId")), "cwd": _text(params.get("cwd"), 4096)}
            # Accepting an escalation can bypass the verified workspace sandbox.
            payload["reason_code"] = "EXECUTION_SCOPE_ESCALATION_UNSUPPORTED"
            if params.get("grantRoot"):
                payload["allowed_decisions"] = ["DENY", "CANCEL"]
                payload["reason_code"] = "EXECUTION_PERSISTENT_PERMISSION_UNSUPPORTED"
            return [_event("approval.requested", payload, params, raw["id"])]
        if method == "item/tool/requestUserInput" and _id(raw.get("id")) is not None:
            return [_event("input.requested", {"title": "Agent question", "questions": _questions(params),
                                               "request_digest": _digest(params)}, params, raw["id"])]
        if method == "error":
            return [_event("provider.error", {"reason_code": "EXECUTION_PROVIDER_ERROR", "will_retry": params.get("willRetry") is True}, params)]
    if provider == "KIMI_CODE":
        if method == "session/request_permission" and _id(raw.get("id")) is not None:
            options = params.get("options", [])
            kinds = {_object(option).get("kind") for option in options} if isinstance(options, list) else set()
            allowed = ["DENY", "CANCEL"]
            if "allow_once" in kinds:
                allowed.insert(0, "ALLOW_ONCE")
            payload = {"approval_kind": "provider_permission", "title": "Kimi permission",
                       "summary": "A Kimi tool requires permission", "allowed_decisions": allowed,
                       "request_digest": _digest(params)}
            return [_event("approval.requested", payload, params, raw["id"])]
        if method == "session/update":
            update = _object(params.get("update"))
            update_type = update.get("sessionUpdate")
            if update_type in {"agent_message_chunk", "user_message_chunk"}:
                content = _object(update.get("content"))
                if content.get("type") == "text":
                    return _text_events("output.delta" if update_type == "agent_message_chunk" else "input.replayed",
                                        content.get("text"), {"replay": update_type == "user_message_chunk"}, params)
                if update_type == "agent_message_chunk":
                    return [_event("output.omitted", {"reason_code": "EXECUTION_NON_TEXT_OUTPUT_UNSUPPORTED", "incomplete": True}, params)]
            if update_type in {"tool_call", "tool_call_update"}:
                payload = {"item_id": _id(update.get("toolCallId")), "status": update.get("status") if update.get("status") in {"pending", "in_progress", "completed", "failed"} else "unknown"}
                return [_event("tool.started" if update_type == "tool_call" else "tool.updated", payload, params)]
            if update_type in {"current_mode_update", "session_info_update", "config_option_update", "usage_update"}:
                return [_event("session.updated", {"update_type": update_type}, params)]
    return []


class ProviderClient:
    provider = ""
    request_methods: frozenset[str] = frozenset()

    def __init__(self, rpc: RpcProcess, on_event: Callable[[dict], None] | None = None):
        self.rpc, self.on_event = rpc, on_event
        self._lock = threading.RLock()
        self._pending: dict[object, dict] = {}
        self._consumed_requests: set[object] = set()
        self._sessions: dict[str, dict] = {}
        self._turns: dict[str, str] = {}
        self.capabilities: dict = {}
        self.rpc.on_message = self._message

    def normalize(self, raw):
        return normalize(self.provider, raw)

    def _emit(self, event):
        if self.on_event is not None:
            self.on_event(event)

    def _message(self, raw):
        events = self.normalize(raw)
        method = raw.get("method")
        request_id = raw.get("id")
        if request_id is not None:
            if method not in self.request_methods:
                self.rpc.respond(request_id, error={"code": -32601, "message": "Unsupported client method"})
                self._emit(_event("request.unsupported", {"reason_code": "EXECUTION_CLIENT_METHOD_UNSUPPORTED"}, _object(raw.get("params")), request_id))
                return
            params = _object(raw.get("params"))
            with self._lock:
                if request_id in self._pending or request_id in self._consumed_requests or len(self._pending) >= 32 or len(self._consumed_requests) >= 4096 or not events:
                    self.rpc.respond(request_id, error={"code": -32000, "message": "Client request unavailable"})
                    return
                pending = {"method": method, "session_id": events[0]["native_session_id"],
                           "allowed": events[0]["payload"].get("allowed_decisions", []),
                           "questions": _questions(params),
                           "options": [{"optionId": _id(_object(option).get("optionId")), "kind": _object(option).get("kind")}
                                       for option in params.get("options", [])[:32]] if isinstance(params.get("options"), list) else []}
                self._pending[request_id] = pending
            if self.on_event is None:
                self.reply(request_id, "DENY")
                return
        for event in events:
            with self._lock:
                session_id, turn_id = event["native_session_id"], event["native_turn_id"]
                if event["kind"] == "turn.started" and session_id and turn_id:
                    self._turns[session_id] = turn_id
                if event["kind"] == "turn.completed" and self._turns.get(session_id) == turn_id:
                    self._turns.pop(session_id, None)
            self._emit(event)

    def _ready(self):
        if not self.capabilities:
            raise ProviderError("EXECUTION_PROVIDER_NOT_INITIALIZED")

    def _session(self, native_id):
        self._ready()
        with self._lock:
            session = self._sessions.get(native_id)
        if session is None:
            raise ProviderError("EXECUTION_PROVIDER_SESSION_NOT_BOUND")
        return session

    def reply(self, request_id, decision: str | dict = "DENY", answers: dict | None = None) -> dict:
        if isinstance(decision, dict):
            answers = decision.get("answers", answers)
            decision = decision.get("decision", "ANSWER" if answers is not None else "DENY")
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                raise ProviderError("EXECUTION_PROVIDER_REQUEST_NOT_PENDING")
            result = self._reply_result(pending, decision, answers)
            self._pending.pop(request_id, None)
            self._consumed_requests.add(request_id)
            # A lost write acknowledgement cannot restore an approval for replay.
            self.rpc.respond(request_id, result=result)
        return {"native_request_id": request_id, "status": "responded"}

    def _reply_result(self, pending, decision, answers):
        raise NotImplementedError

    def close(self):
        self.rpc.close()
        with self._lock:
            self._pending.clear()


class CodexProvider(ProviderClient):
    provider = "CODEX"
    request_methods = frozenset({"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/tool/requestUserInput"})

    def __init__(self, rpc: RpcProcess, on_event=None, *, scope_verified: bool = False,
                 scope_proof: UnixScopeProof | None = None):
        super().__init__(rpc, on_event)
        # The legacy Python-only boolean is accepted for compatibility, but can
        # never grant authority. Runtime supplies only a bound registry receipt.
        self._scoped = scope_proof is not None
        self._scope_proof = (scope_proof if isinstance(scope_proof, UnixScopeProof)
                             and scope_proof.authentic() else None)
        self._scope_channel = object()

    def initialize(self, timeout=30) -> dict:
        result = self.rpc.request("initialize", {"clientInfo": {"name": "agentstate_guard", "version": "0.9.0.dev0"},
                                                 "capabilities": {"experimentalApi": self._scope_proof is not None}}, timeout=timeout)
        self.rpc.notify("initialized")
        platform = result.get("platformFamily", result.get("platformOs"))
        user_agent = result.get("userAgent", "")
        version = re.search(r"/(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)", user_agent) if isinstance(user_agent, str) else None
        ready = (self._scope_proof is not None and platform in {"unix", "linux"}
                 and result.get("platformOs") == "linux" and version is not None
                 and version.group(1) == self._scope_proof.version)
        sandbox_status = "verified" if ready else "unverified"
        if platform == "windows" and not self._scoped:
            try:
                sandbox_status = self.rpc.request("windowsSandbox/readiness", {}, timeout=timeout).get("status", "unknown")
            except RpcRemoteError:
                sandbox_status = "unknown"
            ready = sandbox_status == "ready"
        self.capabilities = {"provider": self.provider, "protocol": "codex-app-server", "server_version": version.group(1) if version else None,
                             "create": True, "resume": True, "fork": True, "send": True, "stream": True, "cancel": True,
                             "read_only_supported": ready, "write": ready, "scope_enforcement": "PROVIDER_SANDBOX" if ready else "UNAVAILABLE",
                             "sandbox_status": sandbox_status, "client_fs": False, "client_terminal": False}
        if not ready:
            self.capabilities["reason_code"] = ("EXECUTION_PROVIDER_VERSION_CHANGED" if self._scope_proof is not None
                                                else "EXECUTION_PROVIDER_SCOPE_UNVERIFIED")
        return dict(self.capabilities)

    def _scoped_configuration(self, effective):
        if self._scope_proof is None:
            raise ProviderError("EXECUTION_SCOPE_PROOF_INVALID")
        try:
            self._scope_proof.validate_configuration(effective)
        except ScopeReadinessError as error:
            raise ProviderError(error.reason_code) from error
        servers = effective.get("mcp_servers", {})
        if not isinstance(servers, dict) or len(servers) > 128 or any(not isinstance(name, str) for name in servers):
            raise ProviderError("EXECUTION_PROVIDER_CONFIG_UNVERIFIED")
        return tuple(sorted(servers))

    def _scoped_ack(self, result, cwd, read_only):
        proof = self._scope_proof
        profile = _object(result.get("activePermissionProfile"))
        roots = result.get("runtimeWorkspaceRoots")
        sandbox = _object(result.get("sandbox"))
        if (proof is None or profile != {"id": proof.profile(read_only), "extends": ":read-only"}
                or roots != [proof.root] or _path_key(cwd) != _path_key(proof.root)
                or sandbox.get("networkAccess") is not False
                or not read_only and (sandbox.get("excludeSlashTmp") is not True
                                      or sandbox.get("excludeTmpdirEnvVar") is not True)):
            raise ProviderError("EXECUTION_PROVIDER_PROFILE_NOT_ACKNOWLEDGED")

    def create_session(self, cwd: str, native_id=None, mode="NEW", read_only=False, *, native_session_id=None, timeout=30) -> dict:
        self._ready()
        if native_session_id is not None:
            if native_id is not None and native_id != native_session_id:
                raise ProviderError("EXECUTION_PROVIDER_SESSION_INPUT_INVALID")
            native_id = native_session_id
        cwd = _path(cwd)
        if type(read_only) is not bool or mode not in {"NEW", "RESUME", "FORK"} or (mode == "NEW") != (native_id is None):
            raise ProviderError("EXECUTION_PROVIDER_SESSION_INPUT_INVALID")
        if not self.capabilities["read_only_supported" if read_only else "write"]:
            raise ProviderError("EXECUTION_PROVIDER_SCOPE_UNAVAILABLE")
        configuration = self.rpc.request('config/read', {'cwd': cwd, 'includeLayers': False}, timeout=timeout)
        effective = configuration.get('config')
        if not isinstance(effective, dict):
            raise ProviderError('EXECUTION_PROVIDER_CONFIG_UNVERIFIED')
        servers = effective.get('mcp_servers', {})
        if not isinstance(servers, dict) or len(servers) > 128:
            raise ProviderError('EXECUTION_PROVIDER_CONFIG_UNVERIFIED')
        overrides = {'mcp_servers': {name: {'enabled': False} for name in servers},
                     'features': {'apps': False, 'multi_agent': False}, 'web_search': 'disabled',
                     'sandbox_workspace_write': {'network_access': False, 'exclude_tmpdir_env_var': True,
                                                 'exclude_slash_tmp': True}}
        tool_names = ()
        if self._scoped:
            tool_names = self._scoped_configuration(effective)
            overrides.pop('sandbox_workspace_write')
            overrides['features'].update(remote_control=False, remote_plugin=False)
            if _path_key(cwd) != _path_key(self._scope_proof.root):
                raise ProviderError("EXECUTION_PROVIDER_SCOPE_MISMATCH")
            params = {"cwd": cwd, "permissions": self._scope_proof.profile(read_only),
                      "runtimeWorkspaceRoots": [self._scope_proof.root],
                      "approvalPolicy": "on-request", "approvalsReviewer": "user", 'config': overrides}
        else:
            params = {"cwd": cwd, "sandbox": "read-only" if read_only else "workspace-write",
                      "approvalPolicy": "on-request", "approvalsReviewer": "user", 'config': overrides}
        if mode == "NEW":
            params["ephemeral"] = False
        else:
            if not isinstance(native_id, str) or _id(native_id) is None:
                raise ProviderError("EXECUTION_PROVIDER_SESSION_INPUT_INVALID")
            params["threadId"] = native_id
            if mode == "FORK":
                params["ephemeral"] = False
        result = self.rpc.request({"NEW": "thread/start", "RESUME": "thread/resume", "FORK": "thread/fork"}[mode], params, timeout=timeout)
        session_id = _id(_object(result.get("thread")).get("id"))
        returned_cwd = result.get("cwd")
        sandbox = _object(result.get("sandbox"))
        expected = "readOnly" if read_only else "workspaceWrite"
        if not isinstance(session_id, str) or sandbox.get("type") != expected or result.get("approvalPolicy") != "on-request" or result.get("approvalsReviewer") != "user":
            raise ProviderError("EXECUTION_PROVIDER_POLICY_NOT_ACKNOWLEDGED")
        if not isinstance(returned_cwd, str) or _path_key(returned_cwd) != _path_key(cwd):
            raise ProviderError("EXECUTION_PROVIDER_CWD_MISMATCH")
        if mode == "RESUME" and session_id != native_id or mode == "FORK" and session_id == native_id:
            raise ProviderError("EXECUTION_PROVIDER_SESSION_ID_MISMATCH")
        if sandbox.get("networkAccess") is True or any(not isinstance(root, str) or not _within(root, cwd) for root in sandbox.get("writableRoots", [])):
            raise ProviderError("EXECUTION_PROVIDER_SCOPE_MISMATCH")
        if self._scoped:
            self._scoped_ack(result, cwd, read_only)
        policy = {"type": expected, "networkAccess": False}
        if not read_only:
            policy.update(writableRoots=[cwd], excludeSlashTmp=True, excludeTmpdirEnvVar=True)
        with self._lock:
            self._sessions[session_id] = {"cwd": cwd, "read_only": read_only, "sandbox_policy": policy}
            if self._scoped:
                self._sessions[session_id].update(scope_channel=self._scope_channel, native_id=session_id,
                    profile=self._scope_proof.profile(read_only), tool_names=tool_names)
        return {"native_session_id": session_id, "cwd": returned_cwd, "read_only": read_only,
                "capabilities": dict(self.capabilities), "scope_enforcement": "PROVIDER_SANDBOX"}

    def start_turn(self, native_session_id: str, text: str, cwd=None, read_only=None, *, timeout=30) -> dict:
        session = self._session(native_session_id)
        if cwd is not None and _path_key(_path(cwd)) != _path_key(session["cwd"]) or read_only is not None and read_only is not session["read_only"]:
            raise ProviderError("EXECUTION_PROVIDER_SCOPE_MISMATCH")
        if not isinstance(text, str) or not text:
            raise ProviderError("EXECUTION_PROVIDER_INPUT_INVALID")
        params = {"threadId": native_session_id, "cwd": session["cwd"],
                  "input": [{"type": "text", "text": text}],
                  "approvalPolicy": "on-request", "approvalsReviewer": "user"}
        if self._scoped:
            proof = self._scope_proof
            if (proof is None or not proof.authentic() or session.get('scope_channel') is not self._scope_channel
                    or session.get('native_id') != native_session_id
                    or session.get('profile') != proof.profile(session['read_only'])
                    or _path_key(session['cwd']) != _path_key(proof.root)):
                raise ProviderError("EXECUTION_SCOPE_PROOF_INVALID")
            effective = self.rpc.request('config/read', {'cwd': session['cwd'], 'includeLayers': False}, timeout=timeout).get('config')
            if not isinstance(effective, dict) or self._scoped_configuration(effective) != session['tool_names']:
                raise ProviderError("EXECUTION_PROVIDER_CONFIG_CHANGED")
            # The same private channel/thread retains its last fully checked
            # creation/resume/fork ACK. No unsupported empty-rollout resume and
            # no policy-changing settings call precedes this fixed-profile turn.
            params.update(permissions=session['profile'], runtimeWorkspaceRoots=[proof.root])
        else:
            params['sandboxPolicy'] = session['sandbox_policy']
        result = self.rpc.request("turn/start", params, timeout=timeout)
        turn = _object(result.get("turn"))
        turn_id = _id(turn.get("id"))
        if not isinstance(turn_id, str):
            raise ProviderError("EXECUTION_PROVIDER_TURN_ID_MISSING")
        if turn.get("status") == "inProgress":
            with self._lock:
                self._turns[native_session_id] = turn_id
        return {"native_turn_id": turn_id, "status": {"inProgress": "running", "completed": "completed", "failed": "failed", "interrupted": "interrupted"}.get(turn.get("status"), "unknown")}

    def invalidate_scope(self):
        if self._scoped:
            with self._lock:
                self._scope_proof = None
                self._scope_channel = object()
                self._sessions.clear()
                self.capabilities = {}

    def close(self):
        self.invalidate_scope()
        super().close()

    def cancel(self, native_session_id: str, native_turn_id=None) -> dict:
        self._session(native_session_id)
        with self._lock:
            turn_id = native_turn_id or self._turns.get(native_session_id)
        if not isinstance(turn_id, str) or _id(turn_id) is None:
            raise ProviderError("EXECUTION_PROVIDER_TURN_ID_MISSING")
        self.rpc.request("turn/interrupt", {"threadId": native_session_id, "turnId": turn_id})
        return {"native_turn_id": turn_id, "status": "cancel_requested"}

    def _reply_result(self, pending, decision, answers):
        if pending["method"] == "item/tool/requestUserInput":
            if decision in {"DENY", "CANCEL"}:
                return {"answers": {}}
            if decision not in {"ANSWER", "RESPOND"} or not isinstance(answers, dict) or any(question["secret"] for question in pending["questions"]):
                raise ProviderError("EXECUTION_PROVIDER_ANSWER_INVALID")
            question_ids = {question["id"] for question in pending["questions"]}
            if set(answers) != question_ids:
                raise ProviderError("EXECUTION_PROVIDER_ANSWER_INVALID")
            result = {}
            for key, value in answers.items():
                values = value.get("answers") if isinstance(value, dict) else value if isinstance(value, list) else [value]
                if not isinstance(values, list) or len(values) > 20 or any(not isinstance(answer, str) or len(answer) > 4000 for answer in values):
                    raise ProviderError("EXECUTION_PROVIDER_ANSWER_INVALID")
                result[key] = {"answers": values}
            return {"answers": result}
        if decision == "ALLOW_ONCE" and self._sessions.get(pending["session_id"], {}).get("read_only", True):
            raise ProviderError("EXECUTION_READ_ONLY_ESCALATION_REJECTED")
        if decision not in pending["allowed"]:
            raise ProviderError("EXECUTION_PROVIDER_DECISION_INVALID")
        return {"decision": {"ALLOW_ONCE": "accept", "DENY": "decline", "CANCEL": "cancel"}[decision]}


class KimiProvider(ProviderClient):
    provider = "KIMI_CODE"
    request_methods = frozenset({"session/request_permission"})

    def __init__(self, rpc: RpcProcess, on_event=None):
        super().__init__(rpc, on_event)
        self._replaying_sessions: set[str] = set()

    def normalize(self, raw):
        events = super().normalize(raw)
        with self._lock:
            for event in events:
                if event["native_session_id"] in self._replaying_sessions:
                    event["payload"]["replay"] = True
                    if event["kind"].startswith("output."):
                        event["kind"] = "output.replayed"
                    elif event["kind"].startswith("tool."):
                        event["kind"] = "tool.replayed"
        return events

    def initialize(self, timeout=30) -> dict:
        result = self.rpc.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                                 "clientInfo": {"name": "agentstate_guard", "version": "0.9.0.dev0"}}, timeout=timeout)
        if result.get("protocolVersion") != 1:
            raise ProviderError("EXECUTION_ACP_VERSION_UNSUPPORTED")
        capabilities = _object(result.get("agentCapabilities"))
        session_capabilities = _object(capabilities.get("sessionCapabilities"))
        self.capabilities = {"provider": self.provider, "protocol": "acp", "protocol_version": 1,
                             "server_version": _text(_object(result.get("agentInfo")).get("version"), 40),
                             "create": True, "resume": "resume" in session_capabilities or capabilities.get("loadSession") is True,
                             "load": capabilities.get("loadSession") is True, "list": "list" in session_capabilities,
                             "fork": False, "send": True, "stream": True, "cancel": True,
                             "read_only_supported": False, "write": False, "scope_enforcement": "UNAVAILABLE",
                             "reason_code": "EXECUTION_ACP_SCOPE_UNENFORCED", "client_fs": False, "client_terminal": False}
        self._resume_method = "session/resume" if "resume" in session_capabilities else "session/load"
        return dict(self.capabilities)

    def create_session(self, cwd: str, native_id=None, mode="NEW", read_only=False, *, native_session_id=None, timeout=30) -> dict:
        self._ready()
        if native_session_id is not None:
            if native_id is not None and native_id != native_session_id:
                raise ProviderError("EXECUTION_PROVIDER_SESSION_INPUT_INVALID")
            native_id = native_session_id
        cwd = _path(cwd)
        if read_only:
            raise ProviderError("EXECUTION_ACP_READ_ONLY_UNSUPPORTED")
        if type(read_only) is not bool or mode not in {"NEW", "RESUME"} or (mode == "NEW") != (native_id is None):
            raise ProviderError("EXECUTION_ACP_SESSION_MODE_UNSUPPORTED")
        params = {"cwd": cwd, "mcpServers": []}
        if mode == "RESUME":
            if not self.capabilities["resume"] or not isinstance(native_id, str) or _id(native_id) is None:
                raise ProviderError("EXECUTION_ACP_RESUME_UNSUPPORTED")
            params["sessionId"] = native_id
        replay = mode == "RESUME" and self._resume_method == "session/load"
        if replay:
            with self._lock:
                self._replaying_sessions.add(native_id)
        result = self.rpc.request("session/new" if mode == "NEW" else self._resume_method, params, timeout=timeout)
        if replay:
            self.rpc.flush_events(timeout=timeout)
            with self._lock:
                self._replaying_sessions.discard(native_id)
        session_id = _id(result.get("sessionId", native_id))
        if not isinstance(session_id, str) or mode == "RESUME" and session_id != native_id:
            raise ProviderError("EXECUTION_PROVIDER_SESSION_ID_MISMATCH")
        if result.get("cwd") is not None and _path_key(result["cwd"]) != _path_key(cwd):
            raise ProviderError("EXECUTION_PROVIDER_CWD_MISMATCH")
        with self._lock:
            self._sessions[session_id] = {"cwd": cwd, "read_only": False}
        return {"native_session_id": session_id, "cwd": cwd, "read_only": False,
                "capabilities": dict(self.capabilities), "scope_enforcement": "UNAVAILABLE",
                "history_replayed": replay}

    def start_turn(self, native_session_id: str, text: str, cwd=None, read_only=None, *, timeout=600) -> dict:
        session = self._session(native_session_id)
        if cwd is not None and _path_key(_path(cwd)) != _path_key(session["cwd"]) or read_only not in {None, False}:
            raise ProviderError("EXECUTION_PROVIDER_SCOPE_MISMATCH")
        if not isinstance(text, str) or not text:
            raise ProviderError("EXECUTION_PROVIDER_INPUT_INVALID")
        self._emit(_event("turn.started", {"status": "running"}, {"sessionId": native_session_id}))
        result = self.rpc.request("session/prompt", {"sessionId": native_session_id, "prompt": [{"type": "text", "text": text}]}, timeout=timeout)
        stop_reason = result.get("stopReason")
        status = {"end_turn": "completed", "cancelled": "interrupted", "max_tokens": "failed", "max_turn_requests": "failed", "refusal": "failed"}.get(stop_reason, "unknown")
        payload = {"status": status, "stop_reason": stop_reason if stop_reason in {"end_turn", "cancelled", "max_tokens", "max_turn_requests", "refusal"} else "unknown"}
        self._emit(_event("turn.completed", payload, {"sessionId": native_session_id}))
        return {"native_turn_id": None, **payload}

    def cancel(self, native_session_id: str, native_turn_id=None) -> dict:
        self._session(native_session_id)
        self.rpc.notify("session/cancel", {"sessionId": native_session_id})
        return {"native_turn_id": None, "status": "cancel_requested"}

    def _reply_result(self, pending, decision, answers):
        if decision not in pending["allowed"]:
            raise ProviderError("EXECUTION_PROVIDER_DECISION_INVALID")
        kind = {"ALLOW_ONCE": "allow_once", "DENY": "reject_once"}.get(decision)
        if kind:
            option = next((option for option in pending["options"] if option["kind"] == kind and isinstance(option["optionId"], str)), None)
            if option:
                return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
            if decision == "ALLOW_ONCE":
                raise ProviderError("EXECUTION_PROVIDER_DECISION_INVALID")
        return {"outcome": {"outcome": "cancelled"}}


def provider_client(provider: str, rpc: RpcProcess, on_event=None, *, scope_verified=False, scope_proof=None) -> ProviderClient:
    if provider == "CODEX":
        return CodexProvider(rpc, on_event, scope_verified=scope_verified, scope_proof=scope_proof)
    if provider == "KIMI_CODE":
        return KimiProvider(rpc, on_event)
    raise ProviderError("EXECUTION_PROVIDER_UNSUPPORTED")
