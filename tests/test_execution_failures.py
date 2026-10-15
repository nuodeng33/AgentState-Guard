"""Durable execution failures with real SQLite and controlled provider delivery."""

import json
import os
import re
import signal
import sqlite3
import sys
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentguard.execution.models import (
    CancelRun,
    ConnectSession,
    CreateSession,
    RespondRun,
    StartRun,
)
from agentguard.execution.protocol import RpcProcess
from agentguard.execution.service import CONTROL_RESERVE, RUN_BUDGET
from agentguard.work.service import WorkError
from tests.test_execution_core import Provider, wait_for

pytest_plugins = ("tests.test_execution_core",)


def ready_session(context):
    context["owner"]["wait"] = True
    command = CreateSession(
        request_id=uuid4(), expected_revision=1, control_consent=True,
        **{key: context[key] for key in ("application_id", "store_id", "scope_id")},
    )
    runtime = context["runtime"]
    with runtime.service() as service:
        session_id = service.create_session(context["work_id"], command)["session_id"]
    runtime._connect(session_id)
    with runtime.service() as service:
        session = service.session(context["work_id"], session_id)
    assert session["state"] == "READY", session
    return session


def queue_run(context, session):
    with context["runtime"].service() as service:
        current = service.session(context["work_id"], session["id"])
        command = StartRun(
            request_id=uuid4(), expected_revision=current["revision"],
            requirement_version=1, input="Run the selected work",
        )
        return service.start_run(context["work_id"], session["id"], command)["run_id"]


def read_run(context, session, run_id):
    with context["runtime"].journal() as service:
        return service.run(context["work_id"], session["id"], run_id, "summary")


def read_events(context, run_id, after=0):
    with context["runtime"].journal() as service:
        return service.events(context["work_id"], run_id, after=after, limit=100)


def running_run(context):
    session = ready_session(context)
    run_id = queue_run(context, session)
    runtime = context["runtime"]
    runtime._dispatch(session["id"], run_id)
    assert read_run(context, session, run_id)["state"] == "RUNNING"
    return session, run_id, runtime.channels[session["id"]]


def test_work_execution_summary_is_bounded_isolated_and_does_not_read_output(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentguard.api.execution import execution_router
    from agentguard.work.models import CreateWork

    session, run_id, _ = running_run(setup)
    with setup["runtime"].journal() as service, service.db.transaction() as conn:
        conn.execute("UPDATE work_runs SET state='WAITING_INPUT' WHERE id=?", (run_id,))
    with setup["runtime"].journal() as service:
        other = service.work.create(CreateWork(request_id=uuid4(), title="Other", owner="Owner", requirement="Other requirement"))
    app = FastAPI()
    app.include_router(execution_router(setup["runtime"]))
    with TestClient(app) as client:
        root = '/api/v1/works/' + setup["work_id"] + '/execution-summary'
        result = client.get(root + '?limit=1')
        assert result.status_code == 200, result.text
        value = result.json()
        assert value['counts'] == {'WAITING_INPUT': 1}
        assert value['items'][0]['id'] == run_id
        assert value['items'][0]['session_id'] == session['id']
        assert value['requirement_version'] == 1
        assert value['has_more'] is False
        detail = client.get('/api/v1/works/' + setup['work_id'] + '/sessions/' + session['id']).json()
        assert detail['scope_root'] == str(setup['workspace'].resolve())
        assert 'identity_json' not in detail
        assert client.get('/api/v1/works/' + other['work_id'] + '/execution-summary').json()['items'] == []
        assert client.get('/api/v1/works/' + str(uuid4()) + '/execution-summary').status_code == 404
        assert client.get(root + '?limit=101').status_code == 422
    with setup['runtime'].journal() as service:
        queries = []
        service.conn.set_trace_callback(queries.append)
        service.execution_summary(setup['work_id'])
        assert not any('input_json' in query or 'payload_json' in query or 'BEGIN IMMEDIATE' in query for query in queries)


def test_work_execution_summary_does_not_keep_old_failures_after_a_new_attempt(setup):
    session = ready_session(setup)
    first = queue_run(setup, session)
    with setup['runtime'].journal() as service, service.db.transaction() as conn:
        conn.execute("UPDATE work_runs SET state='FAILED' WHERE id=?", (first,))
    with setup['runtime'].journal() as service:
        assert service.execution_summary(setup['work_id'])['counts'] == {'FAILED': 1}
    second = queue_run(setup, session)
    with setup['runtime'].journal() as service:
        result = service.execution_summary(setup['work_id'])
        assert result['counts'] == {'QUEUED': 1}
        assert [item['id'] for item in result['items']] == [second]


def request_action(context, session, run_id, channel, *, question=False, request_id="request-1"):
    payload = (
        {"questions": [{"id": request_id, "question": "Choose a value", "secret": False}]}
        if question else {"allowed_decisions": ["ALLOW_ONCE", "DENY", "CANCEL"], "summary": "Controlled action"}
    )
    channel["provider"].on_event({
        "kind": "input.requested" if question else "approval.requested",
        "payload": payload, "native_session_id": session["native_session_id"],
        "native_turn_id": "turn-1", "native_request_id": request_id,
    })
    actions = read_run(context, session, run_id)["pending_actions"]
    return next(action for action in actions if action["payload"]["native_request_id"] == request_id)


def response_command(action, response):
    return RespondRun(
        request_id=uuid4(), event_seq=action["event_seq"],
        action_ref=action["action_ref"], response=response,
    )


def persist_response(context, session, run_id, command):
    with context["runtime"].service() as service:
        return service.response(context["work_id"], session["id"], run_id, command)


def test_cancel_before_dispatch_never_sends_a_turn(setup):
    session = ready_session(setup)
    run_id = queue_run(setup, session)
    runtime = setup["runtime"]
    command = CancelRun(request_id=uuid4())
    with runtime.service() as service:
        cancelled = service.cancel(setup["work_id"], session["id"], run_id, command)
        assert service.cancel(setup["work_id"], session["id"], run_id, command) == cancelled
    runtime._dispatch(session["id"], run_id)
    runtime._cancel(session["id"], run_id)
    runtime._cancel(session["id"], run_id)
    run = read_run(setup, session, run_id)
    assert run["state"] == "CANCELLED"
    assert run["dispatch_state"] == "NOT_SENT"
    assert setup["owner"]["sends"] == setup["owner"]["cancels"] == 0
    assert "DISPATCH_INTENT" not in {event["type"] for event in read_events(setup, run_id)["items"]}


def test_cancel_while_dispatch_ack_is_pending_waits_for_native_turn_id(setup, monkeypatch):
    session = ready_session(setup)
    run_id = queue_run(setup, session)
    runtime, owner = setup["runtime"], setup["owner"]
    channel = runtime.channels[session["id"]]
    provider = channel["provider"]
    entered, release = threading.Event(), threading.Event()
    cancel_turns = []
    original_cancel = provider.cancel

    def delayed_start(native_id, text, **kwargs):
        owner["sends"] += 1
        entered.set()
        if not release.wait(5):
            raise RuntimeError("controlled dispatch barrier timed out")
        provider.on_event({
            "kind": "turn.started", "payload": {"status": "running"},
            "native_session_id": native_id, "native_turn_id": "turn-1",
        })
        return {"native_turn_id": "turn-1", "status": "running"}

    def cancel_with_required_turn(native_id, turn_id):
        if turn_id is None:
            raise RuntimeError("EXECUTION_PROVIDER_TURN_ID_MISSING")
        cancel_turns.append(turn_id)
        original_cancel(native_id, turn_id)

    monkeypatch.setattr(provider, "start_turn", delayed_start)
    monkeypatch.setattr(provider, "cancel", cancel_with_required_turn)
    dispatch = threading.Thread(target=runtime._dispatch, args=(session["id"], run_id))
    dispatch.start()
    try:
        assert entered.wait(3)
        with runtime.service() as service:
            service.cancel(setup["work_id"], session["id"], run_id, CancelRun(request_id=uuid4()))
        runtime._cancel(session["id"], run_id)
    finally:
        release.set()
        dispatch.join(5)
    assert not dispatch.is_alive()
    runtime._cancel(session["id"], run_id)
    runtime._cancel(session["id"], run_id)
    run = wait_for(lambda: read_run(setup, session, run_id), lambda row: row["state"] in {"CANCELLED", "UNCERTAIN"})
    assert run["state"] == "CANCELLED", run
    assert cancel_turns == ["turn-1"]
    assert owner["sends"] == owner["cancels"] == 1


@pytest.mark.parametrize("decision", ["DENY", "ALLOW_ONCE"])
def test_approval_is_consumed_once_and_receipt_replay_does_not_reply_twice(setup, decision):
    session, run_id, channel = running_run(setup)
    action = request_action(setup, session, run_id, channel)
    command = response_command(action, {"decision": decision})
    receipt = persist_response(setup, session, run_id, command)
    assert persist_response(setup, session, run_id, command) == receipt
    runtime = setup["runtime"]
    runtime.send_response(session["id"], run_id, command)
    runtime.send_response(session["id"], run_id, command)
    assert persist_response(setup, session, run_id, command) == receipt
    with pytest.raises(WorkError, match="RUN_ACTION_(CONSUMED|NOT_CURRENT)"):
        persist_response(setup, session, run_id, response_command(action, {"decision": decision}))
    assert setup["owner"]["replies"] == 1
    events = read_events(setup, run_id)["items"]
    assert sum(event["type"] == "RESPONSE_INTENT" for event in events) == 1
    assert sum(event["type"] == "RESPONSE_DISPATCH_INTENT" for event in events) == 1
    assert sum(event["type"] == "RESPONSE_SENT" for event in events) == 1


def test_answering_one_of_two_questions_keeps_the_run_waiting(setup):
    session, run_id, channel = running_run(setup)
    first = request_action(setup, session, run_id, channel, question=True, request_id="question-1")
    second = request_action(setup, session, run_id, channel, question=True, request_id="question-2")
    command = response_command(first, {"answers": {"question-1": ["first answer"]}})
    persist_response(setup, session, run_id, command)
    setup["runtime"].send_response(session["id"], run_id, command)
    run = read_run(setup, session, run_id)
    assert run["state"] == "WAITING_INPUT", run
    assert [action["event_seq"] for action in run["pending_actions"]] == [second["event_seq"]]
    second_command = response_command(second, {"answers": {"question-2": ["second answer"]}})
    persist_response(setup, session, run_id, second_command)
    setup["runtime"].send_response(session["id"], run_id, second_command)
    assert read_run(setup, session, run_id)["state"] == "RUNNING"
    assert setup["owner"]["replies"] == 2


def test_action_from_an_old_run_generation_is_rejected(setup):
    session, run_id, channel = running_run(setup)
    action = request_action(setup, session, run_id, channel)
    with setup["runtime"].journal() as service, service.db.transaction() as conn:
        conn.execute("UPDATE work_runs SET generation=? WHERE id=?", (str(uuid4()), run_id))
    with pytest.raises(WorkError, match="RUN_ACTION_MISMATCH"):
        persist_response(setup, session, run_id, response_command(action, {"decision": "ALLOW_ONCE"}))
    assert setup["owner"]["replies"] == 0
    assert not any(event["type"] == "RESPONSE_INTENT" for event in read_events(setup, run_id)["items"])


@pytest.mark.parametrize("replacement", ["generation", "directory"])
def test_response_revalidates_connection_and_directory_before_delivery(setup, replacement):
    session, run_id, channel = running_run(setup)
    action = request_action(setup, session, run_id, channel)
    command = response_command(action, {"decision": "ALLOW_ONCE"})
    persist_response(setup, session, run_id, command)
    if replacement == "generation":
        channel["generation"] = str(uuid4())
    else:
        workspace = setup["workspace"]
        workspace.rename(workspace.with_name("replaced-project"))
        workspace.mkdir()
    setup["runtime"].send_response(session["id"], run_id, command)
    assert setup["owner"]["replies"] == 0
    assert read_run(setup, session, run_id)["state"] == "UNCERTAIN"
    assert not any(event["type"] == "RESPONSE_SENT" for event in read_events(setup, run_id)["items"])


def test_directory_replacement_rejects_new_response_intent(setup):
    session, run_id, channel = running_run(setup)
    action = request_action(setup, session, run_id, channel)
    workspace = setup["workspace"]
    workspace.rename(workspace.with_name("replaced-project"))
    workspace.mkdir()
    with pytest.raises(WorkError, match="WORKSPACE_ROOT_IDENTITY_CHANGED"):
        persist_response(setup, session, run_id, response_command(action, {"decision": "ALLOW_ONCE"}))
    assert setup["owner"]["replies"] == 0
    assert not any(event["type"] == "RESPONSE_INTENT" for event in read_events(setup, run_id)["items"])


def test_control_events_and_terminal_result_survive_text_truncation(setup):
    session, run_id, channel = running_run(setup)
    runtime = setup["runtime"]
    with runtime.journal() as service, service.db.transaction() as conn:
        conn.execute("UPDATE work_runs SET journal_bytes=? WHERE id=?", (RUN_BUDGET - CONTROL_RESERVE - 32, run_id))
    for _ in range(2):
        channel["provider"].on_event({
            "kind": "output.delta", "payload": {"text": "repeated output", "item_id": "output"},
            "native_session_id": session["native_session_id"], "native_turn_id": "turn-1",
        })
        runtime._flush(channel)
    action = request_action(setup, session, run_id, channel)
    command = response_command(action, {"decision": "DENY"})
    persist_response(setup, session, run_id, command)
    runtime.send_response(session["id"], run_id, command)
    with runtime.service() as service:
        service.cancel(setup["work_id"], session["id"], run_id, CancelRun(request_id=uuid4()))
    runtime._cancel(session["id"], run_id)
    run = wait_for(lambda: read_run(setup, session, run_id), lambda row: row["state"] == "CANCELLED")
    assert run["output_truncated"]
    event_types = [event["type"] for event in read_events(setup, run_id)["items"]]
    assert event_types.count("OUTPUT_TRUNCATED") == 1
    assert {"APPROVAL", "RESPONSE_INTENT", "RESPONSE_SENT", "CANCEL_REQUESTED", "CANCEL_SENT",
            "PROVIDER_RESULT", "RUN_COMPLETED"} <= set(event_types)
    assert "TEXT" not in event_types


@pytest.mark.parametrize("failure", ["source", "provider"])
def test_disconnect_cannot_make_a_running_turn_look_successful(setup, monkeypatch, failure):
    session, run_id, channel = running_run(setup)
    channel["provider"].on_event({
        "kind": "output.delta", "payload": {"text": "Last observed output", "item_id": "last-output"},
        "native_session_id": session["native_session_id"], "native_turn_id": "turn-1",
    })
    if failure == "source":
        monkeypatch.setattr(setup["runtime"].domains, "_bound", lambda binding: ({"connected": False}, None))
    else:
        channel["provider"].alive = False
    setup["runtime"]._check_channels()
    run = read_run(setup, session, run_id)
    assert run["state"] == "UNCERTAIN"
    assert run["completed_at"] is None
    assert run["result"] == {}
    texts = [event["payload"]["text"] for event in read_events(setup, run_id)["items"] if event["type"] == "TEXT"]
    assert texts == ["Last observed output"]
    with setup["runtime"].journal() as service:
        assert service.session(setup["work_id"], session["id"])["state"] == "DISCONNECTED"
    assert session["id"] not in setup["runtime"].channels


def test_killed_transport_preserves_observed_output_and_never_resends_on_restart(setup):
    session, run_id, channel = running_run(setup)
    runtime = setup["runtime"]
    program = """
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'method':'output', 'params':{'text':'Output before process death'}}), flush=True)
print(json.dumps({'id':request['id'], 'result':{}}), flush=True)
sys.stdin.read()
"""
    def observed(frame):
        if frame.get("method") == "output":
            channel["provider"].on_event({
                "kind": "output.delta", "payload": {**frame["params"], "item_id": "killed-output"},
                "native_session_id": session["native_session_id"], "native_turn_id": "turn-1",
            })

    # This is a real disposable transport process, not a model/provider invocation.
    with RpcProcess([sys.executable, "-u", "-c", program], on_message=observed) as rpc:
        channel["provider"].rpc = rpc
        rpc.request("observe", timeout=5)
        rpc.flush_events(timeout=5)
        assert channel["text"] == ["Output before process death"]
        os.kill(rpc.pid, signal.SIGTERM)
        wait_for(lambda: rpc.alive, lambda alive: not alive)
        runtime._check_channels()

    runtime._startup_reconcile()
    row = read_run(setup, session, run_id)
    assert row["state"] == "UNCERTAIN"
    assert row["completed_at"] is None
    events = read_events(setup, run_id)["items"]
    assert [event["payload"]["text"] for event in events if event["type"] == "TEXT"] == ["Output before process death"]
    assert not any(event["type"] == "RUN_COMPLETED" for event in events)
    assert setup["owner"]["sends"] == 1
    assert session["id"] not in runtime.channels


def test_disconnect_keeps_buffer_attached_until_output_persistence_recovers(setup, monkeypatch):
    session, run_id, channel = running_run(setup)
    runtime = setup["runtime"]
    channel["provider"].on_event({
        "kind": "output.delta", "payload": {"text": "Buffered output", "item_id": "last"},
        "native_session_id": session["native_session_id"], "native_turn_id": "turn-1",
    })
    channel["provider"].alive = False
    original_append = runtime._flush
    with monkeypatch.context() as patch:
        def unavailable(_channel):
            raise sqlite3.OperationalError("temporary storage failure")
        patch.setattr(runtime, "_flush", unavailable)
        with pytest.raises(sqlite3.OperationalError):
            runtime._check_channels()
    assert runtime.channels[session["id"]] is channel
    assert channel["text"] == ["Buffered output"]
    runtime._check_channels()
    assert runtime._flush == original_append
    assert session["id"] not in runtime.channels
    assert read_run(setup, session, run_id)["state"] == "UNCERTAIN"
    texts = [event["payload"]["text"] for event in read_events(setup, run_id)["items"] if event["type"] == "TEXT"]
    assert texts == ["Buffered output"]


@pytest.mark.parametrize("status,terminal_state", [
    ("completed", "COMPLETED"), ("interrupted", "CANCELLED"), ("failed", "FAILED"),
])
def test_restart_finishes_durable_provider_results_without_resending(setup, status, terminal_state):
    session = ready_session(setup)
    run_id = queue_run(setup, session)
    runtime = setup["runtime"]
    runtime.close_session(session["id"])
    with runtime.journal() as service, service.db.transaction() as conn:
        service.append(conn, run_id, "PROVIDER_RESULT", {"status": status})
        conn.execute(
            "UPDATE work_runs SET state='FINALIZING',dispatch_state='ACCEPTED',native_turn_id='turn-1',result_json=? WHERE id=?",
            (json.dumps({"status": status}), run_id),
        )
    runtime._startup_reconcile()
    current = read_run(setup, session, run_id)
    assert current["state"] in {"FINALIZING", terminal_state}, current
    runtime._tick()
    run = wait_for(lambda: read_run(setup, session, run_id), lambda row: row["state"] == terminal_state)
    assert run["result"]["status"] == status
    assert run["file_result"]["status"] == "AVAILABLE"
    assert setup["owner"]["sends"] == 0


@pytest.mark.parametrize("state,dispatch_state,expected_state", [
    ("QUEUED", "NOT_SENT", "INTERRUPTED"),
    ("RUNNING", "ACCEPTED", "UNCERTAIN"),
    ("DISPATCHING", "SENDING", "UNCERTAIN"),
])
def test_restart_marks_unsent_or_accepted_runs_without_blind_resend(setup, state, dispatch_state, expected_state):
    session = ready_session(setup)
    run_id = queue_run(setup, session)
    runtime = setup["runtime"]
    runtime.close_session(session["id"])
    with runtime.journal() as service, service.db.transaction() as conn:
        conn.execute("UPDATE work_runs SET state=?,dispatch_state=? WHERE id=?", (state, dispatch_state, run_id))
    runtime._startup_reconcile()
    runtime._tick()
    run = read_run(setup, session, run_id)
    assert run["state"] == expected_state
    assert run["dispatch_state"] == dispatch_state
    assert (run["completed_at"] is not None) == (expected_state == "INTERRUPTED")
    assert setup["owner"]["sends"] == 0


def test_summary_queries_neither_select_nor_decode_full_input_snapshots(setup):
    session = ready_session(setup)
    runtime = setup["runtime"]
    run_ids = []
    for _ in range(3):
        run_id = queue_run(setup, session)
        run_ids.append(run_id)
        with runtime.journal() as service, service.db.transaction() as conn:
            conn.execute("UPDATE work_runs SET state='COMPLETED' WHERE id=?", (run_id,))
    statements = []

    def deny_input_reads(operation, table, column, database, source):
        return sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_READ and table == "work_runs" and column == "input_json" else sqlite3.SQLITE_OK

    with runtime.journal() as service:
        service.conn.set_authorizer(deny_input_reads)
        service.conn.set_trace_callback(statements.append)
        try:
            single = service.run(setup["work_id"], session["id"], run_ids[0], "summary")
            page = service.runs(setup["work_id"], session["id"], limit=2, view="summary")
        finally:
            service.conn.set_trace_callback(None)
        with pytest.raises(sqlite3.DatabaseError):
            service.conn.execute("SELECT input_json FROM work_runs WHERE id=?", (run_ids[0],))
        service.conn.set_authorizer(None)
    assert page["has_more"]
    assert len(page["items"]) == 2
    assert all("input" not in row and "input_json" not in row for row in [single, *page["items"]])
    selections = [statement for statement in statements if re.search(r"SELECT\b.*\bFROM\s+work_runs\b", statement, re.IGNORECASE | re.DOTALL)]
    assert len(selections) == 2
    assert all("input_json" not in statement.lower() and "select *" not in statement.lower() for statement in selections)


def test_event_cursor_reconnection_keeps_identical_output_tokens(setup):
    session, run_id, channel = running_run(setup)
    event = {
        "kind": "output.delta", "payload": {"text": "same", "item_id": "output-1"},
        "native_session_id": session["native_session_id"], "native_turn_id": "turn-1",
    }
    channel["provider"].on_event(event)
    setup["runtime"]._flush(channel)
    first = read_events(setup, run_id)
    cursor = first["last_seq"]
    channel["provider"].on_event(event)
    setup["runtime"]._flush(channel)
    reconnected = read_events(setup, run_id, after=cursor)
    assert read_events(setup, run_id, after=cursor) == reconnected
    text_events = [row for row in reconnected["items"] if row["type"] == "TEXT"]
    assert len(text_events) == 1
    assert text_events[0]["seq"] > cursor
    assert text_events[0]["payload"]["text"] == "same"
    all_text = [row for row in read_events(setup, run_id)["items"] if row["type"] == "TEXT"]
    assert len({row["seq"] for row in all_text}) == 2
    assert "".join(row["payload"]["text"] for row in all_text) == "samesame"


def disconnected_session(context):
    session = ready_session(context)
    runtime = context["runtime"]
    runtime.close_session(session["id"])
    with runtime.journal() as service:
        service.session_state(session["id"], "DISCONNECTED", "CONTROLLED_DISCONNECT")
        return service.session(context["work_id"], session["id"])


def uncertain_run(context):
    session, run_id, channel = running_run(context)
    context["runtime"]._fail_run(run_id, "UNCERTAIN", "NATIVE_STATUS_UNKNOWN")
    return session, run_id, channel


def native_read_responses(monkeypatch, channel, responses):
    pending = list(responses)
    calls = []

    def request(method, params, *, timeout):
        calls.append((method, dict(params)))
        assert pending, "Unexpected extra native status request"
        response = pending.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(channel["provider"].rpc, "request", request, raising=False)
    return calls


def assert_writer_reserved(context, session):
    with context["runtime"].service() as service:
        current = service.session(context["work_id"], session["id"])
        with pytest.raises(WorkError, match="EXECUTION_WRITER_BUSY"):
            service.start_run(context["work_id"], session["id"], StartRun(
                request_id=uuid4(), expected_revision=current["revision"],
                requirement_version=1, input="Another run must remain blocked",
            ))


def test_connect_with_consent_resumes_exact_native_session_once(setup, monkeypatch):
    session = disconnected_session(setup)
    runtime, owner = setup["runtime"], setup["owner"]
    resumes = []

    def factory(saved_session, on_event):
        provider = Provider(saved_session, on_event, owner)

        def resume(cwd, **kwargs):
            resumes.append((cwd, kwargs))
            assert kwargs["mode"] == "RESUME"
            assert kwargs["native_session_id"] == session["native_session_id"]
            return {"native_session_id": kwargs["native_session_id"]}

        provider.create_session = resume
        return provider

    monkeypatch.setattr(runtime, "provider_factory", factory)
    command = ConnectSession(request_id=uuid4(), expected_revision=session["revision"], control_consent=True)
    with runtime.service() as service:
        receipt = service.connect(setup["work_id"], session["id"], command)
        assert service.connect(setup["work_id"], session["id"], command) == receipt
    runtime._connect(session["id"])
    runtime._connect(session["id"])
    with runtime.service() as service:
        current = service.session(setup["work_id"], session["id"])
        assert service.connect(setup["work_id"], session["id"], command) == receipt
        private = service.one("work_sessions", session["id"])
    assert current["state"] == "READY"
    assert current["native_session_id"] == session["native_session_id"]
    assert current["generation"] != session["generation"]
    assert len(resumes) == 1
    assert owner["creates"] == 1
    assert json.loads(private["authorization_json"])["request_id"] == str(command.request_id)


@pytest.mark.parametrize("consent", [None, False])
def test_connect_requires_explicit_true_consent(setup, consent):
    session = disconnected_session(setup)
    values = {"request_id": uuid4(), "expected_revision": session["revision"]}
    if consent is not None:
        values["control_consent"] = consent
    with pytest.raises(ValidationError):
        ConnectSession(**values)
    with setup["runtime"].journal() as service:
        assert service.session(setup["work_id"], session["id"])["state"] == "DISCONNECTED"
    assert setup["owner"]["creates"] == 1


def test_connect_rejects_stale_session_revision(setup):
    session = disconnected_session(setup)
    command = ConnectSession(request_id=uuid4(), expected_revision=session["revision"] - 1, control_consent=True)
    with setup["runtime"].service() as service:
        with pytest.raises(WorkError, match="SESSION_REVISION_CONFLICT"):
            service.connect(setup["work_id"], session["id"], command)
        current = service.session(setup["work_id"], session["id"])
    assert current["state"] == "DISCONNECTED"
    assert current["revision"] == session["revision"]
    assert setup["owner"]["creates"] == 1


@pytest.mark.parametrize("replacement", ["directory", "program"])
def test_connect_rejects_changed_execution_identity(setup, replacement):
    session = disconnected_session(setup)
    if replacement == "directory":
        workspace = setup["workspace"]
        workspace.rename(workspace.with_name("original-project"))
        workspace.mkdir()
        reason = "WORKSPACE_ROOT_IDENTITY_CHANGED"
    else:
        with setup["runtime"].journal() as service, service.db.transaction() as conn:
            conn.execute(
                "UPDATE discovery_applications SET metadata_json=json_set(metadata_json,'$.identity','changed') WHERE id=?",
                (setup["application_id"],),
            )
        reason = "EXECUTION_PROGRAM_IDENTITY_CHANGED"
    command = ConnectSession(request_id=uuid4(), expected_revision=session["revision"], control_consent=True)
    with setup["runtime"].service() as service, pytest.raises(WorkError, match=reason):
        service.connect(setup["work_id"], session["id"], command)
    assert setup["owner"]["creates"] == 1
    assert session["id"] not in setup["runtime"].channels


def test_connect_does_not_retry_uncertain_creation_without_native_session_id(setup):
    runtime = setup["runtime"]
    create = CreateSession(
        request_id=uuid4(), expected_revision=1, control_consent=True,
        **{key: setup[key] for key in ("application_id", "store_id", "scope_id")},
    )
    with runtime.service() as service:
        session_id = service.create_session(setup["work_id"], create)["session_id"]
        service.session_state(session_id, "UNCERTAIN", "CREATE_ACK_LOST")
        session = service.session(setup["work_id"], session_id)
        command = ConnectSession(request_id=uuid4(), expected_revision=session["revision"], control_consent=True)
        with pytest.raises(WorkError, match="SESSION_RECONCILIATION_REQUIRED"):
            service.connect(setup["work_id"], session_id, command)
    runtime._connect(session_id)
    assert session["native_session_id"] is None
    assert setup["owner"]["creates"] == 0
    assert session_id not in runtime.channels


@pytest.mark.parametrize("status,terminal_state", [
    ("completed", "COMPLETED"), ("interrupted", "CANCELLED"), ("failed", "FAILED"),
])
def test_reconcile_follows_pages_and_matches_exact_turn_without_replay(setup, monkeypatch, status, terminal_state):
    session, run_id, channel = uncertain_run(setup)
    calls = native_read_responses(monkeypatch, channel, [
        {"data": [{"id": "another-turn", "status": "completed"}], "nextCursor": "opaque-next-page"},
        {"data": [{"id": "turn-1", "status": status, "items": [{"text": "Historical output must not replay"}]}], "nextCursor": None},
    ])
    runtime = setup["runtime"]
    command = CancelRun(request_id=uuid4(), expected_revision=read_run(setup, session, run_id)["revision"])
    receipt = runtime.reconcile(setup["work_id"], run_id, command)
    assert receipt["native_status"] == status
    assert runtime.reconcile(setup["work_id"], run_id, command) == receipt
    run = wait_for(lambda: read_run(setup, session, run_id), lambda row: row["state"] == terminal_state)
    assert run["file_result"]["status"] == "AVAILABLE"
    assert runtime.reconcile(setup["work_id"], run_id, command) == receipt
    assert len(calls) == 2
    assert [call[0] for call in calls] == ["thread/turns/list", "thread/turns/list"]
    assert "cursor" not in calls[0][1]
    assert calls[1][1]["cursor"] == "opaque-next-page"
    assert all(params["threadId"] == session["native_session_id"] and params["itemsView"] == "notLoaded" for _, params in calls)
    events = read_events(setup, run_id)["items"]
    assert sum(event["type"] == "RECONCILIATION" for event in events) == 1
    assert not any(event["type"] in {"TEXT", "HISTORY"} for event in events)
    assert setup["owner"]["sends"] == 1


@pytest.mark.parametrize("status", ["inProgress", "unknown", None])
def test_reconcile_nonterminal_exact_turn_cannot_release_writer(setup, monkeypatch, status):
    session, run_id, channel = uncertain_run(setup)
    native_read_responses(monkeypatch, channel, [{
        "data": [{"id": "another-turn", "status": "completed"}, {"id": "turn-1", "status": status}],
        "nextCursor": None,
    }])
    command = CancelRun(request_id=uuid4(), expected_revision=read_run(setup, session, run_id)["revision"])
    receipt = setup["runtime"].reconcile(setup["work_id"], run_id, command)
    assert receipt["state"] == "UNCERTAIN"
    assert read_run(setup, session, run_id)["state"] == "UNCERTAIN"
    assert_writer_reserved(setup, session)
    assert setup["owner"]["sends"] == 1


def test_reconcile_does_not_use_an_unrelated_completed_turn(setup, monkeypatch):
    session, run_id, channel = uncertain_run(setup)
    calls = native_read_responses(monkeypatch, channel, [
        {"data": [{"id": "other-1", "status": "completed"}], "nextCursor": "page-2"},
        {"data": [{"id": "other-2", "status": "completed"}], "nextCursor": None},
    ])
    command = CancelRun(request_id=uuid4(), expected_revision=read_run(setup, session, run_id)["revision"])
    receipt = setup["runtime"].reconcile(setup["work_id"], run_id, command)
    assert receipt["state"] == "UNCERTAIN"
    assert receipt["native_status"] == "unknown"
    assert len(calls) == 2
    assert_writer_reserved(setup, session)


def test_reconcile_without_native_turn_id_never_queries_or_releases_writer(setup, monkeypatch):
    session, run_id, channel = uncertain_run(setup)
    with setup["runtime"].journal() as service, service.db.transaction() as conn:
        conn.execute("UPDATE work_runs SET native_turn_id=NULL WHERE id=?", (run_id,))
    calls = native_read_responses(monkeypatch, channel, [])
    with pytest.raises(WorkError, match="RUN_RECONCILIATION_UNAVAILABLE"):
        setup["runtime"].reconcile(setup["work_id"], run_id, CancelRun(request_id=uuid4()))
    assert calls == []
    assert_writer_reserved(setup, session)


def test_reconcile_bounds_pagination_and_keeps_unknown_writer_reserved(setup, monkeypatch):
    session, run_id, channel = uncertain_run(setup)
    page = {"data": [{"id": "another-turn", "status": "completed"}], "nextCursor": "repeating-cursor"}
    calls = native_read_responses(monkeypatch, channel, [page] * 10)
    receipt = setup["runtime"].reconcile(setup["work_id"], run_id, CancelRun(request_id=uuid4()))
    assert receipt["state"] == "UNCERTAIN"
    assert 1 <= len(calls) <= 10
    assert_writer_reserved(setup, session)


def test_reconcile_thread_read_fallback_still_matches_exact_turn(setup, monkeypatch):
    session, run_id, channel = uncertain_run(setup)
    calls = native_read_responses(monkeypatch, channel, [
        RuntimeError("method unavailable"),
        {"thread": {"id": session["native_session_id"], "turns": [
            {"id": "other-turn", "status": "completed"}, {"id": "turn-1", "status": "inProgress"},
        ]}},
    ])
    receipt = setup["runtime"].reconcile(setup["work_id"], run_id, CancelRun(request_id=uuid4()))
    assert receipt["state"] == "UNCERTAIN"
    assert calls[1] == ("thread/read", {"threadId": session["native_session_id"], "includeTurns": True})
    assert_writer_reserved(setup, session)


def test_sqlite_startup_journal_failure_is_caught_and_does_not_accept_requests(setup, monkeypatch):
    runtime = setup["runtime"]
    thread_errors = []

    def broken_journal():
        raise sqlite3.OperationalError("PRIVATE_STARTUP_DATABASE_DETAIL")

    monkeypatch.setattr(runtime, "journal", broken_journal)
    monkeypatch.setattr(threading, "excepthook", thread_errors.append)
    runtime.start()
    runtime.thread.join(3)
    assert not runtime.thread.is_alive()
    assert thread_errors == []
    assert runtime.accepting is False
    assert runtime.last_error == "EXECUTION_WORKER_UNAVAILABLE"
    assert "PRIVATE_STARTUP_DATABASE_DETAIL" not in runtime.last_error
    assert setup["owner"]["creates"] == setup["owner"]["sends"] == 0
