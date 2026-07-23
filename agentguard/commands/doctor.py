"""doctor command — detailed diagnostic with PASS/WARN/FAIL/SKIP/UNREACHABLE."""

from pathlib import Path
from typing import Any, Dict, List

from ..core.runner import run_command, which


def _in_container() -> bool:
    """Heuristic: check if running inside a container."""
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


HOST_SERVICES = {"docker", "tailscale"}


def doctor(config: dict) -> List[Dict[str, object]]:
    """Run comprehensive diagnostic checks."""
    results: List[Dict[str, object]] = []
    container_name = config.get("container_name", "agent-dev")
    in_container = _in_container()

    # 1. Kernel / container detection
    if in_container:
        results.append(_info("container", f"Running inside container (no Docker socket access)"))

    # --- Host services (UNREACHABLE when inside container) ---

    # 2. Docker
    docker_bin = which("docker")
    if not docker_bin:
        if in_container:
            results.append(_unreachable("docker-cli",
                "Container is isolated (no Docker socket mounted). "
                "This is expected and correct — the sandbox must not control the host Docker daemon."))
        else:
            results.append(_fail("docker-cli", "Docker CLI not found in PATH"))

    # 3. Docker daemon
    if docker_bin:
        try:
            r = run_command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
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

    # 4. Container status
    if docker_bin:
        try:
            r = run_command(
                ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.ID}}"],
                timeout=10,
            )
            if r.success and r.stdout.strip():
                results.append(_ok("container", f"{container_name} is running"))
                # Security posture
                try:
                    r2 = run_command([
                        "docker", "inspect", container_name,
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
    else:
        results.append(_unreachable("container", "Docker not available"))

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

    # Tailscale
    if which("tailscale"):
        try:
            r = run_command(["tailscale", "status", "--json"], timeout=10)
            if r.success:
                if '"Online":true' in r.stdout or '"BackendState":"Running"' in r.stdout:
                    results.append(_ok("tailscale", "Tailscale connected"))
                else:
                    results.append(_warn("tailscale", "Tailscale available but not connected"))
            else:
                if in_container:
                    results.append(_unreachable("tailscale", "Tailscale runs on host, not in container"))
                else:
                    results.append(_warn("tailscale", f"Tailscale status unknown: {r.stderr}"))
        except FileNotFoundError:
            if in_container:
                results.append(_unreachable("tailscale", "Tailscale not available in container (expected)"))
            else:
                results.append(_warn("tailscale", "Tailscale CLI not functional"))
    else:
        if in_container:
            results.append(_unreachable("tailscale", "Tailscale runs on host, not in container"))
        else:
            results.append(_warn("tailscale", "Tailscale not installed"))

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


def _ok(check_id: str, msg: str) -> Dict[str, object]:
    return {"check": check_id, "status": "PASS", "message": msg}

def _warn(check_id: str, msg: str) -> Dict[str, object]:
    return {"check": check_id, "status": "WARN", "message": msg}

def _fail(check_id: str, msg: str) -> Dict[str, object]:
    return {"check": check_id, "status": "FAIL", "message": msg}

def _skip(check_id: str, msg: str) -> Dict[str, object]:
    return {"check": check_id, "status": "SKIP", "message": msg}

def _unreachable(check_id: str, msg: str) -> Dict[str, object]:
    return {"check": check_id, "status": "UNREACHABLE", "message": msg}

def _info(check_id: str, msg: str) -> Dict[str, object]:
    return {"check": check_id, "status": "INFO", "message": msg}
