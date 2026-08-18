"""External Agent launcher identity contracts (physical Windows dogfood).

Kimi Code has formal bounded identity support. Node-hosted Agent launchers
(node.exe / generic cli.js / npm shims) resolve identity only through bounded
package-identity metadata derived from bounded filesystem anchor paths.
A raw process command line is never exposed, stored, or logged by the
resolution helpers, and cannot influence classification. When identity cannot
be reliably established the result is UNKNOWN / unclassified — never a guess.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

from agentguard.discovery.agents import launcher_identity
from agentguard.discovery.product import ProductDiscoveryService
from tests.test_product_discovery import (
    _ProcessBackend,
    _ProcessHandle,
    _RuntimeAdapter,
)

NOW = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)


class TestBoundedLauncherIdentity:
    def test_known_package_resolves(self, tmp_path):
        cli = tmp_path / ".npm-global" / "node_modules" / "@moonshot-ai" / "kimi-code" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// cli")
        (cli.parent / "package.json").write_text(
            json.dumps({"name": "@moonshot-ai/kimi-code", "version": "1.0.0"})
        )
        assert launcher_identity.resolve_launcher_identity([str(cli)]) == "KIMI_CODE"

    @staticmethod
    def _install(tmp_path, package: str) -> str:
        cli = tmp_path / "node_modules" / package / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// cli")
        (cli.parent.parent / "package.json").write_text(
            json.dumps({"name": package, "version": "1.0.0"})
        )
        return str(cli)

    def test_claude_code_package_resolves(self, tmp_path):
        cli = self._install(tmp_path, "@anthropic-ai/claude-code")
        assert launcher_identity.resolve_launcher_identity([cli]) == "CLAUDE"

    def test_codex_package_resolves(self, tmp_path):
        cli = self._install(tmp_path, "@openai/codex")
        assert launcher_identity.resolve_launcher_identity([cli]) == "CODEX"

    def test_generic_cli_js_returns_none(self, tmp_path):
        cli = tmp_path / "node_modules" / "some-tool" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("// cli")
        (cli.parent / "package.json").write_text(
            json.dumps({"name": "some-tool", "version": "0.1.0"})
        )
        assert launcher_identity.resolve_launcher_identity([str(cli)]) is None

    def test_package_identity_anchors_bounded(self, tmp_path):
        anchor = tmp_path / "anchored.js"
        anchor.write_text("//")
        joined = tmp_path / "x" / "claude.exe"
        deep = tmp_path / "a" / "b" / "c" / "cli.js"
        drive_relative = "node_modules\\@anthropic-ai\\claude-code\\cli.js"
        for path in (deep,):
            path.parent.mkdir(parents=True)
            path.write_text("//")
        anchors = (
            str(anchor),
            str(joined),
            str(deep),
            drive_relative,
            "C:\\",
            "",
        )
        for value in anchors:
            assert launcher_identity.is_package_identity_anchor(value) is False

    def test_resolver_has_no_process_or_cmdline_surface(self, tmp_path):
        cli = self._install(tmp_path, "@openai/codex")
        assert launcher_identity.resolve_launcher_identity([cli]) == "CODEX"
        assert not hasattr(launcher_identity, "psutil")
        assert not any(
            name.startswith("cmdline") for name in dir(launcher_identity)
        ), "launcher identity must have no command-line access surface"


class _AnchorFakeProcess:
    def __init__(self, argv):
        self._argv = argv

    def cmdline(self):
        if isinstance(self._argv, Exception):
            raise self._argv
        return list(self._argv)


class _AnchorFakePsutil:
    def __init__(self, argv):
        self._argv = argv

    def Process(self, pid):
        return _AnchorFakeProcess(self._argv)


class TestBackendBoundedLauncherAnchors:
    def _backend(self, argv):
        from agentguard.discovery.agents.psutil_backend import PsutilProcessBackend

        return PsutilProcessBackend(psutil_module=_AnchorFakePsutil(argv))

    def test_anchor_extraction_never_returns_raw_command_line(self, tmp_path):
        cli = tmp_path / "node_modules" / "@moonshot-ai" / "kimi-code" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("//")
        argv = ["node", str(cli), "--secret-flag", "Bearer-abc"]
        anchors = self._backend(argv).bounded_launcher_anchors(4242)
        assert anchors == (str(cli),)
        for forbidden in ("secret", "Bearer"):
            assert forbidden not in json.dumps(anchors)

    def test_anchor_extraction_fail_closed(self, tmp_path):
        plain = tmp_path / "tool.js"
        plain.write_text("//")
        argv = ["node", str(plain), "--flag"]
        assert self._backend(argv).bounded_launcher_anchors(4242) == ()
        assert self._backend(RuntimeError("boom")).bounded_launcher_anchors(4242) == ()


class TestNodeHostedFailClosedDiscovery:
    def test_malformed_anchor_inputs_return_none(self):
        assert launcher_identity.resolve_launcher_identity(None) is None
        assert launcher_identity.resolve_launcher_identity([]) is None
        assert launcher_identity.resolve_launcher_identity(["C:\\", ""]) is None

    def _snapshot(self, handles):
        service = ProductDiscoveryService(
            runtime_adapter=_RuntimeAdapter(),
            process_backend=_ProcessBackend(tuple(handles)),
            clock=lambda: NOW,
            home_path="C:\\Users\\private-user",
        )
        return service.discover()

    def test_node_hosted_kimi_classified_only_with_bounded_identity(self, tmp_path):
        cli = tmp_path / "node_modules" / "@moonshot-ai" / "kimi-code" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("//")
        (cli.parent / "package.json").write_text(
            json.dumps({"name": "@moonshot-ai/kimi-code", "version": "1.0.0"})
        )
        with patch(
            "agentguard.discovery.product._resolve_bounded_launcher_identity",
            return_value="KIMI_CODE",
        ):
            snapshot = self._snapshot([_ProcessHandle(201, "node.exe", cwd=str(tmp_path))])
        assert [agent.agent_type for agent in snapshot.agents] == ["KIMI_CODE"]

    def test_plain_node_is_never_an_agent(self):
        snapshot = self._snapshot([_ProcessHandle(202, "node.exe")])
        assert snapshot.agents == ()

    def test_unresolvable_node_launcher_never_guessed(self):
        with patch(
            "agentguard.discovery.product._resolve_bounded_launcher_identity",
            return_value=None,
        ):
            snapshot = self._snapshot(
                [_ProcessHandle(203, "node.exe"), _ProcessHandle(204, "nodejs.exe")]
            )
        assert snapshot.agents == ()


class TestProductDiscoveryWindowsFleet(TestNodeHostedFailClosedDiscovery):
    def test_full_agent_fleet_discovered(self):
        handles = [
            _ProcessHandle(301, "Codex.exe"),
            _ProcessHandle(302, "codex.exe"),
            _ProcessHandle(303, "claude.exe"),
            _ProcessHandle(304, "claude-code.exe"),
            _ProcessHandle(305, "kimi.exe"),
            _ProcessHandle(306, "kimi-code.exe"),
            _ProcessHandle(307, "notepad.exe"),
        ]
        snapshot = self._snapshot(handles)
        types = [agent.agent_type for agent in snapshot.agents]
        assert types.count("CODEX") == 2
        assert types.count("CLAUDE") == 2
        assert types.count("KIMI_CODE") == 2
        assert "notepad" not in json.dumps(snapshot.to_dict()).lower()

    def test_multiple_codex_instances_stay_distinct_and_distinguishable(self):
        handles = [
            _ProcessHandle(401, "codex.exe", cwd="C:\\work\\alpha"),
            _ProcessHandle(402, "codex.exe", cwd="C:\\work\\beta"),
        ]
        snapshot = self._snapshot(handles)
        agents = [agent for agent in snapshot.agents if agent.agent_type == "CODEX"]
        assert len(agents) == 2
        assert len({agent.agent_id for agent in agents}) == 2
        labels = [agent.label for agent in agents]
        assert all(label and label.startswith("CODEX") for label in labels)
        assert len(set(labels)) == 2
        evidence = [
            item for item in snapshot.evidence if item.fact_type == "agent.metadata"
        ]
        labels = [item.value.get("instance_label") for item in evidence]
        assert all(isinstance(label, str) and label.startswith("CODEX") for label in labels)
        assert len(set(labels)) == 2

    def test_instance_labels_stay_opaque(self):
        handle = _ProcessHandle(403, "codex.exe", cwd="C:\\work\\private-name")
        snapshot = self._snapshot([handle])
        (agent,) = snapshot.agents
        assert agent.workspace_ids
        assert "workspace\\" not in str(agent.to_dict()).lower()
        # Label shape: "<TYPE> <short-id>" and never a path or workspace name.
        prefix, _, tail = (agent.label or "").partition(" ")
        assert prefix == "CODEX"
        assert 1 <= len(tail) <= 8
        assert "/" not in tail and "\\" not in tail

    def test_command_line_never_enters_dto_or_evidence(self):
        handles = [_ProcessHandle(501, "codex.exe"), _ProcessHandle(502, "node.exe")]
        with patch(
            "agentguard.discovery.product._resolve_bounded_launcher_identity",
            return_value=None,
        ):
            snapshot = self._snapshot(handles)
        encoded = json.dumps(snapshot.to_dict())
        for forbidden in (
            "cmdline",
            "command_line",
            "commandline",
            "argv",
            "--token",
        ):
            assert forbidden not in encoded.lower()
        for forbidden in (
            "C:\\work",
            "private-user",
        ):
            assert forbidden not in encoded
