"""Bounded local program identities and shared process signature rules."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentguard.discovery.agents import AgentRole, ProcessCollector
from agentguard.discovery.application_probe import (
    entry_from_argv,
    inventory,
    probe_entry,
)
from agentguard.discovery.product import ProductDiscoveryService
from agentguard.discovery.signatures import COMMAND_NAMES, classify_metadata
from tests.test_product_discovery import (
    _ProcessBackend,
    _ProcessHandle,
    _RuntimeAdapter,
)


class _ApplicationHandle(_ProcessHandle):
    def __init__(
        self, pid: int, basename: str, *, path: str, parent_pid: int = 1,
        entry_path: str | None = None, electron_role: str | None = None,
    ) -> None:
        super().__init__(pid, basename)
        self._path = path
        self._parent_pid = parent_pid
        self._entry_path = entry_path
        self._electron_role = electron_role

    def parent_pid(self):
        return self._parent_pid

    def application_metadata(self):
        return {
            "path": self._path,
            "entry_path": self._entry_path,
            "electron_role": self._electron_role,
        }


def _report(*handles, registered_paths=None):
    return ProductDiscoveryService(
        runtime_adapter=_RuntimeAdapter(),
        process_backend=_ProcessBackend(handles),
        registered_paths=registered_paths,
    ).discover_with_authority()


def test_zcode_electron_children_fold_only_into_their_verified_parent_instance():
    executable = "C:/Program Files/Zcode/Zcode.exe"
    report = _report(
        _ApplicationHandle(100, "Zcode.exe", path=executable),
        _ApplicationHandle(101, "Zcode.exe", path=executable,
                           parent_pid=100, electron_role="renderer"),
        _ApplicationHandle(200, "Zcode.exe", path=executable),
        _ApplicationHandle(201, "Zcode.exe", path=executable,
                           parent_pid=200, electron_role="utility"),
        _ApplicationHandle(202, "Zcode.exe", path="C:/Other/Zcode.exe",
                           parent_pid=200, electron_role="renderer"),
    )
    agents = report.snapshot.agents
    assert len(agents) == 3
    assert all(agent.agent_type == "ZCODE" for agent in agents)
    observed = {item["pid"]: item["agent_id"] for item in report.application_observations}
    assert 101 not in observed
    assert 100 in observed
    assert 201 not in observed
    assert 200 in observed
    assert observed[100] != observed[200] != observed[202]


def test_codex_instances_keep_distinct_ids_and_executable_paths():
    report = _report(
        _ApplicationHandle(300, "codex.exe", path="C:/Apps/A/codex.exe"),
        _ApplicationHandle(301, "codex.exe", path="C:/Apps/A/codex.exe"),
        _ApplicationHandle(302, "codex.exe", path="C:/Apps/B/codex.exe"),
    )
    assert len(report.snapshot.agents) == 3
    assert len({item.agent_id for item in report.snapshot.agents}) == 3
    assert {item["program"]["path"] for item in report.application_observations} == {
        "C:/Apps/A/codex.exe", "C:/Apps/B/codex.exe"
    }


def test_node_package_entry_classifies_without_promoting_arbitrary_interpreters():
    entry = "C:/npm/node_modules/@openai/codex/bin/codex.js"
    report = _report(
        _ApplicationHandle(400, "node.exe", path="C:/Node/node.exe", entry_path=entry),
        _ApplicationHandle(401, "node.exe", path="C:/Node/node.exe",
                           entry_path="C:/private/worker.js"),
        _ApplicationHandle(402, "python.exe", path="C:/Python/python.exe",
                           entry_path="C:/private/worker.py"),
    )
    assert [(item.agent_type, item.lifecycle.value) for item in report.snapshot.agents] == [
        ("CODEX", "RUNNING")
    ]
    assert sum(item["agent_id"] is not None for item in report.application_observations) == 1
    assert report.application_observations[0]["program"]["package_name"] == "@openai/codex"
    assert classify_metadata({"executable_basename": "node", "entry_path": entry}, "host") == (
        "CODEX", AgentRole.EXECUTION_AGENT
    )
    assert classify_metadata({"executable_basename": "node", "entry_path": "worker.js"}, "host") is None
    assert classify_metadata({"executable_basename": "python", "entry_path": "worker.py"}, "host") is None


def test_known_package_and_process_name_signatures_cover_real_installations():
    assert classify_metadata(
        {"executable_basename": "node.exe", "package_name": "@cloudcli-ai/cloudcli"},
        "host",
    ) == ("CLOUDCLI", AgentRole.AGENT_HOST)
    assert classify_metadata(
        {"executable_basename": "node", "name": "kimi-code"}, "host"
    ) == ("KIMI_CODE", AgentRole.EXECUTION_AGENT)


def test_installed_node_entry_is_detected_without_execution(tmp_path: Path):
    package = tmp_path / "node_modules" / "@openai" / "codex"
    script = package / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    (package / "package.json").write_text(
        '{"name":"@openai/codex","version":"1.2.3"}', encoding="utf-8"
    )
    script.write_text("// fixture", encoding="utf-8")
    alias = tmp_path / "bin" / "codex"
    alias.parent.mkdir()
    try:
        alias.symlink_to(script)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")
    result = probe_entry(alias)
    assert result["status"] == "AVAILABLE"
    assert result["path"] == str(alias)
    assert result["entry_path"] == str(script)
    assert result["package_name"] == "@openai/codex"
    assert result["version"] == "1.2.3"
    assert result["identity"].startswith("file-stat-sha256:")
    assert classify_metadata(
        {"executable_basename": "node", "entry_path": result["entry_path"],
         "package_name": result["package_name"]}, "host"
    ) == ("CODEX", AgentRole.EXECUTION_AGENT)
    assert result["executable_basename"] == "codex"
    assert classify_metadata(result, "host") == ("CODEX", AgentRole.EXECUTION_AGENT)
    found = inventory(("missing-command-for-agentguard-test",), paths=(str(alias),))
    assert found["coverage"]["observed"] >= 1
    assert found["coverage"]["missing"] == 1
    assert any(item["path"] == str(alias) for item in found["items"])
    assert {"zcode", "codex", "kimi-code", "claude", "cloudcli", "ccr"} <= set(COMMAND_NAMES)


def test_unknown_program_requires_exact_registered_canonical_path(tmp_path: Path):
    executable = tmp_path / "custom.exe"
    executable.write_bytes(b"fixture")
    handle = _ApplicationHandle(500, "custom.exe", path=str(executable))
    undisclosed = _report(handle)
    assert undisclosed.snapshot.agents == ()
    assert len(undisclosed.application_observations) == 1
    assert undisclosed.application_observations[0]["agent_id"] is None
    registered = _report(handle, registered_paths=lambda: (str(executable),))
    assert len(registered.snapshot.agents) == 1
    assert registered.snapshot.agents[0].agent_type == "UNKNOWN"
    assert registered.application_observations[0]["program"]["path"] == str(executable)
    evidence = next(
        item for item in registered.snapshot.evidence if item.fact_type == "agent.metadata"
    )
    assert evidence.value["role"] == "TOOL_PROCESS"
    assert evidence.value["identity_basis"] == "USER_REGISTRATION"
    assert _report(handle, registered_paths=lambda: (str(tmp_path / "other.exe"),)).snapshot.agents == ()


def test_relative_wrapper_entry_uses_process_cwd_and_registration(tmp_path):
    script = tmp_path / 'custom.py'
    script.write_text('# fixture', encoding='utf-8')
    assert entry_from_argv('python', ['python', 'custom.py'])['entry_path'] is None
    parsed = entry_from_argv('python', ['python', 'custom.py'], cwd=str(tmp_path))
    assert parsed['entry_path'] == str(script)
    handle = _ApplicationHandle(501, 'python.exe', path='C:/Python/python.exe', entry_path=str(script))
    report = _report(handle, registered_paths=lambda: (str(script),))
    assert len(report.snapshot.agents) == 1
    assert report.snapshot.agents[0].agent_type == 'UNKNOWN'


def test_same_pid_in_distinct_domains_has_distinct_process_identity():
    handle = _ApplicationHandle(600, "codex.exe", path="C:/Apps/codex.exe")
    results = [
        ProcessCollector(
            backend=_ProcessBackend((handle,)), execution_domain_id=domain,
            collector="product-discovery", clock=lambda: datetime.now(UTC),
        ).collect()
        for domain in ("windows-a", "windows-b")
    ]
    assert results[0].facts[0].process_instance_id != results[1].facts[0].process_instance_id
    assert all(
        classify_metadata({"executable_basename": item.facts[0].executable_basename},
                          item.facts[0].execution_domain_id) ==
        ("CODEX", AgentRole.EXECUTION_AGENT)
        for item in results
    )


def test_argv_extraction_never_returns_inline_code_or_other_arguments():
    assert entry_from_argv("node", ["node", "-e", "secret()"])["entry_path"] is None
    parsed = entry_from_argv("node", ["node", "/opt/app/entry.js", "--token=private"])
    assert parsed == {"entry_path": "/opt/app/entry.js", "electron_role": None}
    assert "private" not in str(parsed)
    assert entry_from_argv("Zcode.exe", ["Zcode.exe", "--type=renderer"])[
        "electron_role"
    ] == "renderer"


def test_new_unknown_path_program_is_automatically_retained_as_candidate(tmp_path, monkeypatch):
    executable = tmp_path / "new-unlisted-agent.exe"
    executable.write_bytes(b"fixture; not executed")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    found = inventory(())
    candidate = next(item for item in found["items"] if item["path"] == str(executable))
    assert candidate["status"] == "AVAILABLE"
    assert classify_metadata(candidate, "local") is None
    assert found["coverage"]["methods"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-bit contract")
def test_unknown_non_executable_path_file_is_not_a_program(tmp_path, monkeypatch):
    document = tmp_path / "not-a-program"
    document.write_bytes(b"fixture; not executable")
    document.chmod(0o644)
    monkeypatch.setenv("PATH", str(tmp_path))
    found = inventory(())
    assert all(item["path"] != str(document) for item in found["items"])
