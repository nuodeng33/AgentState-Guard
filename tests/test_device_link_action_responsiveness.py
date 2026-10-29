"""Actual asynchronous Core must serve reads during slow lifecycle I/O."""
import asyncio
import threading

import httpx
import pytest

from agentguard.api.server import create_app
from agentguard.storage.db import StateDB


@pytest.mark.parametrize("path", ["enable", "disable", "network/refresh"])
def test_device_lifecycle_io_does_not_block_health_or_work(tmp_path, path):
    db = StateDB(tmp_path / "state.db"); db.connect(); db.close()
    entered, release = threading.Event(), threading.Event()
    threads = []
    class Controller:
        def operation(self):
            threads.append(threading.get_ident()); entered.set()
            assert release.wait(2), "fixture lifecycle gate timed out"
            return {"enabled": True}
        enable = disable = refresh_network = operation
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     device_link_controller=Controller(), discovery_service=object())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            token = (await client.get("/api/session")).json()["token"]
            headers = {"X-Session-Token": token}; loop_thread = threading.get_ident()
            emergency = threading.Timer(.75, release.set); emergency.start()
            pending = asyncio.create_task(client.post("/api/v1/device-link/"+path, headers=headers, json={}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                health = await client.get("/api/health")
                work = await client.get("/api/v1/works", headers=headers)
                reads_preceded_release = not release.is_set()
            finally:
                release.set(); emergency.cancel(); emergency.join(timeout=1)
            assert (await pending).status_code == 200
            assert health.status_code == work.status_code == 200
            assert reads_preceded_release, "Device lifecycle blocked independent reads"
            assert threads and all(t != loop_thread for t in threads)
    asyncio.run(exercise())


def test_concurrent_device_lifecycle_transitions_keep_request_order(tmp_path):
    db = StateDB(tmp_path / "state.db"); db.connect(); db.close()
    entered, release, disabled = threading.Event(), threading.Event(), threading.Event()
    order = []
    class Controller:
        def enable(self):
            order.append("enable-enter"); entered.set()
            assert release.wait(2)
            order.append("enable-exit")
            return {"enabled": True}
        def disable(self):
            order.append("disable-enter"); disabled.set()
            order.append("disable-exit")
            return {"enabled": False}
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     device_link_controller=Controller(), discovery_service=object())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            headers = {"X-Session-Token": (await client.get("/api/session")).json()["token"]}
            emergency = threading.Timer(1, release.set); emergency.start()
            first = asyncio.create_task(client.post("/api/v1/device-link/enable", headers=headers, json={}))
            second = None
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                second = asyncio.create_task(client.post("/api/v1/device-link/disable", headers=headers, json={}))
                await asyncio.sleep(.05)
                assert not disabled.is_set(), "Lifecycle transitions overlapped"
            finally:
                release.set(); emergency.cancel(); emergency.join(timeout=1)
                await first
                if second is not None: await second
            assert order == ["enable-enter", "enable-exit", "disable-enter", "disable-exit"]
    asyncio.run(exercise())


def test_lifecycle_status_is_one_consistent_snapshot_during_disable():
    from agentguard.device_link.lifecycle import DeviceLinkController
    controller = DeviceLinkController(None, None, None)
    class ChangingCandidate:
        @property
        def address(self):
            controller._candidate = None
            controller._enabled = False
            return "192.168.50.10"
        subnet = "192.168.50.0/24"
    controller._candidate = ChangingCandidate()
    controller._enabled = True
    observed = controller.status()
    assert observed["enabled"] is True
    assert observed["address"] == "192.168.50.10"
    assert observed["subnet"] == "192.168.50.0/24"
    assert controller.status()["enabled"] is False
