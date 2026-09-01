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

import agentguard.core.docker as docker_mod
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

    def test_windows_registered_location_is_used_when_path_misses(self, tmp_path):
        registered = tmp_path / "GitHubDesktop" / "app-3.5.12" / "git.exe"
        registered.parent.mkdir(parents=True)
        registered.write_bytes(b"MZ")

        with (
            patch.object(host_tools_mod, "platform_is_windows", lambda: True),
            patch.object(host_tools_mod.os, "environ", {"PATH": "C:\\sidecar-only"}),
            patch.object(host_tools_mod, "which", lambda _n: None),
            patch.object(host_tools_mod, "host_search_path", return_value=[]),
            patch.object(
                host_tools_mod,
                "_windows_registered_tool_paths",
                return_value=[str(registered)],
                create=True,
            ),
        ):
            resolution = host_tools_mod.host_tool_resolution("git")

        assert resolution.presence == "HOST_INSTALLED"
        assert resolution.path == str(registered)

    def test_pi_uninstall_registration_is_a_bounded_install_authority(self, tmp_path):
        executable = tmp_path / "Pi Agent Desktop.exe"
        executable.write_bytes(b"MZ")

        with (
            patch.object(host_tools_mod, "platform_is_windows", return_value=True),
            patch.object(host_tools_mod, "which", return_value=None),
            patch.object(host_tools_mod, "host_search_path", return_value=[]),
            patch.object(
                host_tools_mod,
                "_windows_registered_tool_paths",
                return_value=[str(executable)],
            ),
        ):
            resolution = host_tools_mod.host_tool_resolution("pi")

        assert resolution.presence == host_tools_mod.HOST_INSTALLED
        assert resolution.path == str(executable)

    def test_pi_running_identity_requires_registered_path_and_exact_pe_product_name(
        self, tmp_path
    ):
        executable = tmp_path / "Pi Agent Desktop.exe"
        other = tmp_path / "other" / "Pi Agent Desktop.exe"
        executable.write_bytes(b"MZ")
        other.parent.mkdir()
        other.write_bytes(b"MZ")

        with (
            patch.object(host_tools_mod, "platform_is_windows", return_value=True),
            patch.object(
                host_tools_mod,
                "_windows_registered_tool_paths",
                return_value=[str(executable)],
            ),
            patch.object(
                host_tools_mod,
                "_windows_file_product_name",
                return_value="Pi Agent Desktop",
                create=True,
            ),
        ):
            assert host_tools_mod.windows_bounded_product_identity(str(executable)) == "PI"
            assert host_tools_mod.windows_bounded_product_identity(str(other)) is None

        with (
            patch.object(host_tools_mod, "platform_is_windows", return_value=True),
            patch.object(
                host_tools_mod,
                "_windows_registered_tool_paths",
                return_value=[str(executable)],
            ),
            patch.object(
                host_tools_mod,
                "_windows_file_product_name",
                return_value="Unrelated Electron Product",
                create=True,
            ),
        ):
            assert host_tools_mod.windows_bounded_product_identity(str(executable)) is None

    def test_zcode_uninstall_registration_resolves_exact_product_executable(
        self, tmp_path
    ):
        install_root = tmp_path / "zcode"
        install_root.mkdir()
        executable = install_root / "ZCode.exe"
        uninstaller = install_root / "Uninstall ZCode.exe"
        executable.write_bytes(b"MZ")
        uninstaller.write_bytes(b"MZ")

        with (
            patch.object(host_tools_mod, "platform_is_windows", return_value=True),
            patch.object(
                host_tools_mod,
                "_winreg",
                type(
                    "FakeWinreg",
                    (),
                    {"HKEY_CURRENT_USER": 1, "HKEY_LOCAL_MACHINE": 2},
                )(),
            ),
            patch.object(host_tools_mod, "_registry_text", return_value=None),
            patch.object(host_tools_mod, "which", return_value=None),
            patch.object(host_tools_mod, "host_search_path", return_value=[]),
            patch.object(
                host_tools_mod,
                "_registered_zcode_values",
                return_value=[
                    {
                        "DisplayVersion": "3.9.2",
                        "Publisher": "ZCode",
                        "UninstallString": f'"{uninstaller}" /currentuser',
                    }
                ],
                create=True,
            ),
        ):
            resolution = host_tools_mod.host_tool_resolution("zcode")

        assert resolution.presence == host_tools_mod.HOST_INSTALLED
        assert resolution.path == str(executable)

    def test_zcode_running_identity_requires_registered_path_and_exact_pe_product_name(
        self, tmp_path
    ):
        executable = tmp_path / "ZCode.exe"
        lookalike = tmp_path / "other" / "ZCode.exe"
        executable.write_bytes(b"MZ")
        lookalike.parent.mkdir()
        lookalike.write_bytes(b"MZ")

        with (
            patch.object(host_tools_mod, "platform_is_windows", return_value=True),
            patch.object(
                host_tools_mod,
                "_windows_registered_tool_paths",
                return_value=[str(executable)],
            ),
            patch.object(
                host_tools_mod,
                "_windows_file_product_name",
                return_value="ZCode",
            ),
        ):
            assert (
                host_tools_mod.windows_bounded_product_identity(str(executable))
                == "ZCODE"
            )
            assert host_tools_mod.windows_bounded_product_identity(str(lookalike)) is None

        with (
            patch.object(host_tools_mod, "platform_is_windows", return_value=True),
            patch.object(
                host_tools_mod,
                "_windows_registered_tool_paths",
                return_value=[str(executable)],
            ),
            patch.object(
                host_tools_mod,
                "_windows_file_product_name",
                return_value="Unrelated Electron Product",
            ),
        ):
            assert host_tools_mod.windows_bounded_product_identity(str(executable)) is None

    def test_stale_registered_location_does_not_create_false_presence(self, tmp_path):
        stale = tmp_path / "removed" / "git.exe"
        with (
            patch.object(host_tools_mod, "platform_is_windows", lambda: True),
            patch.object(host_tools_mod.os, "environ", {"PATH": "C:\\sidecar-only"}),
            patch.object(host_tools_mod, "which", lambda _n: None),
            patch.object(host_tools_mod, "host_search_path", return_value=[]),
            patch.object(
                host_tools_mod,
                "_windows_registered_tool_paths",
                return_value=[str(stale)],
                create=True,
            ),
        ):
            resolution = host_tools_mod.host_tool_resolution("git")

        assert resolution.presence == "HOST_NOT_FOUND"
        assert resolution.path is None

    def test_registered_codex_package_version_survives_denied_binary_probe(self):
        resolution = host_tools_mod.ToolResolution(
            host_tools_mod.HOST_INSTALLED,
            False,
            r"C:\Program Files\WindowsApps\OpenAI.Codex\codex.exe",
        )
        with (
            patch.object(versions_mod, "platform_is_windows", return_value=True),
            patch.object(
                versions_mod,
                "host_tool_resolution",
                return_value=resolution,
            ),
            patch.object(versions_mod, "_get_version", return_value=None),
            patch.object(
                versions_mod,
                "windows_registered_tool_version",
                return_value="Codex 26.814.5167.0",
                create=True,
            ),
        ):
            versions = versions_mod.all_versions()

        assert versions["codex"] == "Codex 26.814.5167.0"

    def test_registered_pi_version_is_reported_without_launching_the_gui(self):
        pi_resolution = host_tools_mod.ToolResolution(
            host_tools_mod.HOST_INSTALLED,
            False,
            r"D:\pi\Pi Agent Desktop.exe",
        )

        def resolve(name, **_kwargs):
            if name == "pi":
                return pi_resolution
            return host_tools_mod.ToolResolution(
                host_tools_mod.HOST_NOT_FOUND, False, None
            )

        with (
            patch.object(versions_mod, "platform_is_windows", return_value=True),
            patch.object(versions_mod, "host_tool_resolution", side_effect=resolve),
            patch.object(versions_mod, "_get_version", return_value=None),
            patch.object(
                versions_mod,
                "windows_registered_tool_version",
                side_effect=lambda name: (
                    "Pi Agent Desktop 0.1.14" if name == "pi" else None
                ),
            ),
        ):
            versions = versions_mod.all_versions()

        assert versions["pi"] == "Pi Agent Desktop 0.1.14"

    def test_stale_host_path_entry_is_searched_not_crashed(self, tmp_path):
        """Physical dogfood ground truth: registry PATH entries can point at
        moved/removed installs (e.g. a deleted D:\\...\\Git\\cmd, an empty
        npm dir, a python dir without the exe). A stale entry must simply
        not match; a remaining valid entry still resolves; if every entry is
        stale the result is an authoritative HOST_NOT_FOUND, never a guess
        and never a crash."""
        stale = tmp_path / "gone"  # listed in PATH but does not exist
        valid = tmp_path / "real"
        valid.mkdir()
        (valid / "git.exe").write_bytes(b"MZ")

        with (
            patch.object(host_tools_mod, "platform_is_windows", lambda: True),
            patch.object(host_tools_mod.os, "environ", {"PATH": "C:\\sidecar-only", "PATHEXT": ".COM;.EXE"}),
            patch.object(host_tools_mod, "which", lambda _n: None),
        ):
            found = host_tools_mod.host_tool_resolution("git", search=[str(stale), str(valid)])
            all_stale = host_tools_mod.host_tool_resolution("git", search=[str(stale)])

        assert found.presence == "HOST_INSTALLED"
        assert found.path == str(valid / "git.exe")
        assert all_stale.presence == "HOST_NOT_FOUND"
        assert all_stale.path is None

    def test_empty_real_directory_entry_is_stale_not_found(self, tmp_path):
        """A PATH entry that exists but lacks the executable (physical case:
        Python311 dir holding only Lib/Scripts, npm dir holding no shims)
        is a truthful HOST_NOT_FOUND for that tool."""
        empty_dir = tmp_path / "Python311"
        empty_dir.mkdir()
        (empty_dir / "Lib").mkdir()

        with (
            patch.object(host_tools_mod, "platform_is_windows", lambda: True),
            patch.object(host_tools_mod.os, "environ", {"PATH": "C:\\sidecar-only", "PATHEXT": ".COM;.EXE"}),
            patch.object(host_tools_mod, "which", lambda _n: None),
        ):
            resolution = host_tools_mod.host_tool_resolution(
                "python", search=[str(empty_dir)]
            )

        assert resolution.presence == "HOST_NOT_FOUND"

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


class TestResolvedDockerCommand:
    def test_status_docker_commands_use_host_resolved_absolute_executable(self):
        resolved = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[1] == "--version":
                return type(
                    "R", (), {"success": True, "stdout": "Docker version 29.6.2"}
                )()
            if command[1] == "ps":
                return type(
                    "R", (), {"success": True, "stdout": "abc123 Up"}
                )()
            if command[1] == "inspect":
                return type(
                    "R",
                    (),
                    {"success": True, "stdout": "running|now|image|false"},
                )()
            raise AssertionError(command)

        resolution = host_tools_mod.ToolResolution(
            host_tools_mod.HOST_INSTALLED, False, resolved
        )
        with (
            patch.object(
                docker_mod.host_tools,
                "host_tool_resolution",
                return_value=resolution,
            ),
            patch.object(docker_mod, "which", return_value=None),
            patch.object(docker_mod, "run_command", side_effect=run),
        ):
            assert docker_mod.docker_presence() is True
            assert docker_mod.docker_version() == "Docker version 29.6.2"
            assert docker_mod.container_running("agent-dev")[0] is True
            assert docker_mod.container_info("agent-dev")["status"] == "running"

        assert calls
        assert all(command[0] == resolved for command in calls)


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
