"""Fixed, read-only /proc protocol for a Docker container process observation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

MAX_PROCESSES = 1024
MAX_PROBE_BYTES = 512 * 1024
_BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")

# Passed as one literal argument to python3 -I -B -c. It reads process metadata
# only. Bounded argv inspection extracts a script locator, never prompt arguments.
CONTAINER_PROCESS_SOURCE = r'''
import json
import os
import re
import sys

MAX_PROCESSES = 1024
MAX_JSON_BYTES = 256 * 1024
BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")

def safe_text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(
        ord(char) < 32 or ord(char) == 127 for char in value
    )

partial_reason = None
try:
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as source:
        boot_id = source.read(64).strip().lower()
    if not BOOT_ID.fullmatch(boot_id) or not any(char != "0" for char in boot_id if char != "-"):
        boot_id = None
        partial_reason = "BOOT_ID_UNAVAILABLE"
except OSError:
    boot_id = None
    partial_reason = "BOOT_ID_UNAVAILABLE"

processes = []
used_bytes = 0
try:
    pids = sorted(int(item) for item in os.listdir("/proc") if item.isdecimal())
except OSError:
    sys.exit(2)

if len(pids) > MAX_PROCESSES:
    partial_reason = "PROCESS_LIMIT_REACHED"
for pid in pids[:MAX_PROCESSES]:
    root = "/proc/" + str(pid)
    try:
        with open(root + "/stat", encoding="utf-8", errors="replace") as source:
            stat_text = source.read(4096)
        with open(root + "/comm", encoding="utf-8", errors="replace") as source:
            name = source.read(256).strip()
        tail = stat_text[stat_text.rfind(")") + 1:].split()
        ppid = int(tail[1])
        start_ticks = int(tail[19])
        if not safe_text(name, 128) or ppid < 0 or start_ticks <= 0:
            partial_reason = partial_reason or "PROCESS_INACCESSIBLE"
            continue
    except FileNotFoundError:
        continue
    except (OSError, ValueError, IndexError):
        partial_reason = partial_reason or "PROCESS_INACCESSIBLE"
        continue

    item = {"pid": pid, "ppid": ppid, "name": name, "start_ticks": start_ticks}
    for link, key, limit in (("exe", "executable_basename", 128), ("cwd", "cwd", 2048)):
        try:
            value = os.readlink(root + "/" + link)
        except FileNotFoundError:
            continue
        except OSError:
            partial_reason = partial_reason or "PROCESS_ACCESS_DENIED"
            continue
        if key == "executable_basename":
            if safe_text(value, 4096) and value.startswith("/"):
                item["path"] = value
            value = os.path.basename(value)
        if safe_text(value, limit) and (key != "cwd" or value.startswith("/")):
            item[key] = value
        else:
            partial_reason = partial_reason or "PROCESS_INACCESSIBLE"

    if "_asg_applications" in globals() and item.get("path"):
        try:
            with open(root + "/cmdline", "rb") as command:
                argv = command.read(8193)
            if len(argv) <= 8192:
                values = [value.decode("utf-8", "replace") for value in argv.split(b"\0")[:4]]
                locator = _asg_applications.entry_from_argv(item["path"], values, cwd=item.get("cwd"))
                if isinstance(locator, dict):
                    if locator.get("entry_path"):
                        observed = _asg_applications.probe_entry(locator["entry_path"])
                        canonical = observed.get("entry_path")
                        if observed.get("status") == "AVAILABLE" and safe_text(canonical, 4096) and canonical.startswith("/"):
                            locator = {**locator, **observed}
                    for key in ("entry_path", "package_name", "version"):
                        if isinstance(locator.get(key), str) and safe_text(locator[key], 4096):
                            item[key] = locator[key]
        except (OSError, ValueError, AttributeError):
            pass
    item_size = len(json.dumps(item, ensure_ascii=True, separators=(",", ":")))
    if used_bytes + item_size > MAX_JSON_BYTES - 1024:
        partial_reason = "PROCESS_LIMIT_REACHED"
        break
    used_bytes += item_size
    processes.append(item)

result = {"schema_version": 1, "boot_id": boot_id,
          "partial_reason": partial_reason, "processes": processes}
if __name__ != "asg_process_capture":
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
'''


class ContainerProcessProtocolError(ValueError):
    """The bounded container output does not match the fixed protocol."""


@dataclass(frozen=True)
class NormalizedContainerProcesses:
    boot_id: str | None
    partial_reason: str | None
    processes: tuple[dict[str, int | str], ...]


def parse_container_processes(payload: bytes) -> NormalizedContainerProcesses:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PROBE_BYTES:
        raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
    try:
        value: Any = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "boot_id", "partial_reason", "processes"
    } or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
    boot_id = value["boot_id"]
    if boot_id is not None and (
        not isinstance(boot_id, str)
        or _BOOT_ID.fullmatch(boot_id) is None
        or set(boot_id.replace("-", "")) == {"0"}
    ):
        raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
    partial_reason = value["partial_reason"]
    if partial_reason not in {
        None,
        "BOOT_ID_UNAVAILABLE",
        "PROCESS_ACCESS_DENIED",
        "PROCESS_INACCESSIBLE",
        "PROCESS_LIMIT_REACHED",
    } or (boot_id is None and partial_reason is None):
        raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
    raw_processes = value["processes"]
    if not isinstance(raw_processes, list) or len(raw_processes) > MAX_PROCESSES:
        raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
    processes: list[dict[str, int | str]] = []
    seen_pids: set[int] = set()
    for item in raw_processes:
        if not isinstance(item, dict) or not {"pid", "ppid", "name", "start_ticks"} <= set(item) or not set(item) <= {
            "pid", "ppid", "name", "start_ticks", "cwd", "executable_basename", "path", "entry_path", "package_name", "version"
        }:
            raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
        pid, ppid, name, ticks = (item[key] for key in ("pid", "ppid", "name", "start_ticks"))
        if (
            type(pid) is not int or not 1 <= pid <= 2**31 - 1
            or pid in seen_pids
            or type(ppid) is not int or not 0 <= ppid <= 2**31 - 1
            or type(ticks) is not int or not 0 < ticks <= 2**63 - 1
            or not _safe_text(name, 128)
        ):
            raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
        executable = item.get("executable_basename")
        cwd = item.get("cwd")
        if executable is not None and (
            not _safe_text(executable, 128) or "/" in executable or "\\" in executable
        ):
            raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
        if cwd is not None and (not _safe_text(cwd, 2048) or not cwd.startswith("/")):
            raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
        for key in ("path", "entry_path", "package_name", "version"):
            if key in item and (not _safe_text(item[key], 4096) or key in {"path", "entry_path"} and not item[key].startswith("/")):
                raise ContainerProcessProtocolError("DOCKER_PROCESS_PROTOCOL_INVALID")
        seen_pids.add(pid)
        processes.append(item)
    return NormalizedContainerProcesses(boot_id, partial_reason, tuple(processes))


def _safe_text(value: object, limit: int) -> bool:
    return bool(
        isinstance(value, str)
        and 0 < len(value) <= limit
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def collect_processes(applications=None):
    scope = {"__name__": "asg_process_capture"}
    if applications is not None:
        scope["_asg_applications"] = applications
    exec(compile(CONTAINER_PROCESS_SOURCE, "asg_process_probe", "exec"), scope)  # noqa: S102 - fixed repository probe, no client source
    return scope["result"]


def process_probe_source():
    from pathlib import Path

    from .. import application_probe
    source = Path(application_probe.__file__).with_suffix('.py').read_text(encoding="utf-8")
    return ("import types,sys\n_asg_applications=types.ModuleType('_asg_applications')\n"
            "sys.modules['_asg_applications']=_asg_applications\n"
            + "exec(compile(" + repr(source) + ", 'asg_application_probe', 'exec'), _asg_applications.__dict__)\n"
            + CONTAINER_PROCESS_SOURCE)
