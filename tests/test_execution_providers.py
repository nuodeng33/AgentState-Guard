"""Provider contracts, safety acknowledgements and native permission responses."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentguard.execution.protocol import ProtocolError
from agentguard.execution.providers import (
    CodexProvider,
    KimiProvider,
    ProviderError,
    normalize,
)


class FakeRpc:
    def __init__(self, results):
        self.results = results
        self.calls, self.notifications, self.responses = [], [], []
        self.on_message = None

    def request(self, method, params=None, timeout=30):
        self.calls.append((method, params))
        result = self.results[method]
        return result(params) if callable(result) else result

    def notify(self, method, params=None):
        self.notifications.append((method, params))

    def respond(self, request_id, result=None, error=None):
        self.responses.append({"id": request_id, "result": result, "error": error})

    def close(self):
        pass

    def flush_events(self, timeout=5):
        pass


def codex_rpc(**overrides):
    def thread(params):
        return {"thread": {"id": "thread-1"}, "cwd": params["cwd"],
                "sandbox": {"type": "readOnly" if params["sandbox"] == "read-only" else "workspaceWrite", "networkAccess": False, "writableRoots": []},
                "approvalPolicy": "on-request", "approvalsReviewer": "user"}
    values = {"initialize": {"userAgent": "Codex Desktop/0.159.0", "platformFamily": "windows", "codexHome": "private-home"},
              "windowsSandbox/readiness": {"status": "ready"}, "thread/start": thread, "thread/resume": thread,
              "thread/fork": lambda params: {**thread(params), "thread": {"id": "thread-fork"}},
              "turn/start": {"turn": {"id": "turn-1", "status": "inProgress"}}, "turn/interrupt": {},
              'config/read': {'config': {'mcp_servers': {'external': {'enabled': True}}}}}
    return FakeRpc({**values, **overrides})


def kimi_rpc(**overrides):
    values = {"initialize": {"protocolVersion": 1, "agentInfo": {"version": "0.29.2"},
                              "agentCapabilities": {"loadSession": True, "sessionCapabilities": {"resume": {}, "list": {}}},
                              "authMethods": [{"env": {"TOKEN": "private-token"}}]},
              "session/new": {"sessionId": "kimi-1"}, "session/resume": {}, "session/load": {},
              "session/prompt": {"stopReason": "end_turn"}}
    return FakeRpc({**values, **overrides})


def test_codex_handshake_and_turn_use_confirmed_policy_without_auth_payload():
    rpc = codex_rpc()
    client = CodexProvider(rpc)
    capabilities = client.initialize()
    assert capabilities["server_version"] == "0.159.0"
    assert capabilities["sandbox_status"] == "ready"
    assert "private-home" not in json.dumps(capabilities)
    assert rpc.notifications == [("initialized", None)]
    created = client.create_session("C:/work", read_only=True)
    assert created["native_session_id"] == "thread-1"
    assert client.start_turn("thread-1", "inspect") == {"native_turn_id": "turn-1", "status": "running"}
    params = rpc.calls[-1][1]
    assert params["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert params["approvalsReviewer"] == "user"
    assert client.cancel("thread-1")["status"] == "cancel_requested"
    assert rpc.calls[-1] == ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"})


def test_workspace_write_turn_excludes_other_writable_roots_and_temp():
    rpc = codex_rpc()
    client = CodexProvider(rpc)
    client.initialize()
    client.create_session("/work", read_only=False)
    client.start_turn("thread-1", "edit")
    assert rpc.calls[-1][1]["sandboxPolicy"] == {"type": "workspaceWrite", "writableRoots": ["/work"],
                                                "networkAccess": False, "excludeSlashTmp": True, "excludeTmpdirEnvVar": True}


def test_session_disables_inherited_external_tools_without_changing_provider_config():
    rpc = codex_rpc()
    client = CodexProvider(rpc)
    client.initialize()
    client.create_session('/work')
    params = next(params for method, params in rpc.calls if method == 'thread/start')
    assert params['config'] == {'mcp_servers': {'external': {'enabled': False}},
                                'features': {'apps': False, 'multi_agent': False}, 'web_search': 'disabled',
                                'sandbox_workspace_write': {'network_access': False, 'exclude_tmpdir_env_var': True,
                                                            'exclude_slash_tmp': True}}
    assert rpc.results['config/read']['config']['mcp_servers']['external']['enabled'] is True


def test_writable_session_cannot_approve_escape_from_verified_directory():
    rpc = codex_rpc()
    events = []
    client = CodexProvider(rpc, events.append)
    client.initialize()
    client.create_session('/work')
    rpc.on_message({'id': 'escape', 'method': 'item/commandExecution/requestApproval',
                    'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'cwd': '/outside'}})
    assert events[-1]['payload']['allowed_decisions'] == ['DENY', 'CANCEL']
    with pytest.raises(ProviderError, match='DECISION_INVALID'):
        client.reply('escape', 'ALLOW_ONCE')
    assert rpc.responses == []
    client.reply('escape', 'DENY')
    assert rpc.responses[-1]['result'] == {'decision': 'decline'}


@pytest.mark.parametrize("changes,reason", [
    ({"sandbox": {"type": "dangerFullAccess"}}, "POLICY_NOT_ACKNOWLEDGED"),
    ({"sandbox": {"type": "workspaceWrite", "writableRoots": ["/outside"]}}, "SCOPE_MISMATCH"),
    ({"sandbox": {"type": "workspaceWrite", "networkAccess": True}}, "SCOPE_MISMATCH"),
    ({"cwd": "/outside"}, "CWD_MISMATCH"),
    ({"approvalsReviewer": "auto_review"}, "POLICY_NOT_ACKNOWLEDGED"),
])
def test_codex_rejects_broader_or_different_policy(changes, reason):
    reply = {"thread": {"id": "thread-1"}, "cwd": "/work", "sandbox": {"type": "workspaceWrite"},
             "approvalPolicy": "on-request", "approvalsReviewer": "user", **changes}
    rpc = codex_rpc(**{"thread/start": reply})
    client = CodexProvider(rpc)
    client.initialize()
    with pytest.raises(ProviderError, match=reason):
        client.create_session("/work")
    with pytest.raises(ProviderError, match="SESSION_NOT_BOUND"):
        client.start_turn("thread-1", "mutate")


def test_unconfigured_windows_cannot_claim_read_only_or_write_support():
    rpc = codex_rpc(**{"windowsSandbox/readiness": {"status": "notConfigured"}})
    client = CodexProvider(rpc)
    capabilities = client.initialize()
    assert not capabilities["write"] and not capabilities["read_only_supported"]
    with pytest.raises(ProviderError, match="SCOPE_UNAVAILABLE"):
        client.create_session("C:/work", read_only=True)
    assert not any(method == "thread/start" for method, _ in rpc.calls)


def test_codex_only_allows_once_and_never_escalates_read_only_session():
    rpc = codex_rpc()
    client = CodexProvider(rpc, lambda event: None)
    client.initialize()
    client.create_session("/work", read_only=True)
    raw = {"id": "approval-1", "method": "item/commandExecution/requestApproval",
           "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "command": "private command"}}
    rpc.on_message(raw)
    with pytest.raises(ProviderError, match="READ_ONLY_ESCALATION"):
        client.reply("approval-1", "ALLOW_ONCE")
    with pytest.raises(ProviderError, match="DECISION_INVALID"):
        client.reply("approval-1", "ALLOW_ALWAYS")
    assert client.reply("approval-1", "DENY")["status"] == "responded"
    assert rpc.responses[-1]["result"] == {"decision": "decline"}
    with pytest.raises(ProviderError, match="NOT_PENDING"):
        client.reply("approval-1", "DENY")


def test_codex_permission_grant_and_unimplemented_tool_callbacks_fail_closed():
    rpc = codex_rpc()
    CodexProvider(rpc)
    rpc.on_message({"id": 99, "method": "item/permissions/requestApproval", "params": {"permissions": {"network": True}}})
    assert rpc.responses == [{"id": 99, "result": None, "error": {"code": -32601, "message": "Unsupported client method"}}]


def test_codex_questions_validate_all_native_question_ids_and_mask_display_secrets():
    rpc = codex_rpc()
    events = []
    client = CodexProvider(rpc, events.append)
    rpc.on_message({"id": 9, "method": "item/tool/requestUserInput", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "questions": [
            {"id": "q1", "header": "Choice", "question": "password=secret12345", "options": [{"label": "A", "description": "First"}]}]}})
    assert events[0]["native_request_id"] == 9
    assert "secret12345" not in json.dumps(events)
    with pytest.raises(ProviderError, match="ANSWER_INVALID"):
        client.reply(9, "ANSWER", {"other": "A"})
    client.reply(9, "ANSWER", {"q1": "A"})
    assert rpc.responses[-1]["result"] == {"answers": {"q1": {"answers": ["A"]}}}


def test_normalization_excludes_raw_reasoning_commands_environment_and_error_details():
    raws = [
        {"method": "item/reasoning/textDelta", "params": {"delta": "private thought"}},
        {"method": "item/completed", "params": {"item": {"type": "reasoning", "text": "private thought"}}},
        {"method": "item/started", "params": {"item": {"id": "x", "type": "commandExecution", "command": "private command", "env": {"TOKEN": "private-token"}}}},
        {"id": 1, "method": "item/commandExecution/requestApproval", "params": {"command": "private command", "environment": {"TOKEN": "private-token"}}},
        {"method": "error", "params": {"error": {"message": "private failure"}, "willRetry": False}},
    ]
    serialized = json.dumps([event for raw in raws for event in normalize("CODEX", raw)])
    for private in ["private thought", "private command", "private-token", "private failure"]:
        assert private not in serialized
    assert normalize("CODEX", {"method": "turn/completed", "params": {"turn": {"status": "completed"}}})[0]["native_turn_id"] is None


def test_kimi_advertises_no_reverse_tools_or_scope_enforcement_and_no_fork():
    rpc = kimi_rpc()
    client = KimiProvider(rpc)
    capabilities = client.initialize()
    assert rpc.calls[0][1]["clientCapabilities"] == {}
    assert capabilities["resume"] and capabilities["load"] and capabilities["list"]
    assert not capabilities["write"] and not capabilities["read_only_supported"] and not capabilities["fork"]
    assert "private-token" not in json.dumps(capabilities)
    with pytest.raises(ProviderError, match="READ_ONLY_UNSUPPORTED"):
        client.create_session("/work", read_only=True)
    with pytest.raises(ProviderError, match="MODE_UNSUPPORTED"):
        client.create_session("/work", native_id="existing", mode="FORK")
    assert client.create_session("/work")["native_session_id"] == "kimi-1"
    assert rpc.calls[-1] == ("session/new", {"cwd": "/work", "mcpServers": []})
    assert client.create_session("/work", native_id="kimi-1", mode="RESUME")["native_session_id"] == "kimi-1"
    assert rpc.calls[-1] == ("session/resume", {"cwd": "/work", "mcpServers": [], "sessionId": "kimi-1"})


def test_kimi_streams_prompt_then_returns_stop_reason_without_inventing_turn_id():
    entered, release = threading.Event(), threading.Event()
    rpc = kimi_rpc()
    def prompt(params):
        entered.set()
        release.wait(2)
        return {"stopReason": "cancelled"}
    rpc.results["session/prompt"] = prompt
    events = []
    client = KimiProvider(rpc, events.append)
    client.initialize()
    client.create_session("/work")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(client.start_turn, "kimi-1", "hello")
        assert entered.wait(1)
        rpc.on_message({"method": "session/update", "params": {"sessionId": "kimi-1", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "partial"}}}})
        client.cancel("kimi-1")
        assert rpc.notifications == [("session/cancel", {"sessionId": "kimi-1"})]
        release.set()
        result = future.result(2)
    assert result == {"native_turn_id": None, "status": "interrupted", "stop_reason": "cancelled"}
    assert [event["kind"] for event in events] == ["turn.started", "output.delta", "turn.completed"]


def test_kimi_permissions_only_select_allow_once_and_reject_once():
    rpc = kimi_rpc()
    client = KimiProvider(rpc, lambda event: None)
    frame = {"id": "permission", "method": "session/request_permission", "params": {"sessionId": "kimi-1", "options": [
        {"optionId": "always", "kind": "allow_always"}, {"optionId": "once", "kind": "allow_once"}, {"optionId": "deny", "kind": "reject_once"}]}}
    rpc.on_message(frame)
    with pytest.raises(ProviderError, match="DECISION_INVALID"):
        client.reply("permission", "ALLOW_ALWAYS")
    client.reply("permission", "ALLOW_ONCE")
    assert rpc.responses[-1]["result"] == {"outcome": {"outcome": "selected", "optionId": "once"}}
    frame["id"] = "deny-permission"
    rpc.on_message(frame)
    client.reply("deny-permission", "DENY")
    assert rpc.responses[-1]["result"]["outcome"]["optionId"] == "deny"


@pytest.mark.parametrize("method", ["fs/read_text_file", "fs/write_text_file", "terminal/create", "terminal/release", "elicitation/create"])
def test_kimi_unadvertised_reverse_fs_terminal_and_questions_are_rejected(method):
    rpc = kimi_rpc()
    KimiProvider(rpc)
    rpc.on_message({"id": 1, "method": method, "params": {"path": "/secret", "command": "private command"}})
    assert rpc.responses[-1]["error"]["code"] == -32601


def test_kimi_replayed_input_and_thoughts_are_not_current_output():
    user = normalize("KIMI_CODE", {"method": "session/update", "params": {"sessionId": "kimi-1", "update": {
        "sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "old input"}}}})
    assert user[0]["kind"] == "input.replayed" and user[0]["payload"]["replay"] is True
    assert normalize("KIMI_CODE", {"method": "session/update", "params": {"update": {
        "sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "private thought"}}}}) == []


def test_long_provider_text_is_split_without_loss_or_silent_truncation():
    text = "A" * 100_000
    events = normalize("CODEX", {"method": "item/agentMessage/delta", "params": {"delta": text}})
    assert "".join(event["payload"]["text"] for event in events) == text
    assert all(len(event["payload"]["text"]) <= 8192 for event in events)
    assert [event["payload"]["chunk_index"] for event in events] == list(range(len(events)))
    assert all(event["payload"]["chunk_count"] == len(events) for event in events)


def test_unix_codex_requires_verified_scope_and_does_not_guess_from_platform():
    initialize = {"userAgent": "codex_cli_rs/0.147.0", "platformFamily": "unix"}
    rpc = codex_rpc(initialize=initialize)
    client = CodexProvider(rpc)
    assert client.initialize()["scope_enforcement"] == "UNAVAILABLE"
    with pytest.raises(ProviderError, match="SCOPE_UNAVAILABLE"):
        client.create_session("/work")
    caller_flag = CodexProvider(codex_rpc(initialize=initialize), scope_verified=True)
    assert caller_flag.initialize()["write"] is False


def test_reply_failure_is_reported_and_cannot_reconsume_native_request():
    rpc = codex_rpc()
    client = CodexProvider(rpc, lambda event: None)
    rpc.on_message({"id": 1, "method": "item/fileChange/requestApproval", "params": {}})
    def failed_response(*args, **kwargs):
        raise ProtocolError("EXECUTION_STDIN_CLOSED")
    rpc.respond = failed_response
    with pytest.raises(ProtocolError, match="STDIN_CLOSED"):
        client.reply(1, {"decision": "DENY"})
    with pytest.raises(ProviderError, match="NOT_PENDING"):
        client.reply(1, "DENY")


def test_replayed_native_approval_id_is_not_offered_to_the_user_twice():
    rpc = codex_rpc()
    events = []
    client = CodexProvider(rpc, events.append)
    frame = {"id": 1, "method": "item/fileChange/requestApproval", "params": {}}
    rpc.on_message(frame)
    client.reply(1, "DENY")
    rpc.on_message(frame)
    assert len(events) == 1
    assert rpc.responses[-1]["error"]["code"] == -32000


def test_kimi_load_history_is_marked_before_new_execution_context():
    rpc = kimi_rpc(initialize={"protocolVersion": 1, "agentCapabilities": {"loadSession": True}})
    events = []
    client = KimiProvider(rpc, events.append)
    client.initialize()
    def load(params):
        rpc.on_message({"method": "session/update", "params": {"sessionId": params["sessionId"], "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "old output"}}}})
        return {}
    rpc.results["session/load"] = load
    created = client.create_session("/work", native_session_id="old", mode="RESUME")
    assert created["history_replayed"] is True
    assert events[0]["kind"] == "output.replayed" and events[0]["payload"]["replay"] is True
    rpc.on_message({"method": "session/update", "params": {"sessionId": "old", "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "new output"}}}})
    assert events[-1]["kind"] == "output.delta" and events[-1]["payload"]["replay"] is False
