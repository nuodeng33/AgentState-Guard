"""Current pairing protocol must tolerate confirmation polls and durable binding failure."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from agentguard.device_link.crypto import (
    FakeClock,
    generate_ecdsa_p256_keypair,
    public_key_to_der,
)
from agentguard.device_link.errors import DeviceLinkError
from agentguard.device_link.gateway import (
    DeviceLinkGateway,
    DeviceRegistry,
    GatewayConfig,
)
from agentguard.device_link.pairing import PairState
from agentguard.device_link.persistence import DeviceBindingStore
from agentguard.storage.db import StateDB


def gateway(registry=None):
    private, public = generate_ecdsa_p256_keypair()
    return DeviceLinkGateway('desktop', public_key_to_der(public), private,
                             config=GatewayConfig(single_device=True), device_registry=registry)


def pending(value, device="android-a"):
    invitation = value.create_pairing_invitation('192.168.50.10')
    sid = invitation['session_id']
    accepted = value.accept_pairing_ticket(sid, invitation['ticket'], device, 'aa' * 32)
    _private, public = generate_ecdsa_p256_keypair()
    public_der = public_key_to_der(public)
    value.pair_start_sas_scoped(sid, accepted['pairing_token'], public_der.hex())
    return sid, accepted['pairing_token'], public_der


@pytest.mark.parametrize('first', ['android', 'desktop'])
def test_both_confirmation_orders_and_same_side_polls(first):
    value = gateway(); sid, token, public = pending(value)
    actions = {'android': lambda: value.pair_android_confirm(sid, token, True),
               'desktop': lambda: value.pair_desktop_confirm(sid, True)}
    other = 'desktop' if first == 'android' else 'android'
    assert actions[first]()['state'] == 'sas_pending'
    assert actions[first]()['state'] == 'sas_pending'
    assert actions[other]()['state'] == 'confirmed_both'
    assert actions[first]()['state'] == 'confirmed_both'
    assert actions[other]()['state'] == 'confirmed_both'
    result = value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')
    assert result['status'] == 'bound'
    with pytest.raises(DeviceLinkError):
        value.pair_android_confirm(sid, token, True)
    with pytest.raises(DeviceLinkError):
        value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')


def test_failed_binding_does_not_consume_or_mint_tokens(monkeypatch):
    value = gateway(); sid, token, public = pending(value)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    original = value.devices.add
    def refuse(*args, **kwargs):
        raise ValueError('Injected registry refusal')
    monkeypatch.setattr(value.devices, 'add', refuse)
    with pytest.raises(DeviceLinkError):
        value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')
    session = value.pairing_mgr.get(sid)
    assert session.state is PairState.CONFIRMED_BOTH
    assert session.pairing_secret and value.devices.count() == 0
    assert not value._sessions and not value._product_tokens
    monkeypatch.setattr(value.devices, 'add', original)
    assert value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')['status'] == 'bound'


def test_single_device_capacity_is_atomic_across_registry_instances(tmp_path, monkeypatch):
    db = StateDB(tmp_path / 'state.db'); db.connect()
    stores = [DeviceBindingStore(db), DeviceBindingStore(db)]
    db.close()
    keys = [public_key_to_der(generate_ecdsa_p256_keypair()[1]) for _ in stores]
    barrier = Barrier(2)
    for store in stores:
        original = store.count
        def delayed_count(original=original):
            result = original(); barrier.wait(timeout=5); return result
        monkeypatch.setattr(store, 'count', delayed_count)
    def add(index):
        try:
            stores[index].add(f'android-{index}', keys[index], 'Android', ['read'], 1)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(add, [0, 1]))
    assert sorted(outcomes) == [False, True]
    with stores[0]._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM device_link_bindings WHERE revoked_at IS NULL').fetchone()[0] == 1


def test_sql_commit_failure_keeps_pairing_actionable_and_binding_empty(tmp_path, monkeypatch):
    db = StateDB(tmp_path / 'state.db'); db.connect()
    store = DeviceBindingStore(db); db.close()
    value = gateway(store); sid, token, public = pending(value)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    class FailedCommit(sqlite3.Connection):
        def __exit__(self, kind, error, trace):
            if kind is None:
                self.rollback()
                raise sqlite3.OperationalError('Injected disk failure; no sensitive data')
            return super().__exit__(kind, error, trace)
    monkeypatch.setattr(store, '_connect', lambda: sqlite3.connect(str(store._db_path), factory=FailedCommit))
    with pytest.raises(DeviceLinkError) as error:
        value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')
    assert error.value.status == 503
    assert value.pairing_mgr.get(sid).state is PairState.CONFIRMED_BOTH
    assert not value._completed_pairings
    assert not value._product_tokens and not value._sessions
    with sqlite3.connect(str(store._db_path)) as connection:
        assert connection.execute('SELECT COUNT(*) FROM device_link_bindings').fetchone()[0] == 0


def test_expiry_after_insert_rolls_back_before_commit(tmp_path, monkeypatch):
    db = StateDB(tmp_path / 'state.db'); db.connect(); store = DeviceBindingStore(db); db.close()
    value = gateway(store); sid, token, public = pending(value)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    session = value.pairing_mgr.get(sid); clock = FakeClock(0); session._clock = clock; session._expiry_abs = 10
    class ExpireAfterInsert(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.startswith('INSERT INTO device_link_bindings'):
                clock.advance(10)
            return result
    monkeypatch.setattr(store, '_connect', lambda: sqlite3.connect(str(store._db_path), factory=ExpireAfterInsert))
    with pytest.raises(DeviceLinkError):
        value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')
    assert session.state is PairState.EXPIRED
    assert not value._completed_pairings
    assert not value._product_tokens and not value._sessions
    with sqlite3.connect(str(store._db_path)) as connection:
        assert connection.execute('SELECT COUNT(*) FROM device_link_bindings').fetchone()[0] == 0


def test_expiry_after_successful_commit_does_not_strand_durable_binding(tmp_path, monkeypatch):
    db = StateDB(tmp_path / 'state.db'); db.connect(); store = DeviceBindingStore(db); db.close()
    value = gateway(store); sid, token, public = pending(value)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    session = value.pairing_mgr.get(sid); clock = FakeClock(0); session._clock = clock; session._expiry_abs = 10
    class ExpireAfterCommit(sqlite3.Connection):
        def __exit__(self, kind, error, trace):
            result = super().__exit__(kind, error, trace)
            if kind is None: clock.advance(10)
            return result
    monkeypatch.setattr(store, '_connect', lambda: sqlite3.connect(str(store._db_path), factory=ExpireAfterCommit))
    result = value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')
    assert result['status'] == 'bound' and session.state is PairState.CONSUMED
    assert value.validate_token(result['session_token']) == 'android-a'


def test_concurrent_completion_binds_exactly_once():
    value = gateway(); sid, token, public = pending(value)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    def complete(_):
        try: return value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')['status']
        except DeviceLinkError: return 'refused'
    with ThreadPoolExecutor(max_workers=2) as executor: outcomes = list(executor.map(complete, [0, 1]))
    assert sorted(outcomes) == ['bound', 'refused']
    assert value.devices.count() == 1 and len(value._product_tokens) == 1


@pytest.mark.parametrize('action', ['revoke', 'disable'])
def test_revocation_or_disable_cannot_race_after_token_invalidation(action, monkeypatch):
    value = gateway(); sid, token, public = pending(value)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    entered, release = Event(), Event(); original = value._issue_product_token
    def delayed(device):
        entered.set(); assert release.wait(3)
        return original(device)
    monkeypatch.setattr(value, '_issue_product_token', delayed)
    with ThreadPoolExecutor(max_workers=2) as executor:
        completion = executor.submit(value.pair_complete_scoped, sid, token, 'android-a', public.hex(), 'Android')
        assert entered.wait(3)
        stop = executor.submit(value.revoke_device, 'android-a') if action == 'revoke' else executor.submit(value.invalidate_transient_authorizations)
        release.set(); result = completion.result(timeout=3); stop.result(timeout=3)
    assert result['status'] == 'bound'
    assert value.validate_token(result['session_token']) is None
    assert not value._product_tokens
    assert value.devices.count() == (0 if action == 'revoke' else 1)


def bound(value, device="android-a"):
    sid, token, public = pending(value, device)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    value.pair_complete_scoped(sid, token, device, public.hex(), 'Android')
    return sid


def test_desktop_completion_projection_survives_session_pruning():
    value = gateway(); sid = bound(value)
    assert value.pairing_mgr.active_sessions() == 0
    assert value.pair_poll(sid) == {'session_id': sid, 'state': 'consumed'}


@pytest.mark.parametrize('prune', [False, True])
def test_consumed_projection_does_not_claim_success_after_revocation(prune):
    value = gateway(); sid = bound(value)
    if prune: value.pairing_mgr.active_sessions()
    value.revoke_device('android-a')
    assert value.pair_poll(sid).get('state') != 'consumed'


def test_completion_projection_checks_current_key_and_registry_health(monkeypatch):
    value = gateway(); sid = bound(value)
    original = value.devices.get
    monkeypatch.setattr(value.devices, 'get', lambda _uuid: {**original('android-a'), 'fingerprint': 'changed-key'})
    assert value.pair_poll(sid).get('state') != 'consumed'
    monkeypatch.setattr(value.devices, 'get', original)
    value = gateway(); sid = bound(value)
    calls = []
    def fail(uuid):
        calls.append(uuid)
        raise sqlite3.OperationalError('Unavailable registry')
    monkeypatch.setattr(value.devices, 'get', fail)
    assert value.pair_poll(sid)['code'] == 503
    assert calls == ['android-a']


def test_completion_projection_expires_and_disable_clears_it(monkeypatch):
    value = gateway(); sid = bound(value)
    clock = FakeClock(0)
    monkeypatch.setattr('agentguard.device_link.gateway._time.monotonic', clock.now)
    # Receipt recorded before this synthetic clock; expiry is explicit test input.
    value._completed_pairings[sid]['expires'] = 1
    clock.advance(1)
    assert value.pair_poll(sid).get('state') != 'consumed'
    value = gateway(); sid = bound(value)
    assert sid in value._completed_pairings
    value.invalidate_transient_authorizations()
    assert not value._completed_pairings
    assert value.pair_poll(sid).get('state') != 'consumed'


def test_completion_projection_cap_evicts_oldest_without_granting_replay():
    value = gateway(DeviceRegistry()); value.config.max_pair_sessions = 1
    first = bound(value, 'android-first'); second = bound(value, 'android-second')
    assert len(value._completed_pairings) == 1
    assert value.pair_poll(first).get('state') != 'consumed'
    assert value.pair_poll(second)['state'] == 'consumed'


def test_pair_token_expiry_after_database_lock_rolls_back(tmp_path, monkeypatch):
    clock = FakeClock(0)
    monkeypatch.setattr('agentguard.device_link.gateway._time.monotonic', clock.now)
    db = StateDB(tmp_path / 'state.db'); db.connect(); store = DeviceBindingStore(db); db.close()
    value = gateway(store); sid, token, public = pending(value)
    value.pair_android_confirm(sid, token, True); value.pair_desktop_confirm(sid, True)
    session = value.pairing_mgr.get(sid)
    session._clock = FakeClock(0)
    session._expiry_abs = value.config.pair_ttl + 1000
    class ExpireAfterLock(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql == 'BEGIN IMMEDIATE': clock.advance(value.config.pair_ttl)
            return result
    monkeypatch.setattr(store, '_connect', lambda: sqlite3.connect(str(store._db_path), factory=ExpireAfterLock))
    with pytest.raises(DeviceLinkError) as error:
        value.pair_complete_scoped(sid, token, 'android-a', public.hex(), 'Android')
    assert error.value.code == 'PAIR_TOKEN_INVALID'
    assert store.count() == 0 and not value._completed_pairings and not value._product_tokens
