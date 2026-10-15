"""Opt-in real native/Docker/WSL transport tests using disposable session files."""

import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.recovery.protection_domains import DomainRegistry

pytestmark = pytest.mark.skipif(os.environ.get("AGENTGUARD_LIVE_DOMAINS") != "1",
                               reason="explicit live fixture only")

WRITER = """
import json,pathlib,shutil,sys
root=pathlib.Path(sys.argv[1]); action=sys.argv[2]
if not root.name.startswith('asg-session-') or root.is_symlink():
    raise ValueError('invalid disposable fixture')
if action=='cleanup':
    if root.parent != pathlib.Path('/tmp') or root.resolve().parent != root.parent:
        raise ValueError('cleanup outside temporary directory')
    shutil.rmtree(root)
else:
    folder=root/'sessions'/'2026'/'09'/'30'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'rollout-test.jsonl').write_text(json.dumps({'type':'session_meta',
        'payload':{'id':'test-session','cwd':'/work/before' if action=='seed' else '/work/after!'}})+'\\n')
"""


@pytest.mark.parametrize("kind", ["NATIVE", "CONTAINER", "WSL"])
def test_real_session_api_revision_change_and_disconnect(tmp_path, kind):
    state = tmp_path / "state"
    domains = DomainRegistry(state)
    sources = domains.sources()
    source = next(item for item in sources if item["kind"] == kind and (
        kind == "NATIVE" or item["label"] == ("agent-k3" if kind == "CONTAINER" else "Ubuntu")))
    if kind == "WSL":
        domains.connect(source["source_id"], start=True)
    prefix = ([sys.executable] if kind == "NATIVE" else
              ["docker", "exec", source["container_id"], "python3"] if kind == "CONTAINER" else
              ["wsl.exe", "--distribution", source["label"], "--exec", "python3"])
    root = str(tmp_path / ("asg-session-" + uuid4().hex)) if kind == "NATIVE" else "/tmp/asg-session-" + uuid4().hex

    def write(action):
        subprocess.run([*prefix, "-c", WRITER, root, action], check=True, capture_output=True, timeout=20)

    class Observed:
        def sources(self):
            return {"items": [{"source_id": source["source_id"], "kind": kind,
                "state": "OBSERVED", "enabled": True, "freshness": "FRESH"}]}

    seeded = False
    try:
        write("seed")
        seeded = True
        app = create_app(state_db_path=state / "state.db", config={"base_dir": str(state)},
                         discovery_service=Observed(), protection_domains=domains)
        client = TestClient(app)
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        registered = client.post("/api/v1/discovery/session-stores", json={
            "source_id": source["source_id"], "provider": "CODEX", "path": root})
        assert registered.status_code == 201, registered.text
        route = f"/api/v1/discovery/sources/{source['source_id']}/sessions"
        params = {"store_id": registered.json()["store_id"]}
        first = client.get(route, params=params).json()
        assert first["sessions"][0]["cwd"] == "/work/before"
        assert first["revision"]
        unchanged = client.get(route, params=params).json()
        assert first["sessions"] == unchanged["sessions"]
        write("change")
        changed = client.get(route, params=params).json()
        assert changed["sessions"][0]["cwd"] == "/work/after!"
        assert changed["revision"] != first["revision"]
        assert changed["sessions"][0]["binding_id"] == first["sessions"][0]["binding_id"]
        assert all(value is False for key, value in changed["sessions"][0]["control_capabilities"].items()
                   if key in {"create", "continue", "send", "stream", "cancel"})
        if kind != "NATIVE":
            domains.disconnect(source["source_id"])
            offline = client.get(route, params=params)
            assert offline.status_code in {409, 503}
            assert offline.json()["reason_code"] == "DOMAIN_DISCONNECTED"
        print(json.dumps({"kind": kind, "read": True, "revision": True, "change": True,
                          "external_control": False}))
    finally:
        domains.close()
        if seeded and kind != "NATIVE":
            write("cleanup")
