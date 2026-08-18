"""Host-native tool resolver and tri-state port semantics (MSI dogfood).

The packaged sidecar inherits the GUI session's PATH, which can be a strict
subset of the real Windows host PATH (machine/user environment). "the
sidecar PATH can't find it" must never be reported as "the host doesn't
have it", and a probe that was not run must never surface as ``false``.

Covered contracts:
- bundled runtime and external Python are independent facts (packaging never
  skips external Python detection).
- Windows resolvers use host-native environment PATH, not only the sidecar
  process PATH.
- unresolved probes stay UNKNOWN; only an authoritative probe may say absent.
- NOT_PROBED port state is ``None``, never ``False``; the historical
  port-3001 primary fact is gone from the Windows surface.
"""

from __future__ import annotations

from unittest.mock import patch

import agentguard.core.host_tools as host_tools_mod
import agentguard.core.versions as versions_mod
from agentguard.commands.status import status


def _patch_windows():
    return [
        patch.object(host_tools_mod, "platform_is_windows", lambda: True),
        patch.object(versions_mod, "platform_is_windows", lambda: True),
    ]


class TestBundledPlusExternalPython:
    def _bundled_sys(self, tmp_path):
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        sidecar = bundle / "agentguard-sidecar.exe"
        sidecar.write_bytes(b"MZ")
        return type(
            "FakeSys",
            (),
            {
                "_MEIPASS": str(bundle),
                "executable": str(sidecar),
                "frozen": True,
            },
        )()

    def test_packaged_runtime_reports_both_python_facts(self, tmp_path):
        fake_sys = self._bundled_sys(tmp_path)
        pyexe = tmp_path / "Python312" / "python.exe"
        pyexe.parent.mkdir()
        pyexe.write_bytes(b"MZ")
        patches = _patch_windows() + [
            patch("agentguard.core.versions.sys", fake_sys),
            patch.object(host_tools_mod.os, "environ", {}),
            patch.object(
                host_tools_mod, "host_search_path", return_value=[str(pyexe.parent)]
            ),
            patch.object(
                versions_mod,
                "run_command",
                lambda *a, **k: type("R", (), {"success": True, "stdout": "Python 3.12.4"})(),
            ),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            versions = versions_mod.all_versions()

        assert versions["python_bundled"] == "bundled"
        assert versions["python"] == "Python 3.12.4"

    def test_packaged_runtime_without_host_python_is_not_false(self, tmp_path):
        fake_sys = self._bundled_sys(tmp_path)
        patches = _patch_windows() + [
            patch("agentguard.core.versions.sys", fake_sys),
            patch.object(host_tools_mod.os, "environ", {}),
            patch.object(host_tools_mod, "host_search_path", return_value=[]),
            patch.object(host_tools_mod, "which", lambda _n: None),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            versions = versions_mod.all_versions()

        assert versions["python_bundled"] == "bundled"
        # Host truth says no external interpreter; value stays unknown, not False.
        assert versions["python"] is None


class TestHostNativeSearchPath:
    def test_windows_resolver_uses_host_environment_path(self, tmp_path):
        """A tool findable in the host PATH is host-installed even when the
        sidecar's own PATH lacks it."""
        tool_dir = tmp_path / "Git" / "cmd"
        tool_dir.mkdir(parents=True)
        (tool_dir / "git.exe").write_bytes(b"MZ")

        with (
            patch.object(host_tools_mod, "platform_is_windows", lambda: True),
            patch.object(host_tools_mod.os, "environ", {"PATH": "C:\\sidecar-only"}),
            patch.object(host_tools_mod, "which", lambda _n: None),
            patch.object(host_tools_mod, "host_search_path", lambda: [str(tool_dir)]),
        ):
            resolution = host_tools_mod.host_tool_resolution("git")

        assert resolution.presence == "HOST_INSTALLED"
        assert resolution.sidecar_callable is False

    def test_sidecar_only_is_honest(self):
        """Tool visible only to the sidecar PATH is SIDECAR_ONLY, not HOST."""
        with (
            patch.object(host_tools_mod, "platform_is_windows", lambda: True),
            patch.object(host_tools_mod.os, "environ", {"PATH": "C:\\sidecar"}),
            patch.object(host_tools_mod, "host_search_path", list),
            patch.object(host_tools_mod, "which", lambda _n: "C:\\sidecar\\git.exe"),
        ):
            resolution = host_tools_mod.host_tool_resolution("git")

        assert resolution.presence == "SIDECAR_ONLY"
        assert resolution.sidecar_callable is True

    def test_unresolvable_probe_is_unknown_never_absent(self):
        with (
            patch.object(host_tools_mod, "platform_is_windows", lambda: True),
            patch.object(host_tools_mod.os, "environ", {}),
            patch.object(host_tools_mod, "host_search_path", lambda: None),
            patch.object(host_tools_mod, "which", lambda _n: None),
        ):
            resolution = host_tools_mod.host_tool_resolution("git")

        assert resolution.presence == "UNKNOWN"
        assert resolution.sidecar_callable is False

    def test_posix_resolution(self, tmp_path):
        tool_dir = tmp_path / "bin"
        tool_dir.mkdir()
        tool = tool_dir / "git"
        tool.write_bytes(b"#!")
        tool.chmod(0o755)
        with patch.object(host_tools_mod, "platform_is_windows", lambda: False):
            resolution = host_tools_mod.host_tool_resolution("git", search=[str(tool_dir)])
        assert resolution.presence == "HOST_INSTALLED"
        assert resolution.sidecar_callable is True


class TestPortTriState:
    def test_windows_port_probe_is_not_probed_and_default_key_gone(self):
        with (
            patch(
                "agentguard.commands.status.host_tools.platform_is_windows",
                return_value=True,
            ),
            patch("agentguard.commands.status.docker_presence", return_value=False),
            patch("agentguard.core.versions.run_command", side_effect=FileNotFoundError),
        ):
            result = status({})

        assert "port_3001" not in result["checks"]
        for key, value in result["checks"].items():
            if key.startswith("port_"):
                assert value is not False, (key, value)

    def test_posix_unprobed_port_is_none_under_configured_key(self):
        def fail_run(*args, **kwargs):
            raise FileNotFoundError(args[0][0])

        with (
            patch(
                "agentguard.commands.status.host_tools.platform_is_windows",
                return_value=False,
            ),
            patch("agentguard.commands.status.docker_presence", return_value=False),
            patch("agentguard.commands.status.run_command", side_effect=fail_run),
            patch("agentguard.core.versions.run_command", side_effect=fail_run),
        ):
            result = status({"port": 9000})

        assert result["checks"]["port_9000"] is None
        assert "port_3001" not in result["checks"]
