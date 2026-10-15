#!/usr/bin/env python3
# ruff: noqa: N999
"""Read-only live discovery report: counts, statuses, and program locations."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentguard.discovery import application_probe, session_metadata
from agentguard.discovery.domains.docker_host import DockerHostProbe
from agentguard.discovery.signatures import COMMAND_NAMES

_CONTAINER_WORKER = r'''
import json, shutil, sys, types
sources = json.loads(sys.stdin.buffer.read(131072))
modules = {}
for name in ('application_probe', 'session_metadata'):
    module = types.ModuleType(name)
    exec(compile(sources[name], name + '.py', 'exec'), module.__dict__)
    modules[name] = module
applications = modules['application_probe']
sessions = modules['session_metadata']
located = [shutil.which(name) for name in sources['commands']]
entries = [applications.probe_entry(path) for path in dict.fromkeys(located) if path]
paths = sorted(item['path'] for item in entries if item['status'] == 'AVAILABLE')
result = {'programs': {'status': 'AVAILABLE' if len(paths) == len(sources['commands']) else
                                 'PARTIAL' if paths else 'EMPTY',
                       'count': len(paths), 'paths': paths},
          'stores': []}
for candidate in sessions.default_stores():
    provider, path = candidate['provider'], candidate['path']
    inspection = sessions.inspect_store(provider, path)
    item = {'provider': provider, 'locator_basis': candidate['locator_basis'],
            'status': inspection['status'],
            'reason_code': inspection['reason_code'], 'observed_sessions': 0,
            'has_more': False}
    if inspection['status'] in ('AVAILABLE', 'DEGRADED'):
        offset = 0
        for page in range(20):
            read = sessions.read_store(provider, path, limit=50, offset=offset)
            item.update(status=read['status'], reason_code=read['reason_code'],
                        observed_sessions=item['observed_sessions'] + len(read['sessions']),
                        has_more=read['has_more'])
            if provider == 'ZCODE':
                item['matched'] = item.get('matched', 0) + sum(
                    s.get('mapping_status') == 'MATCHED' for s in read['sessions'])
                item['unmatched'] = item.get('unmatched', 0) + sum(
                    s.get('mapping_status') == 'UNMATCHED' for s in read['sessions'])
            if not read['has_more'] or read['next_offset'] is None:
                break
            offset = read['next_offset']
    result['stores'].append(item)
sys.stdout.write(json.dumps(result, separators=(',', ':'), ensure_ascii=True) + '\n')
'''


def _programs(names: tuple[str, ...]) -> dict:
    located = [shutil.which(name) for name in names]
    entries = [application_probe.probe_entry(path) for path in dict.fromkeys(located) if path]
    paths = sorted(item["path"] for item in entries if item["status"] == "AVAILABLE")
    return {"status": "AVAILABLE" if len(paths) == len(names) else
                      "PARTIAL" if paths else "EMPTY", "count": len(paths), "paths": paths}


def _read_pages(provider: str, path: str, max_pages: int = 20) -> dict:
    inspection = session_metadata.inspect_store(provider, path)
    report = {"status": inspection["status"], "reason_code": inspection["reason_code"],
              "observed_sessions": 0, "has_more": False}
    if inspection["status"] not in {"AVAILABLE", "DEGRADED"}:
        return report
    offset = 0
    for _ in range(max_pages):
        result = session_metadata.read_store(provider, path, limit=50, offset=offset)
        report.update(status=result["status"], reason_code=result["reason_code"],
                      observed_sessions=report["observed_sessions"] + len(result["sessions"]),
                      has_more=result["has_more"])
        if provider == "ZCODE":
            report["matched"] = report.get("matched", 0) + sum(
                row.get("mapping_status") == "MATCHED" for row in result["sessions"])
            report["unmatched"] = report.get("unmatched", 0) + sum(
                row.get("mapping_status") == "UNMATCHED" for row in result["sessions"])
        if not result["has_more"] or result["next_offset"] is None:
            break
        offset = result["next_offset"]
    return report


def _clean_container_result(value: object) -> dict:
    failure = {"status": "UNKNOWN", "reason_code": "CONTAINER_METADATA_PROBE_FAILED"}
    if (not isinstance(value, dict) or not isinstance(value.get("programs"), dict)
            or not isinstance(value.get("stores"), list) or len(value["stores"]) > 16):
        return failure
    program = value["programs"]
    raw_paths = program.get("paths")
    if not isinstance(raw_paths, list) or len(raw_paths) > len(COMMAND_NAMES):
        return failure
    paths = [path for path in raw_paths if isinstance(path, str) and path.startswith("/")
             and len(path) <= 4096 and not any(ord(char) < 32 for char in path)]
    if len(paths) != len(raw_paths):
        return failure
    status = program.get("status")
    if not isinstance(status, str) or status not in {"AVAILABLE", "PARTIAL", "EMPTY"}:
        return failure
    stores = []
    for raw in value["stores"]:
        if (not isinstance(raw, dict) or not isinstance(raw.get("provider"), str)
                or raw["provider"] not in session_metadata.PROVIDERS):
            return failure
        observed = raw.get("observed_sessions")
        if type(observed) is not int or not 0 <= observed <= 1000:
            return failure
        raw_status = raw.get("status")
        row = {"provider": raw["provider"], "status": raw_status
               if isinstance(raw_status, str) and raw_status in {"AVAILABLE", "DEGRADED", "EMPTY", "UNKNOWN"}
               else "UNKNOWN", "observed_sessions": observed,
               "has_more": raw.get("has_more") is True}
        reason = raw.get("reason_code")
        row["reason_code"] = reason if isinstance(reason, str) and re.fullmatch(
            r"[A-Z0-9_]{1,96}", reason) else "CONTAINER_METADATA_PROBE_FAILED"
        basis = raw.get("locator_basis")
        if isinstance(basis, str) and basis in {"SERVICE_HOME", "OS_USER_PROFILE", "EXPLICIT_ENV"}:
            row["locator_basis"] = basis
        if raw["provider"] == "ZCODE":
            for key in ("matched", "unmatched"):
                count = raw.get(key)
                if type(count) is int and 0 <= count <= observed:
                    row[key] = count
        stores.append(row)
    return {"status": "AVAILABLE", "programs": {"status": status, "count": len(paths),
                                                  "paths": paths}, "stores": stores}


def _container_probe(docker: str, env: dict[str, str], container_id: str) -> dict:
    source = {"application_probe": Path(application_probe.__file__).read_text(encoding="utf-8"),
              "session_metadata": Path(session_metadata.__file__).read_text(encoding="utf-8"),
              "commands": COMMAND_NAMES}
    try:
        completed = subprocess.run(
            [docker, "exec", "-i", container_id, "python3", "-I", "-B", "-c", _CONTAINER_WORKER],
            input=json.dumps(source, ensure_ascii=True).encode(), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode or len(completed.stdout) > 65536:
            return {"status": "UNKNOWN", "reason_code": "CONTAINER_METADATA_PROBE_FAILED"}
        result = json.loads(completed.stdout)
        return _clean_container_result(result)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "UNKNOWN", "reason_code": "CONTAINER_METADATA_PROBE_FAILED"}


def _docker(max_containers: int) -> dict:
    probe = DockerHostProbe()
    listing = probe.list_targets()
    report = {"status": listing.status.value, "reason_code": listing.reason_code,
              "running_containers": len(listing.targets), "reported_containers": 0,
              "truncated": len(listing.targets) > max_containers, "containers": []}
    for ordinal, target in enumerate(listing.targets[:max_containers], start=1):
        transport = probe.session_transport(target)
        if transport is None:
            item = {"status": "UNKNOWN", "reason_code": "CONTAINER_TRANSPORT_UNAVAILABLE"}
        else:
            item = _container_probe(transport[0], transport[1], target.container_id)
        report["containers"].append({"ordinal": ordinal, **item})
    report["reported_containers"] = len(report["containers"])
    return report


def _http_get(base_url: str, route: str, token: str | None = None) -> tuple[int, dict]:
    headers = {"X-Session-Token": token} if token else {}
    request = Request(base_url + route, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=8) as response:
            data = response.read(256 * 1024 + 1)
            if len(data) > 256 * 1024:
                return response.status, {}
            value = json.loads(data)
            return response.status, value if isinstance(value, dict) else {}
    except HTTPError as error:
        return error.code, {}
    except (OSError, ValueError, URLError):
        return 0, {}


def _api(base_url: str) -> dict:
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in
            {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password or
            parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        return {"status": "UNKNOWN", "reason_code": "BASE_URL_NOT_LOOPBACK"}
    base_url = base_url.rstrip("/")
    code, session = _http_get(base_url, "/api/session")
    token = session.get("token")
    if code != 200 or not isinstance(token, str):
        return {"status": "UNKNOWN", "reason_code": "API_SESSION_UNAVAILABLE", "http_status": code}
    code, sources = _http_get(base_url, "/api/v1/discovery/sources", token)
    if code != 200 or not isinstance(sources.get("items"), list):
        return {"status": "UNKNOWN", "reason_code": "API_SOURCES_UNAVAILABLE", "http_status": code}
    report = {"status": "AVAILABLE", "source_count": len(sources["items"]),
              "programs": {"count": 0, "paths": []}, "stores": []}
    code, applications = _http_get(base_url, "/api/v1/discovery/applications?limit=100", token)
    if code == 200 and isinstance(applications.get("items"), list):
        paths = sorted({item["path"] for item in applications["items"]
                        if isinstance(item, dict) and isinstance(item.get("path"), str)})
        report["programs"] = {"count": len(paths), "paths": paths,
                              "has_more": bool(applications.get("has_more"))}
    else:
        report["programs"] = {"status": "UNKNOWN", "http_status": code,
                              "count": 0, "paths": []}
    for source in sources["items"]:
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            continue
        source_id = source["source_id"]
        route = "/api/v1/discovery/sources/" + quote(source_id, safe="") + "/session-stores"
        code, stores = _http_get(base_url, route, token)
        if code != 200 or not isinstance(stores.get("items"), list):
            report["stores"].append({"status": "UNKNOWN", "http_status": code,
                                     "observed_sessions": 0})
            continue
        for store in stores["items"]:
            if not isinstance(store, dict) or not isinstance(store.get("id"), str):
                continue
            query = urlencode({"store_id": store["id"], "limit": 50})
            session_route = "/api/v1/discovery/sources/" + quote(source_id, safe="") + "/sessions?" + query
            code, result = _http_get(base_url, session_route, token)
            rows = result.get("sessions") if isinstance(result.get("sessions"), list) else []
            report["stores"].append({"provider": store.get("provider"),
                                     "status": result.get("status", "UNKNOWN"),
                                     "http_status": code, "observed_sessions": len(rows),
                                     "has_more": bool(result.get("has_more"))})
    report["store_count"] = len(report["stores"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Optional local test API, queried with GET only")
    parser.add_argument("--max-containers", type=int, default=16)
    arguments = parser.parse_args()
    if not 0 <= arguments.max_containers <= 256:
        parser.error("--max-containers must be between 0 and 256")
    zcode_candidates = [item for item in session_metadata.default_stores()
                        if item["provider"] == "ZCODE"]
    result = {"schema_version": "discovery-live-verification-1",
              "native_zcode": {"candidate_count": len(zcode_candidates), "stores": [
                  {"locator_basis": candidate["locator_basis"],
                   **_read_pages("ZCODE", candidate["path"])}
                  for candidate in zcode_candidates]},
              "docker": _docker(arguments.max_containers)}
    if arguments.base_url:
        result["api"] = _api(arguments.base_url)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
