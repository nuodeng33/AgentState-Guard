"""doctor command — detailed diagnostic with PASS/WARN/FAIL/SKIP/UNREACHABLE."""

import platform
from pathlib import Path

from ..core.runner import run_command, which
from ..discovery.capabilities import CapabilityStatus
from ..discovery.domains.docker_host import DockerHostProbe
from .status import _check_port


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


HOST_SERVICES = {"docker"}


def doctor(config: dict) -> list[dict[str, object]]:
    """Run comprehensive diagnostic checks."""
    results: list[dict[str, object]] = []
    in_container = _in_container()

    # 1. Kernel / container detection
    if in_container:
        results.append(_info("container", "Running inside container; Docker availability is diagnosed separately"))

    # Docker availability is observed independently of the container heuristic.

    # 2. Docker
    docker_bin = which("docker")
    if not docker_bin:
        if in_container:
            results.append(_unreachable("docker-cli",
                "Container is isolated (no Docker socket mounted). "
                "This is expected and correct — the sandbox must not control the host Docker daemon."))
        else:
            results.append(_fail("docker-cli", "Docker CLI not found in PATH"))

    # Docker enumeration is independent of the optional process/tool probe.
    if docker_bin:
        try:
            listing = DockerHostProbe().list_targets()
            if listing.status in {CapabilityStatus.AVAILABLE, CapabilityStatus.DEGRADED} or listing.reason_code == "DOCKER_TARGETS_EMPTY":
                results.append(_ok("docker-daemon", "Local Docker daemon enumerated"))
            else:
                results.append(_unreachable("docker-daemon", listing.reason_code))
            if listing.status in {CapabilityStatus.AVAILABLE, CapabilityStatus.DEGRADED}:
                for target in listing.targets:
                    results.append(_ok("container", f"{target.name}: {target.status}; process/tool capabilities are reported separately"))
            elif listing.reason_code == "DOCKER_TARGETS_EMPTY":
                results.append(_info("container", "No running containers observed"))
            else:
                results.append(_unreachable("container", listing.reason_code))
        except (OSError, RuntimeError, ValueError):
            results.append(_unreachable("container", "DOCKER_TARGET_LIST_FAILED"))
    else:
        results.append(_unreachable("container", "Docker CLI unavailable on the Core host"))

    port = int(config.get("port", 3001))
    results.append(_ok("port", f"Optional Core-host service port {port} is listening") if _check_port(port)
                   else _skip("port", f"Optional Core-host service port {port} is not listening"))

    # 6. Node.js
    if which("node"):
        try:
            r = run_command(["node", "--version"], timeout=5)
            results.append(_ok("node", f"Node {r.stdout}" if r.success else "Node version unavailable"))
        except (OSError, RuntimeError, ValueError) as e:
            results.append(_fail("node", str(e)))
    else:
        results.append(_fail("node", "Node.js not found"))

    # The packaged Core is Python even when no python3 command is on PATH.
    results.append(_ok("python", f"Python {platform.python_version()} (Core runtime)"))

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
    if platform.system() != "Linux":
        results.append(_skip("config-files", "Linux development-fixture config paths do not apply to this Core host"))
    else:
        settings_ok = sum(1 for p in key_paths if p.is_file())
        if settings_ok == len(key_paths):
            results.append(_ok("config-files", "Linux development-fixture config files present"))
        elif settings_ok > 0:
            results.append(_info("config-files", f"{settings_ok}/{len(key_paths)} optional Linux development-fixture config files present"))
        else:
            results.append(_skip("config-files", "Optional Linux development-fixture config files not present"))

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
        except (OSError, RuntimeError, ValueError) as e:
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
