"""Real subprocess checks for transport limits and bidirectional RPC."""

import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentguard.execution.protocol import (
    ProtocolError,
    RpcLimits,
    RpcProcess,
    RpcRemoteError,
    RpcTimeout,
)


def launch(program, **kwargs):
    return RpcProcess([sys.executable, "-u", "-c", program], **kwargs)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


def test_responses_correlate_when_received_in_reverse_order():
    program = """
import json, sys
frames = [json.loads(sys.stdin.readline()) for _ in range(2)]
for frame in reversed(frames):
    print(json.dumps({'id':frame['id'],'result':{'method':frame['method']}}), flush=True)
sys.stdin.read()
"""
    with launch(program) as rpc, ThreadPoolExecutor(2) as pool:
        first = pool.submit(rpc.request, "first")
        second = pool.submit(rpc.request, "second")
        assert first.result(3) == {"method": "first"}
        assert second.result(3) == {"method": "second"}


def test_server_callback_can_make_request_and_respond_without_deadlock():
    program = """
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'id':'approval','method':'request_permission','params':{}}), flush=True)
nested = json.loads(sys.stdin.readline())
print(json.dumps({'id':nested['id'],'result':{'ok':True}}), flush=True)
approval = json.loads(sys.stdin.readline())
print(json.dumps({'id':request['id'],'result':approval['result']}), flush=True)
sys.stdin.read()
"""
    received = []
    with launch(program) as rpc:
        def callback(frame):
            assert rpc.request("nested", timeout=2) == {"ok": True}
            rpc.respond(frame["id"], {"accepted": False})
            received.append(frame["id"])
        rpc.on_message = callback
        assert rpc.request("outer", timeout=3) == {"accepted": False}
        rpc.flush_events(timeout=2)
        assert received == ["approval"]


def test_timeout_does_not_replay_request_or_break_later_response_correlation():
    program = """
import json, sys, time
first = json.loads(sys.stdin.readline())
time.sleep(.15)
print(json.dumps({'id':first['id'],'result':{'late':True}}), flush=True)
second = json.loads(sys.stdin.readline())
print(json.dumps({'id':second['id'],'result':{'next_id':second['id']}}), flush=True)
sys.stdin.read()
"""
    with launch(program) as rpc:
        with pytest.raises(RpcTimeout) as failure:
            rpc.request("mutate", timeout=0.05)
        assert failure.value.request_id == 1
        assert rpc.request("observe", timeout=2) == {"next_id": 2}


@pytest.mark.parametrize("program,limits,reason", [
    ("import sys; sys.stdout.write('['); sys.stdout.flush()", RpcLimits(), "EXECUTION_JSONL_TRUNCATED"),
    ("print('not json', flush=True)", RpcLimits(), "EXECUTION_RPC_FRAME_INVALID"),
    ("print('x'*200, flush=True)", RpcLimits(message_bytes=64), "EXECUTION_STDOUT_LIMITED"),
    ("import sys; sys.stderr.write('secret'*100); sys.stderr.flush(); sys.stdin.read()", RpcLimits(stderr_bytes=100), "EXECUTION_STDERR_LIMITED"),
])
def test_malformed_or_excessive_output_closes_own_process(program, limits, reason):
    with launch(program, limits=limits) as rpc:
        wait_for(lambda: rpc.failure_reason is not None)
        assert rpc.failure_reason == reason
        with pytest.raises(ProtocolError, match=reason):
            rpc.request("observe")
        assert "secret" not in str(ProtocolError(reason))


def test_stdin_limit_rejects_before_sending_and_other_requests_still_work():
    program = """
import json, sys
for line in sys.stdin:
    f=json.loads(line)
    print(json.dumps({'id':f['id'],'result':{'ok':True}}),flush=True)
"""
    with launch(program, limits=RpcLimits(stdin_bytes=200)) as rpc:
        with pytest.raises(ProtocolError, match="EXECUTION_STDIN_LIMITED"):
            rpc.request("send", {"text": "x" * 500})
        assert rpc.byte_counts["stdin"] == 0
        assert rpc.request("observe") == {"ok": True}


def test_remote_error_does_not_expose_provider_error_data():
    program = """
import json,sys
f=json.loads(sys.stdin.readline())
print(json.dumps({'id':f['id'],'error':{'code':-1,'message':'password=secret12345','data':{'token':'secret12345'}}}),flush=True)
sys.stdin.read()
"""
    with launch(program) as rpc:
        with pytest.raises(RpcRemoteError) as failure:
            rpc.request("send")
        assert failure.value.error_code == -1
        assert "secret" not in str(failure.value)


def test_callback_backpressure_is_bounded():
    entered, release = threading.Event(), threading.Event()
    def callback(_):
        entered.set()
        release.wait(2)
    program = """
import json,sys,time
time.sleep(.1)
for i in range(30):
    print(json.dumps({'method':'event','params':{'n':i}}),flush=True)
sys.stdin.read()
"""
    rpc = launch(program, on_message=callback, limits=RpcLimits(queued_messages=2))
    try:
        wait_for(lambda: rpc.failure_reason is not None)
        assert rpc.failure_reason == "EXECUTION_EVENT_QUEUE_LIMITED"
    finally:
        release.set()
        rpc.close()


def test_close_only_terminates_the_process_started_by_this_client():
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    rpc = launch("import sys; sys.stdin.read()")
    try:
        rpc.close()
        assert unrelated.poll() is None
        assert not rpc.alive
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_shell_commands_and_nonfinite_timeouts_are_rejected():
    with pytest.raises(ValueError):
        RpcProcess("echo unsafe")
    with launch("import sys; sys.stdin.read()") as rpc:
        with pytest.raises(ValueError):
            rpc.request("read", timeout=float("inf"))
        with pytest.raises(ProtocolError, match="INPUT_INVALID"):
            rpc.notify("read", ["invalid"])


def test_event_barrier_delivers_history_before_caller_changes_context():
    received = []
    program = """
import json,sys
f=json.loads(sys.stdin.readline())
print(json.dumps({'method':'history','params':{'text':'old'}}),flush=True)
print(json.dumps({'id':f['id'],'result':{}}),flush=True)
sys.stdin.read()
"""
    def callback(frame):
        time.sleep(.05)
        received.append(frame["params"]["text"])
    with launch(program, on_message=callback) as rpc:
        rpc.request("load")
        rpc.flush_events(timeout=2)
        assert received == ["old"]


def test_codex_compatibility_omits_jsonrpc_only_on_outbound_frames():
    program = """
import json,sys
f=json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','id':f['id'],'result':{'has_jsonrpc':'jsonrpc' in f}}),flush=True)
sys.stdin.read()
"""
    with launch(program, jsonrpc=False) as rpc:
        assert rpc.request("initialize") == {"has_jsonrpc": False}


def test_stdout_total_budget_is_separate_from_frame_limit():
    program = """
import json,sys
for i in range(5):
    print(json.dumps({'method':'event','params':{'n':i}}),flush=True)
sys.stdin.read()
"""
    with launch(program, limits=RpcLimits(stdout_bytes=70, message_bytes=1024)) as rpc:
        wait_for(lambda: rpc.failure_reason is not None)
        assert rpc.failure_reason == "EXECUTION_STDOUT_LIMITED"


def test_nonfinite_wire_json_is_rejected():
    with launch("print('{\"method\":\"event\",\"params\":{\"number\":NaN}}',flush=True)") as rpc:
        wait_for(lambda: rpc.failure_reason is not None)
        assert rpc.failure_reason == "EXECUTION_RPC_FRAME_INVALID"
