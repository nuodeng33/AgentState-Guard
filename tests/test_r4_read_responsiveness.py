"""R4 integrity work must not stall independent authenticated reads.

In-process ASGI only; disposable databases, no live discovery or native process.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from agentguard.api import r4_projection
from agentguard.api.server import create_app
from agentguard.evidence import discovery_adapter
from agentguard.storage.db import StateDB


@pytest.mark.parametrize("path", [
    "/api/v1/runtime", "/api/v1/agents", "/api/v1/supervision",
    "/api/v1/recovery", "/api/v1/changes", "/api/v1/evidence/fixture-event",
])
def test_slow_ledger_verification_does_not_block_health_or_work_reads(tmp_path: Path, monkeypatch, path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    database.close()
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     discovery_service=object())
    entered, release = threading.Event(), threading.Event()
    verified_threads = []
    original = r4_projection.verify_ledger

    def bounded_verify(connection):
        verified_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(2), "fixture verification gate timed out"
        # Real verification still runs, on the same connection and owning thread.
        return original(connection)

    monkeypatch.setattr(r4_projection, "verify_ledger", bounded_verify)
    monkeypatch.setattr(discovery_adapter, "verify_ledger_snapshot", bounded_verify)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as client:
            token = (await client.get("/api/session")).json()["token"]
            headers = {"X-Session-Token": token}
            loop_thread = threading.get_ident()
            # Bounded emergency release lets the old blocking implementation fail cleanly.
            fallback = threading.Timer(.75, release.set)
            fallback.start()
            request = asyncio.create_task(client.get(path, headers=headers))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                health = await client.get("/api/health")
                works = await client.get("/api/v1/works", headers=headers)
                independent_reads_finished_before_release = not release.is_set()
            finally:
                release.set()
                fallback.cancel()
                fallback.join(timeout=1)
            response = await request
            assert health.status_code == works.status_code == 200
            assert response.status_code in {200, 404}
            assert independent_reads_finished_before_release, "R4 verification stalled independent reads"
            assert verified_threads and all(value != loop_thread for value in verified_threads)

    asyncio.run(exercise())
