import base64
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from agentguard.recovery import protection_domains, protection_helper
from agentguard.recovery.protection_domains import DomainError, DomainRegistry


def _entry_with_content(entry, content):
    digest = hashlib.sha256(content).hexdigest()
    return {**entry, 'size': len(content), 'content_digest': digest,
            'observation_digest': digest, 'content_b64': base64.b64encode(content).decode('ascii')}


_FAULT_PROGRAM = r'''
import json, os, sys
payload = json.load(sys.stdin)
scope = {'__name__': 'fault_helper'}
exec(compile(payload['source'], 'protection_helper.py', 'exec'), scope)
scope['_renameat2'] = lambda first, second, flags: False
request, fault = payload['request'], payload['fault']
original_atomic = scope['_atomic_json']
original_sync = scope['_sync_dir']
backup = request['root'] + '/.agentguard/protection/' + scope['_digest'](request['operation_id'].encode())[:32] + '/' + scope['_digest'](request['step_id'].encode())[:32] + '.old'
target = request['root'] + '/' + request['relative_path']
def atomic(path, value):
    if fault == 'before_receipt' and str(path).endswith('.receipt.json'):
        os._exit(83)
    original_atomic(path, value)
    if fault == 'after_intent' and str(path).endswith('.intent.json'):
        os._exit(83)
def sync(path):
    original_sync(path)
    if os.path.exists(backup) and not os.path.exists(target):
        if fault == 'after_backup':
            os._exit(83)
        if fault == 'interloper':
            with open(target, 'wb') as output:
                output.write(b'external gap version')
scope['_atomic_json'] = atomic
scope['_sync_dir'] = sync
original_rename = os.rename
def rename(first, second, *args, **kwargs):
    if fault == 'external_before_backup' and str(second).endswith('.old'):
        os.unlink(first)
        with open(first, 'wb') as output:
            output.write(b'external before version')
    return original_rename(first, second, *args, **kwargs)
if fault == 'external_before_backup':
    os.rename = rename
original_link = os.link
def link(first, second, *args, **kwargs):
    result = original_link(first, second, *args, **kwargs)
    if fault == 'after_publish_link' and str(second).endswith('/' + request['relative_path']):
        os._exit(83)
    return result
if fault == 'after_publish_link':
    os.link = link
try:
    print(json.dumps(scope['handle'](request)))
except OSError as error:
    print(json.dumps({'error': error.errno}))
'''


def _fault_apply(container_id, request, fault):
    payload = {'source': Path(protection_helper.__file__).read_text(encoding='utf-8'),
               'request': request, 'fault': fault}
    result = subprocess.run(['docker', 'exec', '-i', container_id, 'python3', '-I', '-B', '-c',
                             _FAULT_PROGRAM], input=json.dumps(payload), capture_output=True,
                            text=True, check=False)
    assert result.returncode in {0, 83}, result.stderr
    return result.returncode, json.loads(result.stdout) if result.stdout else None


def test_native_replace_retains_actual_old_and_reconciles(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'note.txt').write_bytes(b'old\r\nvalue')
    registry = DomainRegistry(tmp_path / 'state')
    assert registry.sources()[0]['source_id'] == 'local'
    binding = registry.register('local', str(root))
    initial_fingerprint = registry.fingerprint(binding)
    assert len(initial_fingerprint) == 64
    assert registry.available_bytes(binding) > 0
    expected = registry.scan(binding)['entries'][0]
    desired = _entry_with_content(expected, b'new\nvalue')
    receipt = registry.apply(binding, 'op1', 'step1', 'note.txt', expected, desired)
    assert receipt['state'] == 'VERIFIED'
    assert base64.b64decode(receipt['actual_before']['content_b64']) == b'old\r\nvalue'
    assert base64.b64decode(receipt['after']['content_b64']) == b'new\nvalue'
    assert (root / 'note.txt').read_bytes() == b'new\nvalue'
    assert registry.fingerprint(binding) != initial_fingerprint
    assert registry.apply(binding, 'op1', 'step1', 'note.txt', expected, desired)['state'] == 'VERIFIED'
    assert registry.reconcile(binding, 'op1', 'step1')['state'] == 'VERIFIED'
    assert registry.operation_storage(binding, 'op1') > len(b'old\r\nvalue')
    assert registry.cleanup_operation(binding, 'op1')['removed'] is True
    assert registry.operation_storage(binding, 'op1') == 0
    registry.close()


def test_docker_disconnect_and_mount_change_reject_old_binding(tmp_path, monkeypatch):
    registry = DomainRegistry(tmp_path / 'state')
    container_id = 'a' * 64
    endpoint = 'npipe:////./pipe/docker_engine'
    mount = {'Type': 'volume', 'Source': '/var/lib/docker/volumes/one/_data',
             'Destination': '/workspace', 'RW': True, 'Name': 'one', 'Driver': 'local'}
    inspected = {'Id': container_id, 'State': {'Running': True, 'StartedAt': 'first'},
                 'Mounts': [mount]}
    monkeypatch.setattr(registry, '_docker_context', lambda: (endpoint, {'DOCKER_HOST': endpoint}))
    monkeypatch.setattr(registry, '_inspect_docker', lambda identifier, env: inspected)
    monkeypatch.setattr(protection_domains.shutil, 'which', lambda command: 'C:/docker.exe')

    def run(argv, **kwargs):
        if argv[0] == 'wsl.exe':
            raise DomainError('DOMAIN_RUNTIME_UNAVAILABLE')
        if argv[1] == 'exec':
            return json.dumps({'ok': True, 'result': {'platform': 'posix', 'pid': 123}}).encode()
        return (json.dumps({'ID': container_id, 'Names': 'fixture'}) + '\n').encode()

    monkeypatch.setattr(protection_domains, '_run', run)
    source = next(item for item in registry.sources() if item['kind'] == 'CONTAINER')
    assert source['running'] is True
    assert source['connected'] is False
    assert registry.is_docker_disconnected(source['source_id']) is False
    source = registry.connect(source['source_id'])
    assert source['connected'] is True
    binding = {'source_id': source['source_id'], 'kind': 'CONTAINER',
               'root': '/workspace/project', 'epoch': source['epoch'],
               'identity': {'root': {'dev': 1, 'ino': 2}, 'container_id': container_id,
                            'endpoint': endpoint,
                            'storage': registry._storage(source, '/workspace/project')}}
    monkeypatch.setattr(registry, '_docker_context', lambda: None)
    with pytest.raises(DomainError, match='DOMAIN_IDENTITY_CHANGED'):
        registry.disconnect(source['source_id'])
    assert registry.is_docker_disconnected(source['source_id']) is True
    monkeypatch.setattr(registry, '_docker_context', lambda: (endpoint, {'DOCKER_HOST': endpoint}))
    assert next(item for item in registry.sources() if item['source_id'] == source['source_id'])['connected'] is False
    registry.disconnect(source['source_id'])
    assert registry.is_docker_disconnected(source['source_id']) is True
    assert next(item for item in registry.sources() if item['source_id'] == source['source_id'])['connected'] is False
    with pytest.raises(DomainError, match='DOMAIN_DISCONNECTED'):
        registry._bound(binding)
    registry.connect(source['source_id'])
    assert registry.is_docker_disconnected(source['source_id']) is False
    assert registry._bound(binding)[0]['connected'] is True
    inspected['Mounts'] = [{**mount, 'Source': '/var/lib/docker/volumes/two/_data', 'Name': 'two'}]
    with pytest.raises(DomainError, match='DOMAIN_MOUNT_IDENTITY_CHANGED'):
        registry._bound(binding)
    with pytest.raises(DomainError, match='DOMAIN_DISCONNECT_UNSUPPORTED'):
        registry.disconnect('local')


@pytest.mark.parametrize('failure', ['transport', 'malformed'])
def test_docker_connect_requires_working_helper(tmp_path, monkeypatch, failure):
    registry = DomainRegistry(tmp_path / 'state')
    container_id = 'b' * 64
    endpoint = 'npipe:////./pipe/docker_engine'
    inspected = {'Id': container_id, 'State': {'Running': True, 'StartedAt': 'first'}, 'Mounts': []}
    monkeypatch.setattr(registry, '_docker_context', lambda: (endpoint, {'DOCKER_HOST': endpoint}))
    monkeypatch.setattr(registry, '_inspect_docker', lambda identifier, env: inspected)
    monkeypatch.setattr(protection_domains.shutil, 'which', lambda command: 'C:/docker.exe')
    working = False

    def run(argv, **kwargs):
        if argv[0] == 'wsl.exe':
            raise DomainError('DOMAIN_RUNTIME_UNAVAILABLE')
        if argv[1] == 'exec':
            if working:
                return json.dumps({'ok': True, 'result': {'platform': 'posix', 'pid': 321}}).encode()
            if failure == 'transport':
                raise DomainError('DOMAIN_TRANSPORT_FAILED')
            return json.dumps({'ok': True, 'result': {}}).encode()
        return (json.dumps({'ID': container_id, 'Names': 'fixture'}) + '\n').encode()

    monkeypatch.setattr(protection_domains, '_run', run)
    source_id = next(item['source_id'] for item in registry.sources() if item['kind'] == 'CONTAINER')
    assert registry._source(source_id)['connected'] is False
    with pytest.raises(DomainError, match='DOMAIN_TRANSPORT_FAILED|DOMAIN_PROTOCOL_INVALID'):
        registry.connect(source_id)
    failed = next(item for item in registry.sources() if item['source_id'] == source_id)
    assert failed['running'] is True
    assert failed['connected'] is False
    assert failed['capabilities'] == {'scan': False, 'apply': False}
    assert registry.is_docker_disconnected(source_id) is False
    working = True
    connected = registry.connect(source_id)
    assert connected['connected'] is True
    assert connected['capabilities'] == {'scan': True, 'apply': True}
    inspected['State']['StartedAt'] = 'second'
    restarted = next(item for item in registry.sources() if item['source_id'] == source_id)
    assert restarted['epoch'] != connected['epoch']
    assert restarted['connected'] is False
    assert registry._source(source_id)['connected'] is False
    assert registry.connect(source_id)['connected'] is True


def test_wsl_without_disk_identity_cannot_gain_protection_authority(tmp_path, monkeypatch):
    registry = DomainRegistry(tmp_path / 'state')
    monkeypatch.setattr(registry, '_docker_context', lambda: None)
    monkeypatch.setattr(protection_domains, '_wsl_registry', lambda: {
        'Ubuntu': {'guid': 'distro-guid', 'disk_identity': None}})

    def run(argv, **kwargs):
        if argv[-1] == '--verbose':
            return b'  NAME      STATE      VERSION\n  Ubuntu    Running    2\n'
        return b'Ubuntu\n'

    monkeypatch.setattr(protection_domains, '_run', run)
    source = next(item for item in registry.sources() if item['kind'] == 'WSL')
    assert source['running'] is True
    assert source['connected'] is False
    with pytest.raises(DomainError, match='DOMAIN_IDENTITY_CHANGED'):
        registry.connect(source['source_id'], start=True)
    with pytest.raises(DomainError, match='DOMAIN_IDENTITY_CHANGED'):
        registry.register(source['source_id'], '/workspace/project')
    binding = {'source_id': source['source_id'], 'kind': 'WSL',
               'root': '/workspace/project', 'epoch': source['epoch'],
               'identity': {'root': {}, 'guid': 'distro-guid', 'disk_identity': None}}
    with pytest.raises(DomainError, match='DOMAIN_IDENTITY_CHANGED'):
        registry.scan(binding)
    assert registry._channels == {}


def test_root_identity_change_rejected(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    registry = DomainRegistry(tmp_path / 'state')
    binding = registry.register('local', str(root))
    root.rename(tmp_path / 'old_workspace')
    root.mkdir()
    with pytest.raises(DomainError) as exc:
        registry.scan(binding)
    assert exc.value.reason_code == 'WORKSPACE_ROOT_IDENTITY_CHANGED'


def test_system_root_and_symlink_rejected(tmp_path):
    registry = DomainRegistry(tmp_path / 'state')
    with pytest.raises(DomainError):
        registry.register('local', str(tmp_path.anchor))
    actual = tmp_path / 'actual'
    actual.mkdir()
    linked = tmp_path / 'linked'
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    with pytest.raises(DomainError):
        registry.register('local', str(linked))


@pytest.mark.skipif(os.environ.get('AGENTGUARD_LIVE_DOMAINS') != '1', reason='explicit live fixture only')
def test_live_docker_and_wsl_temp_fixtures(tmp_path):
    registry = DomainRegistry(tmp_path / 'state')
    initial = registry.sources()
    targets = [item for item in initial if item['kind'] == 'CONTAINER' and
               item['label'] == 'agent-k3' and item['running']]
    wsl = [item for item in initial if item['kind'] == 'WSL' and item['label'] == 'Ubuntu']
    assert len(targets) == len(wsl) == 1
    docker, ubuntu = targets[0], wsl[0]
    print('Ubuntu before:', ubuntu['running'], ubuntu['connected'])
    print('Docker target:', docker['container_id'])
    fixtures = []
    try:
        for domain in (docker, ubuntu):
            if domain['kind'] == 'WSL':
                registry.connect(domain['source_id'], start=True)
                channel = registry._channels[domain['source_id']]
                print('WSL host channel PID:', channel.process.pid,
                      'remote helper PID:', channel.request({'action': 'ping'})['pid'])
                for provider in ('CODEX', 'KIMI_CODE'):
                    store = registry.read_sessions(domain['source_id'], provider)
                    assert store['provider'] == provider
                    print('WSL session store:', provider, store['status'], store['reason_code'])
                prefix = ['wsl.exe', '--distribution', domain['label'], '--exec']
            else:
                registry.connect(domain['source_id'])
                prefix = ['docker', 'exec', domain['container_id']]
            path = '/tmp/asg-protection-' + uuid.uuid4().hex
            fixtures.append((prefix, path))
            print(domain['kind'], 'fixture:', path)
            subprocess.run([*prefix, 'mkdir', path], check=True)
            subprocess.run([*prefix, 'touch', path + '/sample.txt'], check=True)
            binding = registry.register(domain['source_id'], path)
            scan = registry.scan(binding)
            assert scan['complete']
            existing = next(entry for entry in scan['entries'] if entry['relative_path'] == 'sample.txt')
            desired = _entry_with_content(existing, b'fixture verified\n')
            receipt = registry.apply(binding, 'live-' + uuid.uuid4().hex, 'step1', 'sample.txt', existing, desired)
            assert receipt['state'] == 'VERIFIED'
            assert base64.b64decode(receipt['after']['content_b64']) == b'fixture verified\n'
            rescanned = registry.scan(binding)['entries']
            assert next(item for item in rescanned if item['relative_path'] == 'sample.txt')['content_digest'] == desired['content_digest']
        state = next(item for item in registry.sources() if item['source_id'] == ubuntu['source_id'])
        print('Ubuntu after:', state['running'], state['connected'])
    finally:
        registry.close()
        for prefix, path in fixtures:
            subprocess.run([*prefix, 'rm', '-f', path + '/sample.txt'], check=True)
            subprocess.run([*prefix, 'rm', '-rf', path + '/.agentguard'], check=True)
            subprocess.run([*prefix, 'rmdir', path], check=True)


@pytest.mark.skipif(os.environ.get('AGENTGUARD_LIVE_DOMAINS') != '1', reason='explicit live fixture only')
def test_live_docker_storage_kinds(tmp_path):
    image = subprocess.run(['docker', 'inspect', '--format', '{{.Config.Image}}', 'agent-k3'],
                           check=True, capture_output=True, text=True).stdout.strip()
    token = uuid.uuid4().hex
    volume = 'asg-protection-' + token
    name = 'asg-protection-' + token
    bind_root = tmp_path / 'asg-protection-bind'
    bind_root.mkdir()
    container_id = None
    subprocess.run(['docker', 'volume', 'create', volume], check=True, capture_output=True)
    try:
        run = subprocess.run(['docker', 'run', '-d', '--name', name,
            '--mount', f'type=bind,source={bind_root},target=/mnt/asg-bind',
            '--mount', f'type=volume,source={volume},target=/mnt/asg-volume',
            '--user', '0:0', '--entrypoint', 'python3', image, '-c', 'import time; time.sleep(180)'],
            check=True, capture_output=True, text=True)
        container_id = run.stdout.strip()
        assert len(container_id) == 64
        print('Dedicated Docker fixture container:', container_id, 'volume:', volume)
        registry = DomainRegistry(tmp_path / 'state')
        try:
            source = next(item for item in registry.sources() if item.get('container_id') == container_id)
            source = registry.connect(source['source_id'])
            for root, storage_kind in (('/mnt/asg-bind/project', 'bind'),
                                       ('/mnt/asg-volume/project', 'volume'),
                                       ('/tmp/asg-project', 'writable_layer')):
                subprocess.run(['docker', 'exec', container_id, 'mkdir', root], check=True)
                subprocess.run(['docker', 'exec', container_id, 'touch', root + '/sample.txt'], check=True)
                binding = registry.register(source['source_id'], root)
                assert binding['identity']['storage']['kind'] == storage_kind
                before = next(item for item in registry.scan(binding)['entries']
                              if item['relative_path'] == 'sample.txt')
                after = _entry_with_content(before, storage_kind.encode())
                operation = 'storage-' + storage_kind
                receipt = registry.apply(binding, operation, 'step1', 'sample.txt', before, after)
                assert receipt['state'] == 'VERIFIED'
                assert registry.reconcile(binding, operation, 'step1')['state'] == 'VERIFIED'
                print('Verified storage:', storage_kind, root)
            subprocess.run(['docker', 'exec', container_id, 'python3', '-I', '-B', '-c',
                            "import os; p='/tmp/asg-project/foreign.txt'; open(p,'wb').write(b'foreign'); os.chown(p,12345,12345)"],
                           check=True)
            foreign_binding = registry.register(source['source_id'], '/tmp/asg-project')
            foreign = next(item for item in registry.scan(foreign_binding)['entries']
                           if item['relative_path'] == 'foreign.txt')
            assert foreign['category'] == 'audit_only'
            assert foreign['reason_code'] == 'WORKSPACE_METADATA_AUDIT_ONLY'
            binding = registry.register(source['source_id'], '/mnt/asg-bind/project')
            for fault in ('after_intent', 'after_backup', 'after_publish_link', 'before_receipt',
                          'interloper', 'external_before_backup'):
                relative = fault + '.txt'
                host_file = bind_root / 'project' / relative
                host_file.write_bytes(b'old version')
                before = next(item for item in registry.scan(binding)['entries']
                              if item['relative_path'] == relative)
                after = _entry_with_content(before, b'new version')
                operation = 'fault-' + fault
                request = {'action': 'apply', 'root': binding['root'],
                           'identity': binding['identity']['root'], 'operation_id': operation,
                           'step_id': 'step1', 'relative_path': relative,
                           'expected': before, 'target': after}
                code, result = _fault_apply(container_id, request, fault)
                reconciled = registry.reconcile(binding, operation, 'step1')
                if fault in {'after_intent', 'after_backup'}:
                    assert code == 83
                    assert reconciled['state'] == 'NOT_APPLIED'
                    assert host_file.read_bytes() == b'old version'
                elif fault in {'after_publish_link', 'before_receipt'}:
                    assert code == 83
                    assert reconciled['state'] == 'APPLIED'
                    assert host_file.read_bytes() == b'new version'
                elif fault == 'interloper':
                    assert code == 0 and result['error'] == 17
                    assert reconciled['state'] == 'NEEDS_ATTENTION'
                    assert host_file.read_bytes() == b'external gap version'
                    assert base64.b64decode(reconciled['actual_before']['content_b64']) == b'old version'
                else:
                    assert code == 0 and result['state'] == 'CONFLICT'
                    assert base64.b64decode(result['actual_before']['content_b64']) == b'external before version'
                    assert reconciled['state'] == 'CONFLICT'
                    inverse = registry.apply(binding, operation, 'compensate-step1', relative,
                                             after, result['actual_before'])
                    assert inverse['state'] == 'VERIFIED'
                    assert host_file.read_bytes() == b'external before version'
                print('Verified fallback fault:', fault, reconciled['state'])
        finally:
            registry.close()
    finally:
        if container_id:
            subprocess.run(['docker', 'rm', '-f', container_id], check=True, capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', volume], check=True, capture_output=True)
        assert bind_root.resolve().is_relative_to(tmp_path.resolve())
        shutil.rmtree(bind_root)
