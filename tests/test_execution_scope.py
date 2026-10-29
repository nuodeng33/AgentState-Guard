"""Server-issued Unix scope receipts and independent fail-closed OS witnesses."""
import errno
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from agentguard.execution import scope_probe
from agentguard.execution.scope_readiness import (
    CHECKS,
    SCHEMA,
    ScopeReadinessError,
    digest,
    issue_proof,
    profile_arguments,
    profiles,
)


@pytest.fixture
def selection():
    identity = {"application": "program-identity", "entry_path": "/usr/bin/codex",
                "store": "store-identity", "store_path": "/home/user/store", "scope": "scope-digest",
                "binding": {"source_id": "wsl-test", "kind": "WSL", "root": "/work",
                            "identity": {"root": {"dev": 1, "ino": 2}, "guid": "guid", "disk_identity": "disk"}, "epoch": "epoch"}}
    source = {"source_id": "wsl-test", "kind": "WSL", "guid": "guid", "disk_identity": "disk",
              "epoch": "epoch", "connected": True, "running": True}
    nonce = "a" * 32
    launch = {"argv": ["wsl.exe", "--exec", "/usr/bin/codex", "app-server", *profile_arguments(nonce)]}
    report = {"schema_version": SCHEMA, "challenge": nonce, "version": "0.164.0-alpha.11.1",
              "root_identity": {"dev": 1, "ino": 2}, "profile_digest": digest(profiles(nonce)),
              "cleanup": True, "checks": dict.fromkeys(CHECKS, True)}
    return identity, source, launch, report, nonce


def test_issued_proof_binds_identity_launch_full_version_and_fixed_policy(selection):
    identity, source, launch, _report, nonce = selection
    proof = issue_proof(*selection)
    assert proof.matches(identity, launch)
    assert proof.version == "0.164.0-alpha.11.1"
    assert proof.source_digest == digest(source)
    assert not proof.matches({**identity, "store": "replaced"}, launch)
    assert not proof.matches(identity, {"argv": launch["argv"] + ["--other"]})
    assert scope_probe._profiles(nonce) == profiles(nonce)
    configuration = {"default_permissions": "asg_ro_" + nonce, "permissions": profiles(nonce), "features": dict.fromkeys(
        ("apps", "multi_agent", "remote_control", "remote_plugin"), False), "web_search": "disabled"}
    proof.validate_configuration(configuration)
    configuration["permissions"][proof.profile(False)]["filesystem"]["/outside"] = "write"
    with pytest.raises(ScopeReadinessError, match="PROFILE_UNVERIFIED"):
        proof.validate_configuration(configuration)


@pytest.mark.parametrize("field", ["cleanup", *sorted(CHECKS)])
def test_any_failed_canary_or_cleanup_cannot_issue_proof(selection, field):
    identity, source, launch, report, nonce = selection
    if field == "cleanup":
        report[field] = False
    else:
        report["checks"][field] = False
    with pytest.raises(ScopeReadinessError, match="PROOF_INVALID"):
        issue_proof(identity, source, launch, report, nonce)


def test_receipt_cannot_be_replayed_for_another_root_or_nonce(selection):
    identity, source, launch, report, nonce = selection
    for changed in ({**report, "challenge": "b" * 32}, {**report, "root_identity": {"dev": 1, "ino": 99}},
                    {**report, "profile_digest": "0" * 64}):
        with pytest.raises(ScopeReadinessError):
            issue_proof(identity, source, launch, changed, nonce)


@pytest.mark.skipif(os.name != "posix", reason="fixed helper executes inside WSL")
@pytest.mark.parametrize("attack", ["none", "outside-write", "network-connect", "forged-denial", "foreign-file", "generated-cache"])
def test_probe_independently_checks_bytes_network_and_owned_cleanup(tmp_path, monkeypatch, attack):
    root = tmp_path / "project"; root.mkdir()
    scratch = tmp_path / "scratch"; scratch.mkdir()
    monkeypatch.setattr(scope_probe.tempfile, "gettempdir", lambda: str(scratch))
    challenge = "b" * 32
    victim = tmp_path / "preexisting-user-file"
    victim.write_bytes(b"preserve original user bytes")
    observed_environments = []

    def controlled_codex(argv, environment, timeout=10):
        observed_environments.append(environment)
        assert set(environment) == {"PATH", "CODEX_HOME", "HOME", "LANG", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
        if argv[1] == "--version":
            return "codex-cli 0.164.0-alpha.11.1\n"
        inside, outside, nonce, port, mode = argv[-5:]
        result = {"challenge": nonce, "inside": {"allowed": mode == "write", "errno": errno.EROFS},
                  "outside": {"allowed": False, "errno": errno.EROFS},
                  "network": {"allowed": False, "errno": errno.EPERM}}
        if mode == "write":
            Path(inside).write_bytes(nonce.encode())
        if attack in {"outside-write", "forged-denial"}:
            Path(outside).write_bytes(b"outside overwritten despite reported denial")
        if attack == "network-connect":
            import socket
            connection = socket.create_connection(("127.0.0.1", int(port)), timeout=1)
            connection.close()
        if attack == "foreign-file":
            (Path(outside).parent / "not-created-by-probe").write_bytes(b"preserve foreign bytes")
        if attack == "generated-cache" and mode == "write":
            cache = Path(environment["CODEX_HOME"]) / "codex-bwrap-synthetic-mount-targets"
            cache.mkdir()
            (cache / "lock").write_bytes(b"")
            (cache / "outside-directory-link").symlink_to(tmp_path, target_is_directory=True)
            os.link(victim, cache / "outside-hardlink")
        return json.dumps(result)

    monkeypatch.setattr(scope_probe, "_command", controlled_codex)
    info = root.stat()
    request = {"program": str(Path(sys.executable).resolve()), "root": str(root),
               "root_identity": {"dev": info.st_dev, "ino": info.st_ino}, "challenge": challenge}
    if attack in {"none", "generated-cache"}:
        report = scope_probe.probe(request)
        assert report["cleanup"] is True
        assert report["checks"] == dict.fromkeys(CHECKS, True)
        assert report["profile_digest"] == digest(profiles(challenge))
        assert list(scratch.iterdir()) == []
        assert list(root.iterdir()) == []
    else:
        with pytest.raises(scope_probe.ProbeError) as error:
            scope_probe.probe(request)
        assert error.value.reason_code == ("EXECUTION_SCOPE_CLEANUP_FAILED" if attack == "foreign-file"
                                           else "EXECUTION_SCOPE_CANARY_INVALID")
        if attack == "foreign-file":
            assert next(scratch.rglob("not-created-by-probe")).read_bytes() == b"preserve foreign bytes"
        else:
            assert list(scratch.iterdir()) == []
    assert victim.read_bytes() == b"preserve original user bytes"
    assert victim.stat().st_nlink == 1
    assert observed_environments


from agentguard.execution.protocol import ProtocolError
from agentguard.execution.providers import CodexProvider, ProviderError
from tests.test_execution_providers import FakeRpc


def _rpc_for_proof(proof):
    def thread(params):
        read_only = params["permissions"] == proof.profile(True)
        sandbox = {"type": "readOnly" if read_only else "workspaceWrite", "networkAccess": False,
                   "writableRoots": []}
        if not read_only:
            sandbox.update(excludeSlashTmp=True, excludeTmpdirEnvVar=True)
        return {"thread": {"id": params.get("threadId", "thread-1")}, "cwd": params["cwd"],
                "sandbox": sandbox, "approvalPolicy": "on-request", "approvalsReviewer": "user",
                "activePermissionProfile": {"id": params["permissions"], "extends": ":read-only"},
                "runtimeWorkspaceRoots": params["runtimeWorkspaceRoots"]}
    return FakeRpc({"initialize": {"userAgent": "asg/" + proof.version, "platformFamily": "unix", "platformOs": "linux"},
                    "config/read": {"config": {"default_permissions": proof.profile(True), "permissions": profiles(proof.challenge),
                        "features": dict.fromkeys(("apps", "multi_agent", "remote_control", "remote_plugin"), False),
                        "web_search": "disabled", "mcp_servers": {"foreign": {"enabled": True}}}},
                    "thread/start": thread, "thread/resume": thread,
                    "thread/fork": lambda params: {**thread(params), "thread": {"id": "fork-1"}},
                    "turn/start": {"turn": {"id": "turn-1", "status": "inProgress"}}})


def test_boolean_readiness_never_grants_unix_authority():
    rpc = FakeRpc({"initialize": {"userAgent": "asg/0.164.0-alpha.11.1", "platformFamily": "unix", "platformOs": "linux"}})
    client = CodexProvider(rpc, scope_verified=True)
    assert client.initialize()["write"] is False
    with pytest.raises(ProviderError, match="SCOPE_UNAVAILABLE"):
        client.create_session("/work")
    assert not any(method == "thread/start" for method, _ in rpc.calls)


@pytest.mark.parametrize("read_only", [True, False])
@pytest.mark.parametrize("mode", ["NEW", "RESUME", "FORK"])
def test_scoped_sessions_and_turns_use_one_confirmed_profile_without_legacy_parameters(selection, read_only, mode):
    proof = issue_proof(*selection)
    rpc = _rpc_for_proof(proof)
    client = CodexProvider(rpc, scope_proof=proof)
    assert client.initialize()["write"] is True
    assert rpc.calls[0][1]["capabilities"]["experimentalApi"] is True
    created = client.create_session("/work", native_id=None if mode == "NEW" else "old-thread", mode=mode, read_only=read_only)
    assert client.start_turn(created["native_session_id"], "bounded task")["native_turn_id"] == "turn-1"
    for method, params in rpc.calls:
        if method in {"thread/start", "thread/resume", "thread/fork", "turn/start"}:
            assert params["permissions"] == proof.profile(read_only)
            assert params["runtimeWorkspaceRoots"] == ["/work"]
            assert "sandbox" not in params and "sandboxPolicy" not in params
            assert "sandbox_workspace_write" not in params.get("config", {})
    assert len([1 for method, _ in rpc.calls if method == "turn/start"]) == 1
    # No unsupported resume/settings preflight is invented for a new empty thread.
    assert len([1 for method, _ in rpc.calls if method == "thread/resume"]) == (mode == "RESUME")


@pytest.mark.parametrize("bad", ["profile", "extends", "roots", "cwd", "network", "approval", "reviewer", "tmp", "missing"])
def test_bad_policy_ack_never_binds_a_thread_or_sends_a_turn(selection, bad):
    proof = issue_proof(*selection); rpc = _rpc_for_proof(proof)
    original = rpc.results["thread/start"]
    def broken(params):
        result = original(params)
        if bad == "profile": result["activePermissionProfile"]["id"] = "other-profile"
        elif bad == "extends": result["activePermissionProfile"]["extends"] = ":workspace"
        elif bad == "roots": result["runtimeWorkspaceRoots"] = ["/outside"]
        elif bad == "cwd": result["cwd"] = "/outside"
        elif bad == "network": result["sandbox"]["networkAccess"] = True
        elif bad == "approval": result["approvalPolicy"] = "never"
        elif bad == "reviewer": result["approvalsReviewer"] = "auto_review"
        elif bad == "tmp": result["sandbox"]["excludeSlashTmp"] = False
        else: result.pop("activePermissionProfile")
        return result
    rpc.results["thread/start"] = broken
    client = CodexProvider(rpc, scope_proof=proof); client.initialize()
    with pytest.raises(ProviderError): client.create_session("/work")
    with pytest.raises(ProviderError): client.start_turn("thread-1", "do not send")
    assert not any(method == "turn/start" for method, _ in rpc.calls)


def test_profile_drift_close_and_cross_channel_ack_cannot_send(selection):
    proof = issue_proof(*selection); rpc = _rpc_for_proof(proof)
    client = CodexProvider(rpc, scope_proof=proof); client.initialize(); client.create_session("/work")
    other_rpc = _rpc_for_proof(proof)
    other = CodexProvider(other_rpc, scope_proof=proof); other.initialize()
    other._sessions["thread-1"] = dict(client._sessions["thread-1"])
    with pytest.raises(ProviderError): other.start_turn("thread-1", "cross-channel")
    assert not any(method == "turn/start" for method, _ in other_rpc.calls)
    rpc.results["config/read"]["config"]["permissions"][proof.profile(False)]["network"]["enabled"] = True
    with pytest.raises(ProviderError): client.start_turn("thread-1", "drift")
    assert not any(method == "turn/start" for method, _ in rpc.calls)
    client.close()
    with pytest.raises(ProviderError): client.start_turn("thread-1", "closed")
    assert not any(method == "turn/start" for method, _ in rpc.calls)


def test_unknown_turn_dispatch_is_never_automatically_retried(selection):
    proof = issue_proof(*selection); rpc = _rpc_for_proof(proof)
    def unknown(_params): raise ProtocolError("EXECUTION_DISPATCH_UNKNOWN")
    rpc.results["turn/start"] = unknown
    client = CodexProvider(rpc, scope_proof=proof); client.initialize(); client.create_session("/work")
    with pytest.raises(ProtocolError, match="DISPATCH_UNKNOWN"):
        client.start_turn("thread-1", "only one dispatch")
    assert len([1 for method, _ in rpc.calls if method == "turn/start"]) == 1


from agentguard.execution.runtime import ExecutionRuntime
from agentguard.execution.scope_readiness import PROBE_BOOTSTRAP, PreparedExecution
from agentguard.recovery import protection_domains
from agentguard.recovery.protection_domains import DomainError, DomainRegistry
from agentguard.work.service import WorkError


@pytest.mark.parametrize("drift", [None, "program", "store", "root", "epoch", "disk", "disconnected", "canonical-alias"])
def test_registry_checks_selection_before_and_after_fixed_probe(selection, monkeypatch, tmp_path, drift):
    identity, source, _launch, report, nonce = selection
    source["label"] = "Ubuntu"
    registry = DomainRegistry(tmp_path)
    after_probe = [False]
    canonical = "/usr/lib/codex-real" if drift == "canonical-alias" else "/usr/bin/codex"
    seen = []
    def bound(_binding):
        snapshot = dict(source)
        if after_probe[0] and drift in {"epoch", "disk", "disconnected"}:
            key = {"epoch": "epoch", "disk": "disk_identity", "disconnected": "connected"}[drift]
            snapshot[key] = False if drift == "disconnected" else "changed"
        return snapshot, {"root": "/work", "identity": {"dev": 1, "ino": 2}}
    def call(_source, payload):
        seen.append(payload["action"])
        if payload["action"] == "register": return {"identity": {"dev": 1, "ino": 99 if after_probe[0] and drift == "root" else 2}}
        if payload["action"] == "application_entry": return {"status": "AVAILABLE", "identity": "changed" if after_probe[0] and drift == "program" else "program-identity", "entry_path": canonical}
        return {"identity": "changed" if after_probe[0] and drift == "store" else "store-identity"}
    def run(argv, *, input_bytes, timeout):
        assert argv[:7] == ["wsl.exe", "--distribution", "Ubuntu", "--exec", "python3", "-I", "-B"]
        assert argv[9] == PROBE_BOOTSTRAP
        script, request = input_bytes.splitlines()
        script = json.loads(script); request = json.loads(request)
        assert hashlib.sha256(script.encode()).hexdigest() == argv[10]
        assert set(request) == {"program", "root", "root_identity", "challenge"}
        assert request["program"] == canonical
        assert "store_path" not in request and "credential" not in script.lower()
        assert timeout == 40
        after_probe[0] = True
        return json.dumps({"ok": True, "result": report}).encode()
    monkeypatch.setattr(registry, "_bound", bound)
    monkeypatch.setattr(registry, "_call", call)
    monkeypatch.setattr(protection_domains.secrets, "token_hex", lambda _size: nonce)
    monkeypatch.setattr(protection_domains, "_run", run)
    if drift not in {None, "canonical-alias"}:
        with pytest.raises(DomainError): registry.prepare_execution(identity, "CODEX")
    else:
        prepared = registry.prepare_execution(identity, "CODEX")
        assert prepared.proof.matches(identity, prepared.launch)
        assert prepared.launch["argv"][8] == canonical
        assert prepared.launch["argv"][-len(profile_arguments(nonce)):] == profile_arguments(nonce)
        assert seen.count("register") == seen.count("application_entry") == seen.count("inspect_session_store") == 2


def test_runtime_rejects_a_json_or_rebound_proof_before_spawning(selection, monkeypatch):
    identity, _source, launch, _report, _nonce = selection
    runtime = object.__new__(ExecutionRuntime)
    runtime.provider_factory = None
    class Domains:
        def prepare_execution(self, selected, provider):
            return PreparedExecution(launch, {"scope_verified": True})
    runtime.domains = Domains()
    from agentguard.execution import protocol
    def no_process(**_kwargs): raise AssertionError("invalid proof must not start a provider")
    monkeypatch.setattr(protocol, "RpcProcess", no_process)
    with pytest.raises(WorkError, match="SCOPE_PROOF_INVALID"):
        runtime._build_provider({"identity_json": json.dumps(identity), "provider": "CODEX"}, lambda _raw: None)


def test_observed_full_version_is_bound_without_a_numeric_version_allowlist(selection):
    selection[3]["version"] = "9.2.3-compatible.1"
    proof = issue_proof(*selection)
    rpc = _rpc_for_proof(proof)
    client = CodexProvider(rpc, scope_proof=proof)
    assert client.initialize()["write"] is True
    changed = _rpc_for_proof(proof)
    changed.results["initialize"]["userAgent"] = "asg/9.2.3-compatible.2"
    assert CodexProvider(changed, scope_proof=proof).initialize()["write"] is False


def test_runtime_identity_failure_invalidates_the_same_channel_without_resending(selection):
    import threading

    identity, _source, _launch, _report, _nonce = selection
    proof = issue_proof(*selection); rpc = _rpc_for_proof(proof)
    client = CodexProvider(rpc, scope_proof=proof); client.initialize(); client.create_session("/work")
    runtime = object.__new__(ExecutionRuntime)
    runtime.lock = threading.RLock()
    runtime.channels = {"session": {"provider": client, "text": ["retained output"]}}
    class ChangedSelection:
        def validate_selection(self, *_args, **_kwargs): raise WorkError("EXECUTION_IDENTITY_CHANGED")
    session = {"id": "session", "application_id": "app", "store_id": "store", "scope_id": "scope",
               "identity_json": json.dumps(identity)}
    with pytest.raises(WorkError, match="IDENTITY_CHANGED"):
        runtime._validate_selection(ChangedSelection(), session)
    with pytest.raises(ProviderError): client.start_turn("thread-1", "never send")
    assert runtime.channels["session"]["text"] == ["retained output"]
    assert not any(method == "turn/start" for method, _ in rpc.calls)


@pytest.mark.skipif(os.name != "posix", reason="standalone proof transport executes inside WSL")
def test_hash_checked_bootstrap_runs_only_packaged_fixed_helper(tmp_path):
    import subprocess

    from agentguard.execution.scope_readiness import probe_source

    script = probe_source()
    root = tmp_path / "root"; root.mkdir()
    request = {"program": str(Path(sys.executable).resolve()), "root": str(root),
               "root_identity": {"dev": -1, "ino": -1}, "challenge": "c" * 32}
    payload = (json.dumps(script) + "\n" + json.dumps(request) + "\n").encode()
    command = [sys.executable, "-I", "-B", "-c", PROBE_BOOTSTRAP, hashlib.sha256(script.encode()).hexdigest()]
    reply = subprocess.run(command, input=payload, capture_output=True, timeout=5, check=False)
    assert reply.returncode == 0, reply.stderr
    assert json.loads(reply.stdout) == {"ok": False, "reason_code": "EXECUTION_SCOPE_ROOT_CHANGED"}
    assert list(root.iterdir()) == []
    command[-1] = "0" * 64
    rejected = subprocess.run(command, input=payload, capture_output=True, timeout=5, check=False)
    assert rejected.returncode == 2 and not rejected.stdout


def test_forged_object_missing_profiles_and_native_id_changes_are_rejected(selection):
    from dataclasses import replace

    identity, source, _launch, report, challenge = selection
    proof = issue_proof(*selection)
    with pytest.raises(ScopeReadinessError):
        issue_proof(identity, source, {"argv": ["unbound-program"]}, report, challenge)
    for forged in ({"scope_verified": True}, replace(proof, _issuer=object())):
        rpc = _rpc_for_proof(proof)
        assert CodexProvider(rpc, scope_proof=forged).initialize()["write"] is False
    rpc = _rpc_for_proof(proof)
    client = CodexProvider(rpc, scope_proof=proof); client.initialize(); client.create_session("/work")
    client._sessions["different-thread"] = client._sessions["thread-1"]
    with pytest.raises(ProviderError): client.start_turn("different-thread", "do not send")
    assert not any(method == "turn/start" for method, _ in rpc.calls)


@pytest.mark.parametrize("kind,provider", [("CONTAINER", "CODEX"), ("WSL", "KIMI_CODE"), ("NATIVE", "CODEX")])
def test_other_sources_keep_the_existing_launch_without_scope_grant(selection, monkeypatch, tmp_path, kind, provider):
    identity, source, launch, _report, _challenge = selection
    source["kind"] = kind
    registry = DomainRegistry(tmp_path)
    monkeypatch.setattr(registry, "execution_launch", lambda *_args: launch)
    monkeypatch.setattr(registry, "_bound", lambda _binding: (source, {}))
    def forbidden(*_args, **_kwargs): raise AssertionError("unrelated source must not be probed")
    monkeypatch.setattr(protection_domains, "_run", forbidden)
    prepared = registry.prepare_execution(identity, provider)
    assert prepared.launch is launch and prepared.proof is None


@pytest.mark.skipif(os.name != "posix", reason="probe subprocess ownership is Unix-only")
@pytest.mark.parametrize("command,reason", [
    ("print('bounded')", None),
    ("import sys; sys.stdout.write('x'*65537)", "OUTPUT_LIMIT"),
    ("import time; time.sleep(2)", "TIMEOUT"),
    ("import os,time; os.close(1); time.sleep(2)", "TIMEOUT"),
])
def test_probe_command_is_bounded_and_reaps_only_its_owned_group(command, reason):
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    argv = [sys.executable, "-I", "-B", "-c", command]
    if reason:
        with pytest.raises(scope_probe.ProbeError, match=reason):
            scope_probe._command(argv, environment, timeout=.2)
    else:
        assert scope_probe._command(argv, environment, timeout=2).strip() == "bounded"


def test_scope_invalidation_does_not_join_rpc_callbacks_under_the_runtime_lock(selection):
    import threading

    identity, _source, _launch, _report, _nonce = selection
    proof = issue_proof(*selection); rpc = _rpc_for_proof(proof)
    client = CodexProvider(rpc, scope_proof=proof); client.initialize(); client.create_session("/work")
    runtime = object.__new__(ExecutionRuntime); runtime.lock = threading.RLock()
    runtime.channels = {"session": {"provider": client}}
    callbacks = []
    def close_with_callback():
        arrived = threading.Event()
        def callback():
            with runtime.lock: arrived.set()
        thread = threading.Thread(target=callback); thread.start(); callbacks.append(thread)
        assert arrived.wait(.3), "RPC close must not join callbacks while holding the runtime lock"
    rpc.close = close_with_callback
    class ChangedSelection:
        def validate_selection(self, *_args, **_kwargs): raise WorkError("EXECUTION_IDENTITY_CHANGED")
    session = {"id": "session", "application_id": "app", "store_id": "store", "scope_id": "scope",
               "identity_json": json.dumps(identity)}
    try:
        with pytest.raises(WorkError, match="IDENTITY_CHANGED"):
            runtime._validate_selection(ChangedSelection(), session)
    finally:
        for thread in callbacks:
            thread.join(timeout=1)
            assert not thread.is_alive()


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "WNOWAIT"), reason="Linux WSL process ownership")
def test_probe_cleans_group_before_reaping_the_owned_leader(monkeypatch):
    original_killpg = os.killpg
    observed = []
    def kill_while_owned(pid, sig):
        try:
            state = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError as error:
            raise AssertionError("leader was already reaped before group cleanup") from error
        observed.append(None if state is None else state.si_status)
        return original_killpg(pid, sig)
    monkeypatch.setattr(scope_probe.os, "killpg", kill_while_owned)
    assert scope_probe._command([sys.executable, "-I", "-B", "-c", "print('owned')"],
                                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, timeout=2).strip() == "owned"
    assert observed == [0]


def test_prepared_execution_repr_never_prints_its_environment():
    prepared = PreparedExecution({"argv": ["program"], "env": {"TOKEN": "PRIVATE_TEST_SENTINEL"}})
    assert "PRIVATE_TEST_SENTINEL" not in repr(prepared)
    assert prepared.launch["env"]["TOKEN"] == "PRIVATE_TEST_SENTINEL"


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "WNOWAIT"), reason="Linux WSL process ownership")
def test_probe_preserves_a_normal_nonzero_leader_exit_status(monkeypatch):
    original = scope_probe.subprocess.Popen
    owned = []
    def launch(*args, **kwargs):
        process = original(*args, **kwargs); owned.append(process); return process
    monkeypatch.setattr(scope_probe.subprocess, "Popen", launch)
    with pytest.raises(scope_probe.ProbeError, match="PROFILE_UNAVAILABLE"):
        scope_probe._command([sys.executable, "-I", "-B", "-c", "import sys; sys.exit(7)"],
                             {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, timeout=2)
    assert owned[0].returncode == 7


@pytest.mark.skipif(os.name != "posix", reason="WSL private directory security")
def test_new_private_directory_mode_failure_keeps_first_error_and_cleans_its_own_inode(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    root = tmp_path / "root"; root.mkdir()
    original_fstat = os.fstat
    def unsupported_mode(fd):
        value = original_fstat(fd)
        if os.readlink("/proc/self/fd/" + str(fd)) == str(root / ".agentguard"):
            return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino,
                                   st_uid=value.st_uid, st_mode=stat.S_IFDIR | 0o777)
        return value
    monkeypatch.setattr(scope_probe.os, "fstat", unsupported_mode)
    request = {"program": str(Path(sys.executable).resolve()), "root": str(root),
               "root_identity": {"dev": root.stat().st_dev, "ino": root.stat().st_ino}, "challenge": "d" * 32}
    with pytest.raises(scope_probe.ProbeError, match="PRIVATE_UNSAFE") as error:
        scope_probe.probe(request)
    assert error.value.reason_code == "EXECUTION_SCOPE_PRIVATE_UNSAFE"
    assert not (root / ".agentguard").exists()


@pytest.mark.skipif(os.name != "posix", reason="WSL owned cleanup bounds")
def test_owned_generated_cleanup_has_a_bound_and_does_not_follow_external_links(tmp_path, monkeypatch):
    parent = tmp_path / "owned-parent"; parent.mkdir()
    scratch = scope_probe._Scratch(str(parent), generated=True)
    for index in range(3):
        (Path(scratch.path) / str(index)).write_bytes(b"owned")
    monkeypatch.setattr(scope_probe, "_LIMIT_CLEANUP", 2)
    assert scratch.cleanup() is False
    assert scratch.cleanup_failure == "generated_io_" + str(errno.E2BIG)


def test_scoped_launch_defaults_to_the_issued_readonly_profile(selection):
    nonce = selection[-1]
    arguments = profile_arguments(nonce)
    assert 'default_permissions="asg_ro_' + nonce + '"' in arguments


def test_scoped_configuration_rejects_a_writable_default(selection):
    proof = issue_proof(*selection)
    rpc = _rpc_for_proof(proof)
    client = CodexProvider(rpc, scope_proof=proof)
    client.initialize()
    rpc.results['config/read']['config']['default_permissions'] = proof.profile(False)
    with pytest.raises(ProviderError, match='PROFILE_UNVERIFIED'):
        client.create_session('/work')
    assert not any(method == 'thread/start' for method, _ in rpc.calls)
