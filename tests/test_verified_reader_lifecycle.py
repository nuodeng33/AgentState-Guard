"""Test-scope ownership must stop lazy readers before global patches can leak."""
import os
import subprocess
import sys
from pathlib import Path

_SCENARIO = r'''
import threading
import time

from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.evidence import ledger
from agentguard.storage.db import StateDB
from tests import test_ledger_read_cache as cache_tests
from tests.test_evidence_ledger import _event
from tests.test_execution_core import setup as setup
from tests.test_execution_failures import (
    test_sqlite_startup_journal_failure_is_caught_and_does_not_accept_requests as _startup_journal_failure,
)
from tests.test_ledger_read_cache import database as database

owned = []
closed_while_patched = []


def test_00_unclosed_testclient_starts_a_real_lazy_reader(tmp_path, monkeypatch):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    with database.transaction():
        ledger.EvidenceLedger().append(database._conn, _event(event_id="previous-test-event"))
    database.close()
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)}, discovery_service=object())
    reader = app.state.verified_agents
    reader._interval = 60
    client = TestClient(app)  # deliberately no lifespan and no explicit cleanup
    client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
    assert client.get("/api/v1/agents").status_code == 200
    deadline = time.monotonic() + 3
    while reader.read()["verification"]["checked_at"] is None:
        assert time.monotonic() < deadline
        time.sleep(.005)
    assert reader._thread.is_alive()
    original_validator = ledger._verify_ledger_rows
    original_close = reader.close

    def test_local_validator(rows, **kwargs):
        return original_validator(rows, **kwargs)

    def close_before_patch_teardown():
        assert ledger._verify_ledger_rows is test_local_validator
        original_close()
        closed_while_patched.append(True)

    monkeypatch.setattr(ledger, "_verify_ledger_rows", test_local_validator)
    monkeypatch.setattr(reader, "close", close_before_patch_teardown)
    owned.append((client, reader))  # prevent GC/finalizers from hiding the leak


def test_01_prior_test_scope_really_stopped_its_owned_worker():
    reader = owned[0][1]
    assert not reader._thread.is_alive(), "previous test's lazy reader survived scope teardown"
    assert reader._stop.is_set()
    assert closed_while_patched == [True], "reader must close before monkeypatch teardown"
    reader.request_refresh()
    assert not reader._thread.is_alive(), "a closed prior-test reader restarted"


def test_02_original_exact_generation_contract_is_not_contaminated(database, monkeypatch):
    reader = owned[0][1]
    original_counted = cache_tests.counted

    def counted_with_controlled_prior_worker_visit(patch):
        calls = original_counted(patch)
        if reader._thread.is_alive():
            observed = threading.Event()
            counted_validator = ledger._verify_ledger_rows

            def watched(rows, **kwargs):
                result = counted_validator(rows, **kwargs)
                if threading.current_thread() is reader._thread:
                    observed.set()
                return result

            patch.setattr(ledger, "_verify_ledger_rows", watched)
            reader.request_refresh()
            assert observed.wait(3), "controlled leaked reader did not visit the real global validator"
        return calls

    monkeypatch.setattr(cache_tests, "counted", counted_with_controlled_prior_worker_visit)
    # Reuse the unchanged real assertion (65 full validations and 64 retained keys).
    cache_tests.test_success_cache_is_bounded_at_64_generations(database, monkeypatch)


def test_03_original_runtime_fixture_teardown_keeps_its_patch_order(setup, monkeypatch):
    _startup_journal_failure(setup, monkeypatch)
'''


def test_scope_teardown_closes_unmanaged_app_reader_before_next_test_patches(tmp_path):
    scenario = tmp_path / "isolated-suite"
    scenario.mkdir()
    # Exercise the actual shared fixtures in a fresh pytest process. The two
    # sequential test scopes would otherwise be hard to observe from one test.
    (scenario / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (scenario / "test_reader_scope.py").write_text(_SCENARIO)
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(root), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(scenario), "-q", "--tb=short",
         "--disable-warnings", "--maxfail=0", "--basetemp", str(tmp_path / "inner-temp")],
        cwd=root, env=environment, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout, result.stdout
