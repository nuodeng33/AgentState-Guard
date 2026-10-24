"""Real completion must survive channel run-id clearing during Core shutdown."""
import threading
from contextlib import contextmanager

import pytest

from tests import test_pending_instructions as pending


@pytest.fixture
def setup(tmp_path):
    yield from pending.setup.__wrapped__(tmp_path)


def test_finalization_clearing_channel_during_shutdown_keeps_completed_evidence(setup, monkeypatch):
    runtime = setup['runtime']
    finalizer_entered, release_finalizer, finalized = (threading.Event() for _ in range(3))
    original_finalize = runtime._finalize
    finalizer_errors = []

    def gated_finalize(session_id, run_id):
        finalizer_entered.set()
        try:
            assert release_finalizer.wait(8), 'Test failed to release actual file finalization'
            original_finalize(session_id, run_id)
        except BaseException as error:
            finalizer_errors.append(error)
            raise
        finally:
            finalized.set()

    monkeypatch.setattr(runtime, '_finalize', gated_finalize)
    session, run_id, providers = pending.boot(setup)
    provider = providers[0]
    provider.finish()
    assert finalizer_entered.wait(8)
    assert pending.run_read(setup, session['id'], run_id)['state'] == 'FINALIZING'
    original_journal = runtime.journal
    shutdown_thread = threading.current_thread()
    shutdown_journals = 0

    @contextmanager
    def journal_with_finalization_race():
        nonlocal shutdown_journals
        if threading.current_thread() is shutdown_thread:
            shutdown_journals += 1
            if shutdown_journals == 2:
                # close has already selected the active channel/run. Complete the
                # real protected file capture before its next database lookup.
                release_finalizer.set()
                assert finalized.wait(8), 'Actual finalization did not complete'
                assert not finalizer_errors
                assert runtime.channels[session['id']]['run_id'] is None
        with original_journal() as service:
            yield service

    try:
        with monkeypatch.context() as patch:
            patch.setattr(runtime, 'journal', journal_with_finalization_race)
            runtime.close()
    finally:
        # Preserve fixture cleanup even if the unpatched shutdown raises.
        release_finalizer.set()
    run = pending.run_read(setup, session['id'], run_id)
    assert run['state'] == 'COMPLETED'
    assert run['file_result']['status'] == 'AVAILABLE'
    assert setup['owner']['cancels'] == 0
    assert not runtime.channels
    assert provider.alive is False
    assert runtime.pool._shutdown is True
    with original_journal() as service:
        types = [row[0] for row in service.conn.execute(
            'SELECT type FROM work_run_events WHERE run_id=? ORDER BY seq', (run_id,))]
    assert types.count('RUN_COMPLETED') == 1
    assert not {'CANCEL_REQUESTED', 'CANCEL_SENT', 'RUN_FAILED', 'RUN_UNCERTAIN'} & set(types)
