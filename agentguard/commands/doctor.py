"""doctor command — detailed diagnostic with PASS/WARN/FAIL/SKIP/UNREACHABLE."""

from pathlib import Path

from ..core.host_tools import (
    HOST_INSTALLED,
    host_tool_resolution,
    platform_is_windows,
)
from ..core.host_tools import (
    UNKNOWN as PRESENCE_UNKNOWN,
)
from ..core.runner import run_command, which
from ..core.versions import bundled_sidecar_active


def _in_container() -> bool:
    """Heuristic: check if running inside a container (POSIX-only probe)."""
    if platform_is_windows():
        return False
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.is_file():
            text = cgroup.read_text(encoding="utf-8", errors="replace")
            if "docker" in text or "containerd" in text or "kubepods" in text:
                return True
        if Path("/.dockerenv").exists():
            return True
    except OSError:
        pass
    return False


HOST_SERVICES = {"docker"}


def doctor(config: dict) -> list[dict[str, object]]:
    """Run comprehensive diagnostic checks."""
    if platform_is_windows():
        return _doctor_windows(config)
    return _doctor_posix(config)


def _doctor_windows(config: dict) -> list[dict[str, object]]:
    """Windows-native probes only; no Linux/container-era assumptions run."""
    results: list[dict[str, object]] = [
        _info("platform", "Windows host detected; running Windows-native probes"),
        *_bundled_runtime_checks(),
    ]

    # Docker Desktop is optional tooling on a physical Windows host; resolve it
    # from host-native facts, not only the sidecar's inherited PATH.
    docker_resolution = host_tool_resolution("docker")
    if docker_resolution.presence == PRESENCE_UNKNOWN:
        results.append(_unreachable("docker-cli", "Docker presence could not be probed; UNKNOWN"))
    elif docker_resolution.path or docker_resolution.sidecar_callable:
        _docker_checks(
            results,
            config,
            in_container=False,
            docker_bin=docker_resolution.path or which("docker"),
        )
    else:
        results.append(_warn("docker-cli", "Docker Desktop not detected (optional)"))

    # Node.js — real Windows fact: node.exe present in PATH plus real version.
    _check_windows_binary(results, "node", ["node", "--version"], "Node.js")

    # Git — real Windows fact.
    _check_windows_binary(results, "git", ["git", "--version"], "Git")

    # External Python — real Windows interpreter semantics (py launcher,
    # python.exe). Distinct from the bundled sidecar runtime reported above.
    _windows_external_python(results)

    # Port listing, Linux config-file and /workspace filesystem probes, and the
    # historical container/CloudCLI assumptions do not apply to a native
    # Windows host and are intentionally not probed here.
    return results


def _doctor_posix(config: dict) -> list[dict[str, object]]:
    """Existing Linux/container diagnostics (unchanged behavior)."""
    results: list[dict[str, object]] = []
    in_container = _in_container()

    # 1. Kernel / container detection
    if in_container:
        results.append(_info("container", "Running inside container (no Docker socket access)"))

    results.extend(_bundled_runtime_checks())

    # --- Host services (UNREACHABLE when inside container) ---
    _docker_checks(results, config, in_container=in_container)

    # 5. Port check
    port = int(config.get("port", 3001))
    try:
        r = run_command(["ss", "-tln", f"sport = :{port}"], timeout=5)
        if r.success and f":{port}" in r.stdout:
            results.append(_ok("port", f"Port {port} listening"))
        else:
            results.append(_warn("port", f"Port {port} not listening (expected if CloudCLI not running)"))
    except FileNotFoundError:
        results.append(_skip("port", "Cannot check port (no ss)"))
    except Exception:
        results.append(_skip("port", "Port check unavailable"))

    # 6. Node.js
    if which("node"):
        try:
            r = run_command(["node", "--version"], timeout=5)
            results.append(_ok("node", f"Node {r.stdout}" if r.success else "Node version unavailable"))
        except Exception as e:
            results.append(_fail("node", str(e)))
    else:
        results.append(_fail("node", "Node.js not found"))

    # 7. Python
    if which("python3"):
        try:
            r = run_command(["python3", "--version"], timeout=5)
            results.append(_ok("python", f"Python {r.stdout}" if r.success else "Python version unavailable"))
        except Exception as e:
            results.append(_fail("python", str(e)))
    else:
        results.append(_fail("python", "Python3 not found"))

    # 8-14. Tool versions
    _check_tool(results, "claude", ["claude", "--version"])
    _check_tool(results, "cloudcli", ["cloudcli", "--version"])
    _check_tool(results, "ccr", ["ccr", "--version"])

    _check_tool(results, "git", ["git", "--version"])

    # Config files
    key_paths = [
        Path("/home/agent/.claude/settings.json"),
        Path("/workspace/.claude/settings.local.json"),
    ]
    settings_ok = sum(1 for p in key_paths if p.is_file())
    if settings_ok == len(key_paths):
        results.append(_ok("config-files", "All key config files present"))
    elif settings_ok > 0:
        results.append(_warn("config-files", f"{settings_ok}/{len(key_paths)} config files present"))
    else:
        results.append(_fail("config-files", "No config files found"))

    # Filesystem
    workspace = Path("/workspace")
    if workspace.is_dir():
        try:
            test_file = workspace / ".agentguard-write-test"
            test_file.write_text("")
            test_file.unlink()
            results.append(_ok("filesystem", "/workspace is writable"))
        except (OSError, PermissionError):
            results.append(_fail("filesystem", "/workspace not writable"))

    return results


def _docker_checks(
    results: list,
    config: dict,
    *,
    in_container: bool,
    docker_bin: str | None = None,
) -> None:
    """Shared Docker daemon/container probes (valid on Linux and Windows)."""
    # A named container is doctor truth only when this installation
    # explicitly configures one; the historical dev default never applies.
    # Tolerant read: legacy flat keys and the nested [checks] table both count.
    container_name = config.get("container_name") or (config.get("checks") or {}).get(
        "container_name"
    )
    docker_bin = docker_bin or which("docker")
    if not docker_bin:
        if in_container:
            results.append(_unreachable("docker-cli",
                "Container is isolated (no Docker socket mounted). "
                "This is expected and correct — the sandbox must not control the host Docker daemon."))
        else:
            results.append(_fail("docker-cli", "Docker CLI not found in PATH"))

    # Docker daemon
    if docker_bin:
        try:
            r = run_command([docker_bin, "info", "--format", "{{.ServerVersion}}"], timeout=10)
            if r.success:
                results.append(_ok("docker-daemon", f"Docker daemon v{r.stdout}"))
            else:
                if in_container:
                    results.append(_unreachable("docker-daemon",
                        "Cannot reach Docker daemon from container (no socket). Expected."))
                else:
                    results.append(_warn("docker-daemon", f"Docker daemon not responding: {r.stderr}"))
        except FileNotFoundError:
            results.append(_unreachable("docker-daemon", "Docker CLI not available in container"))
        except Exception as e:
            results.append(_unreachable("docker-daemon", str(e)))

    # Container status — only for an explicitly configured, product-owned name
    if docker_bin and container_name:
        try:
            r = run_command(
                [docker_bin, "ps", "--filter", f"name={container_name}", "--format", "{{.ID}}"],
                timeout=10,
            )
            if r.success and r.stdout.strip():
                results.append(_ok("container", f"{container_name} is running"))
                # Security posture
                try:
                    r2 = run_command([
                        docker_bin, "inspect", container_name,
                        "--format", "{{.HostConfig.Privileged}}|{{.HostConfig.CapDrop}}",
                    ], timeout=10)
                    if r2.success:
                        parts = r2.stdout.split("|")
                        priv = parts[0].strip() if len(parts) > 0 else "?"
                        if priv == "false":
                            results.append(_ok("docker-security", "Container not privileged"))
                        else:
                            results.append(_warn("docker-security", f"Container privileged={priv}"))
                except Exception:
                    results.append(_skip("docker-security", "Cannot inspect container"))
            else:
                if in_container:
                    results.append(_unreachable("container",
                        f"Cannot check container '{container_name}' from inside container"))
                else:
                    results.append(_fail("container", f"{container_name} not running"))
        except FileNotFoundError:
            results.append(_unreachable("container", "Docker not available in container"))
        except Exception as e:
            results.append(_unreachable("container", str(e)))
    elif not container_name:
        results.append(_skip("container", "No product-owned container configured"))
    else:
        results.append(_unreachable("container", "Docker not available"))


def _bundled_runtime_checks() -> list[dict[str, object]]:
    """Bundled sidecar runtime fact; distinct from any external Python."""
    if bundled_sidecar_active():
        return [_ok("bundled-runtime", "Bundled sidecar runtime active")]
    return [_skip("bundled-runtime", "No bundled sidecar runtime (running from source)")]


def _check_windows_binary(
    results: list, name: str, cmd: list, label: str
) -> None:
    """Host-native Windows version check; never a spurious FAIL.

    Uses the host-native resolver — the sidecar's inherited PATH subset does
    not define host truth.
    """
    resolution = host_tool_resolution(cmd[0])
    if resolution.presence == PRESENCE_UNKNOWN:
        results.append(
            _unreachable(name, f"{label} presence could not be probed; treating as UNKNOWN")
        )
        return
    if not (resolution.path or resolution.sidecar_callable):
        results.append(_warn(name, f"{label} not detected on host"))
        return

    executable = resolution.path or which(cmd[0]) or cmd[0]
    try:
        r = run_command([executable, *cmd[1:]], timeout=5)
    except Exception:
        presence = "installed" if resolution.presence == HOST_INSTALLED else "sidecar-only"
        results.append(
            _unreachable(name, f"{label} {presence} but version probe failed")
        )
        return
    if r.success and r.stdout:
        results.append(_ok(name, f"{label} {r.stdout.splitlines()[0]}"))
    else:
        results.append(_unreachable(name, f"{label} version unavailable"))


def _windows_external_python(results: list) -> None:
    """External interpreter via real Windows semantics (py launcher, python.exe)."""
    for candidate in ("py", "python", "python3"):
        resolution = host_tool_resolution(candidate)
        if not (resolution.path or resolution.sidecar_callable):
            continue
        executable = resolution.path or which(candidate) or candidate
        try:
            r = run_command([executable, "--version"], timeout=5)
        except Exception:
            continue
        if r.success and r.stdout:
            version = r.stdout.splitlines()[0].strip()
            results.append(_ok("python", f"{version or 'Python'} (external interpreter)"))
            return
    if bundled_sidecar_active():
        results.append(
            _info(
                "python",
                "Optional external Python interpreter not detected on host",
            )
        )
    else:
        results.append(_warn("python", "No external Python interpreter detected on host"))


def _check_tool(results: list, name: str, cmd: list) -> None:
    """Run a version check and append result."""
    if which(cmd[0]):
        try:
            r = run_command(cmd, timeout=10)
            if r.success:
                results.append(_ok(name, f"{cmd[0]} {r.stdout.split(chr(10))[0]}"))
            else:
                results.append(_warn(name, f"{cmd[0]} version unavailable"))
        except FileNotFoundError:
            results.append(_fail(name, f"{cmd[0]} not found"))
        except Exception as e:
            results.append(_fail(name, str(e)))
    else:
        results.append(_warn(name, f"{cmd[0]} not found"))


def _ok(check_id: str, msg: str) -> dict[str, object]:
    return {"check": check_id, "status": "PASS", "message": msg}

def _warn(check_id: str, msg: str) -> dict[str, object]:
    return {"check": check_id, "status": "WARN", "message": msg}

def _fail(check_id: str, msg: str) -> dict[str, object]:
    return {"check": check_id, "status": "FAIL", "message": msg}

def _skip(check_id: str, msg: str) -> dict[str, object]:
    return {"check": check_id, "status": "SKIP", "message": msg}

def _unreachable(check_id: str, msg: str) -> dict[str, object]:
    return {"check": check_id, "status": "UNREACHABLE", "message": msg}

def _info(check_id: str, msg: str) -> dict[str, object]:
    return {"check": check_id, "status": "INFO", "message": msg}
