import json
from datetime import UTC, datetime

import pytest

from agentguard.discovery.catalog import (
    DiscoveryCatalog,
    include_historical_sources,
    record_sessions,
    record_sources,
)
from agentguard.discovery.source_identity import (
    docker_source_id,
    legacy_docker_source_id,
    reconcile_source_alias,
    resolve_source,
)
from agentguard.storage.db import StateDB


@pytest.fixture
def database(tmp_path):
    db = StateDB(tmp_path / 'state.db')
    db.connect()
    yield db
    db.close()


def test_verified_alias_preserves_disabled_intent_session_id_and_hides_duplicate(database):
    canonical, legacy = docker_source_id('a' * 64), legacy_docker_source_id('a' * 64)
    record_sessions(database, legacy, {'status': 'AVAILABLE', 'provider': 'CODEX',
        'sessions': [{'session_id': 'native-1', 'cwd': '/project'}]})
    old_id = database._conn.execute('SELECT id FROM protection_sessions').fetchone()[0]
    with database.transaction() as conn:
        conn.execute('INSERT INTO discovery_source_settings VALUES (?,?,?,?)', (legacy, 0, 3, 'now'))
        conn.execute('INSERT INTO discovery_source_settings VALUES (?,?,?,?)', (canonical, 1, 4, 'now'))
        reconcile_source_alias(conn, 'a' * 64)
    assert resolve_source(database._conn, legacy) == canonical
    assert database._conn.execute('SELECT enabled,revision FROM discovery_source_settings WHERE source_id=?', (canonical,)).fetchone() == (0, 5)
    assert database._conn.execute('SELECT id,source_id FROM protection_sessions').fetchone() == (old_id, canonical)
    result = record_sources(database, {'items': [{'source_id': canonical, 'label': 'container'}]})
    assert [item['source_id'] for item in result['items']] == [canonical]
    with database.transaction() as conn:
        reconcile_source_alias(conn, 'a' * 64)
    assert database._conn.execute('SELECT revision FROM discovery_source_settings').fetchone()[0] == 5


def test_same_provider_session_id_isolated_by_store_and_retry_reuses_binding(database):
    result = {'status': 'AVAILABLE', 'provider': 'CODEX', 'sessions': [
        {'session_id': 'same-id', 'title': 'not persisted', 'cwd': '/project'}]}
    first = record_sessions(database, 'local', {**result, 'store_id': 'store-a'})
    second = record_sessions(database, 'local', {**result, 'store_id': 'store-b'})
    retried = record_sessions(database, 'local', {**result, 'store_id': 'store-a'})
    assert first['sessions'][0]['binding_id'] != second['sessions'][0]['binding_id']
    assert first['sessions'][0]['binding_id'] == retried['sessions'][0]['binding_id']
    assert all('title' not in json.loads(row[0]) for row in database._conn.execute('SELECT metadata_json FROM protection_sessions'))


class Discovery:
    def sources(self):
        return {'items': [{'source_id': 'local', 'kind': 'NATIVE', 'label': 'Local',
            'state': 'OBSERVED', 'enabled': True, 'freshness': 'FRESH'}]}


def test_multi_store_selection_does_not_guess(database):
    catalog = DiscoveryCatalog(database, None, Discovery())
    for root in ('/one', '/two'):
        catalog._save_store('local', 'CODEX', root, {'identity': root, 'status': 'AVAILABLE'})
    result = catalog.read_sessions('local')
    assert result['reason_code'] == 'SESSION_STORE_SELECTION_REQUIRED'
    assert result['sessions'] == []


def test_display_instances_are_local_facts_and_never_control_capability(database):
    catalog = DiscoveryCatalog(database, None, Discovery())
    record_sources(database, Discovery().sources())
    with database.transaction() as conn:
        conn.execute('INSERT INTO discovery_applications(id,source_id,path,metadata_json,observed_at) VALUES (?,?,?,?,?)',
            ('application-a', 'local', 'C:/codex.exe', json.dumps({'instances': [
                {'agent_id': 'agent-a', 'pid': 100, 'cwd': 'C:/work'}]}), datetime.now(UTC).isoformat()))
    item = catalog.enrich_agents({'items': [{'agent_id': 'agent-a'}]})['items'][0]
    assert item['program_path'] == 'C:/codex.exe'
    assert item['source_label'] == 'Local'
    assert item['control_capabilities']['send'] is False


def test_process_observation_does_not_replace_installation_facts(database):
    catalog = DiscoveryCatalog(database, None, Discovery())
    program = {'path': 'C:/tools/codex.exe', 'status': 'AVAILABLE', 'version': '1.2', 'identity': 'entry-id'}
    catalog._save_application('local', program)
    catalog._save_application('local', {**program, 'status': 'RUNNING', 'version': None, 'identity': 'process-path'},
                              instances=[{'pid': 42, 'agent_id': 'agent-42'}])
    item = catalog.applications('local')['items'][0]
    assert item['status'] == 'AVAILABLE' and item['version'] == '1.2'
    assert item['identity'] == 'entry-id' and item['running'] is True
    assert item['installation_freshness'] == 'FRESH'
    with database.transaction() as conn:
        conn.execute("UPDATE discovery_applications SET metadata_json=json_set(metadata_json,'$.installation_observed_at','2020-01-01T00:00:00+00:00')")
    item = catalog.applications('local')['items'][0]
    assert item['installation_freshness'] == 'STALE' and item['running'] is True


def test_verified_alias_replays_old_receipt_and_restores_disconnect(tmp_path):
    from contextlib import closing

    from agentguard.recovery.protection import ProtectionError, ProtectionService

    class Domains:
        def __init__(self):
            self.disconnected = []

        def disconnect(self, source_id):
            self.disconnected.append(source_id)
            return {'source_id': source_id, 'connected': False}

    domains = Domains()
    canonical, legacy = docker_source_id('b' * 64), legacy_docker_source_id('b' * 64)
    state = tmp_path / 'state'
    with closing(ProtectionService(state / 'state.db', state, domains)) as service:
        queued = service.source_action(legacy, 'disconnect', 'old-receipt')
        original = service.execute_source(queued['id'])
        with service.db.transaction() as conn:
            reconcile_source_alias(conn, 'b' * 64)
        assert service.source_action(canonical, 'disconnect', 'old-receipt') == original
        assert service.source_action(legacy, 'disconnect', 'old-receipt') == original
        with pytest.raises(ProtectionError, match='PROTECTION_REQUEST_REUSED'):
            service.source_action(canonical, 'connect', 'old-receipt')
    domains.disconnected.clear()
    with closing(ProtectionService(state / 'state.db', state, domains)) as reopened:
        reopened.restore_disconnections()
    assert domains.disconnected == [canonical]


def test_source_history_poll_does_not_compete_with_writer(database):
    record_sources(database, {'items': [{'source_id': 'gone', 'label': 'Old source'}]})
    reader = StateDB(database.db_path)
    reader.connect()
    try:
        with database.transaction():
            result = include_historical_sources(reader, Discovery().sources())
        old = next(item for item in result['items'] if item['source_id'] == 'gone')
        assert old['state'] == 'NOT_CURRENT' and old['agent_count'] == 0
        assert old['freshness'] == 'STALE'
    finally:
        reader.close()
