"""Pinned native, Docker and explicit WSL workspace protection domains."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

from agentguard.discovery.domains.docker_host import _is_local_endpoint
from agentguard.discovery.domains.wsl import parse_wsl_list
from agentguard.discovery.probes.container_processes import (
    ContainerProcessProtocolError,
    parse_container_processes,
)
from agentguard.discovery.source_identity import (
    docker_source_id,
    legacy_docker_source_id,
)
from agentguard.recovery.workspace_permissions import current_user_permission_backend
from agentguard.recovery.workspace_policy import scan_workspace

from . import protection_helper

_ID = re.compile(r'[0-9a-f]{64}\Z')
_MAX_RESPONSE = 200 * 1024 * 1024
_TIMEOUT = 30
_BOOTSTRAP = '''import hashlib,json,sys
line=sys.stdin.buffer.readline(262145)
if not line.endswith(b'\\n') or len(line)>262144: sys.exit(2)
source=json.loads(line)
if not isinstance(source,str) or hashlib.sha256(source.encode()).hexdigest()!=sys.argv[1]: sys.exit(2)
scope={'__name__':'agentguard_helper'}
exec(compile(source,'agentguard/recovery/protection_helper.py','exec'),scope)
scope['main']()
'''


class DomainError(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


def _hash(*parts: object) -> str:
    return hashlib.sha256('\x1f'.join(map(str, parts)).encode()).hexdigest()


def _helper_source() -> str:
    """Ship the same audited readers used natively, never client supplied code."""
    from agentguard.discovery import application_probe, session_metadata
    from agentguard.discovery.probes import container_processes
    prefix = 'import types,sys\n'
    for name, module in (('_asg_applications', application_probe), ('_asg_sessions', session_metadata),
                         ('_asg_processes', container_processes)):
        source = Path(module.__file__).with_suffix('.py').read_text(encoding='utf-8')
        prefix += f"{name}=types.ModuleType({name!r})\nsys.modules[{name!r}]={name}\n"
        prefix += f"exec(compile({source!r},{name!r},'exec'),{name}.__dict__)\n"
    helper = Path(protection_helper.__file__).with_suffix('.py').read_text(encoding='utf-8')
    return 'from __future__ import annotations\n' + prefix + helper.replace('from __future__ import annotations', '')


def _run(argv: list[str], *, input_bytes: bytes | None = None, timeout: int = _TIMEOUT,
         env: dict[str, str] | None = None) -> bytes:
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
                                   env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except FileNotFoundError as exc:
        raise DomainError('DOMAIN_RUNTIME_UNAVAILABLE') from exc
    except OSError as exc:
        raise DomainError('DOMAIN_TRANSPORT_FAILED') from exc
    output: list[bytes] = []
    errors: list[BaseException] = []

    def read() -> None:
        try:
            assert process.stdout is not None
            output.append(process.stdout.read(_MAX_RESPONSE + 1))
        except OSError as exc:
            errors.append(exc)

    def write() -> None:
        if input_bytes is None:
            return
        try:
            assert process.stdin is not None
            process.stdin.write(input_bytes)
            process.stdin.close()
        except OSError as exc:
            errors.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    writer = threading.Thread(target=write, daemon=True)
    reader.start()
    writer.start()
    deadline = time.monotonic() + timeout
    reader.join(max(0, deadline - time.monotonic()))
    writer.join(max(0, deadline - time.monotonic()))
    if reader.is_alive() or writer.is_alive() or (output and len(output[0]) > _MAX_RESPONSE):
        process.kill()
        process.wait(timeout=2)
        raise DomainError('DOMAIN_RESPONSE_LIMIT' if output and len(output[0]) > _MAX_RESPONSE else 'DOMAIN_TRANSPORT_TIMEOUT')
    try:
        status = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=2)
        raise DomainError('DOMAIN_TRANSPORT_TIMEOUT') from exc
    if errors or status != 0:
        raise DomainError('DOMAIN_TRANSPORT_FAILED')
    return output[0] if output else b''


def _decode_json(raw: bytes) -> dict:
    if len(raw) > _MAX_RESPONSE:
        raise DomainError('DOMAIN_RESPONSE_LIMIT')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DomainError('DOMAIN_PROTOCOL_INVALID') from exc
    if not isinstance(value, dict):
        raise DomainError('DOMAIN_PROTOCOL_INVALID')
    return value


def _unwrap(response: dict) -> dict:
    if response.get('ok') is not True:
        raise DomainError(str(response.get('reason_code', 'DOMAIN_HELPER_FAILED')))
    result = response.get('result')
    if not isinstance(result, dict):
        raise DomainError('DOMAIN_PROTOCOL_INVALID')
    return result


def _wsl_text(raw: bytes) -> str:
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')) or b'\x00' in raw[:100]:
        return raw.decode('utf-16', errors='replace') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-16-le', errors='replace')
    return raw.decode('utf-8', errors='replace')


def _wsl_registry() -> dict[str, dict]:
    if os.name != 'nt':
        return {}
    import winreg
    values: dict[str, dict] = {}
    try:
        parent = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Lxss')
    except OSError:
        return {}
    with parent:
        count = winreg.QueryInfoKey(parent)[0]
        for index in range(count):
            try:
                guid = winreg.EnumKey(parent, index)
                with winreg.OpenKey(parent, guid) as key:
                    name = winreg.QueryValueEx(key, 'DistributionName')[0]
                    base = winreg.QueryValueEx(key, 'BasePath')[0]
                if name.casefold() == 'docker-desktop' or name.casefold().startswith('docker-desktop-'):
                    continue
                base_path = Path(os.path.expandvars(base))
                disk = next((base_path / n for n in ('ext4.vhdx', 'rootfs') if (base_path / n).exists()), base_path)
                try:
                    info = disk.stat()
                    disk_identity = {'dev': info.st_dev, 'ino': info.st_ino}
                except OSError:
                    disk_identity = None
                values[name] = {'guid': guid.lower(), 'disk_identity': disk_identity}
            except (OSError, ValueError):
                continue
    return values


class _WslChannel:
    def __init__(self, distro: str, source: str):
        try:
            self.process = subprocess.Popen(['wsl.exe', '--distribution', distro, '--exec', 'python3', '-I', '-B', '-u', '-c',
                                             _BOOTSTRAP, hashlib.sha256(source.encode()).hexdigest()],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(source, ensure_ascii=True).encode() + b'\n')
            self.process.stdin.flush()
        except OSError as exc:
            if hasattr(self, 'process'):
                self.process.kill()
                self.process.wait(timeout=2)
            raise DomainError('DOMAIN_TRANSPORT_FAILED') from exc
        self.responses: queue.Queue[bytes | None] = queue.Queue()
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline(_MAX_RESPONSE + 1)
            self.responses.put(line or None)
            if not line or len(line) > _MAX_RESPONSE:
                return

    def request(self, payload: dict) -> dict:
        with self.lock:
            if self.process.poll() is not None:
                raise DomainError('DOMAIN_DISCONNECTED')
            try:
                assert self.process.stdin is not None
                self.process.stdin.write(json.dumps(payload, ensure_ascii=True).encode() + b'\n')
                self.process.stdin.flush()
                line = self.responses.get(timeout=_TIMEOUT)
            except (OSError, queue.Empty, ValueError) as exc:
                raise DomainError('DOMAIN_TRANSPORT_FAILED') from exc
            if not line:
                raise DomainError('DOMAIN_DISCONNECTED')
            return _unwrap(_decode_json(line))

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        except OSError:
            self.process.terminate()


class DomainRegistry:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._connection_epochs: dict[str, str] = {}
        self._sources: dict[str, dict] = {}
        self._channels: dict[str, _WslChannel] = {}
        self._disconnected_containers: set[str] = set()
        self._docker_endpoint: str | None = None
        self._docker_env: dict[str, str] | None = None
        self._lock = threading.RLock()

    def _docker_context(self) -> tuple[str, dict[str, str]] | None:
        docker = shutil.which('docker')
        if docker is None:
            return None
        try:
            name = _run([docker, 'context', 'show'], timeout=4).decode('ascii').strip()
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name):
                return None
            endpoint = json.loads(_run([docker, 'context', 'inspect', name, '--format', '{{json .Endpoints.docker.Host}}'], timeout=4))
        except (DomainError, UnicodeError, ValueError):
            return None
        if not isinstance(endpoint, str) or not _is_local_endpoint(endpoint):
            return None
        requested = os.environ.get('DOCKER_HOST')
        if requested and (not _is_local_endpoint(requested) or requested != endpoint):
            return None
        env = dict(os.environ)
        env.pop('DOCKER_CONTEXT', None)
        env['DOCKER_HOST'] = endpoint
        return endpoint, env

    def sources(self, refresh: bool = True) -> list[dict]:
        if not refresh and self._sources:
            return [dict(v) for v in tuple(self._sources.values())]
        with self._lock:
            return self._refresh_sources()

    def _refresh_sources(self) -> list[dict]:
        sources: dict[str, dict] = {'local': {'source_id': 'local', 'kind': 'NATIVE',
             'label': 'Local', 'running': True, 'connected': True,
             'epoch': _hash('native', os.name), 'capabilities': {'scan': True, 'apply': True}}}
        context = self._docker_context()
        self._docker_endpoint, self._docker_env = context if context else (None, None)
        if context:
            docker = shutil.which('docker')
            try:
                lines = _run([docker, 'ps', '-a', '--no-trunc', '--format', '{{json .}}'], env=context[1], timeout=8).decode().splitlines()
                for line in lines[:256]:
                    item = json.loads(line)
                    full_id = item.get('ID')
                    if not isinstance(full_id, str) or not _ID.fullmatch(full_id):
                        continue
                    inspected = self._inspect_docker(full_id, context[1])
                    if inspected is None:
                        continue
                    source_id = docker_source_id(full_id)
                    running = inspected.get('State', {}).get('Running') is True
                    epoch = _hash(context[0], full_id, inspected.get('State', {}).get('StartedAt'))
                    connected = (running and source_id not in self._disconnected_containers and
                                 self._connection_epochs.get(source_id) == epoch)
                    sources[source_id] = {'source_id': source_id, 'kind': 'CONTAINER',
                        'label': str(item.get('Names', full_id[:12]))[:128], 'running': running,
                        'connected': connected, 'epoch': epoch,
                        'capabilities': {'scan': connected, 'apply': connected},
                        'container_id': full_id, 'endpoint': context[0], 'mounts': inspected.get('Mounts', [])}
            except (DomainError, UnicodeError, ValueError):
                pass
        registrations = _wsl_registry()
        try:
            listed = parse_wsl_list(_wsl_text(_run(['wsl.exe', '--list', '--verbose'], timeout=5)))
            running_raw = _wsl_text(_run(['wsl.exe', '--list', '--running', '--quiet'], timeout=5))
            running_names = {line.strip().lstrip('\ufeff') for line in running_raw.replace('\x00', '').splitlines() if line.strip()}
        except DomainError:
            listed = None
            running_names = set()
        if listed:
            for distro in listed.distros:
                registration = registrations.get(distro.name)
                if registration is None:
                    continue
                source_id = 'wsl-' + _hash(registration['guid'])[:24]
                channel = self._channels.get(source_id)
                connected = (registration['disk_identity'] is not None and channel is not None and
                             channel.process.poll() is None)
                sources[source_id] = {'source_id': source_id, 'kind': 'WSL', 'label': distro.name,
                    'running': distro.name in running_names, 'connected': connected,
                    'epoch': _hash(registration['guid'], registration['disk_identity'],
                                   self._connection_epochs.get(source_id)),
                    'capabilities': {'scan': connected, 'apply': connected},
                    'guid': registration['guid'], 'disk_identity': registration['disk_identity']}
        self._sources = sources
        return [dict(v) for v in sources.values()]

    @staticmethod
    def _inspect_docker(full_id: str, env: dict[str, str]) -> dict | None:
        docker = shutil.which('docker')
        try:
            raw = json.loads(_run([docker, 'inspect', '--type', 'container', full_id], env=env, timeout=8))
            value = raw[0] if isinstance(raw, list) and len(raw) == 1 else None
            return value if isinstance(value, dict) and value.get('Id') == full_id else None
        except (DomainError, ValueError, TypeError):
            return None

    def _source(self, source_id: str) -> dict:
        source_id = self.resolve_source_id(source_id)
        if source_id == 'local':
            return {'source_id': 'local', 'kind': 'NATIVE', 'label': 'Local', 'running': True,
                    'connected': True, 'epoch': _hash('native', os.name),
                    'capabilities': {'scan': True, 'apply': True}}
        if source_id not in self._sources:
            self.sources(refresh=True)
            source_id = self.resolve_source_id(source_id)
        source = self._sources.get(source_id)
        if source is None:
            raise DomainError('DOMAIN_SOURCE_NOT_FOUND')
        if source['kind'] == 'CONTAINER':
            context = self._docker_context()
            if context is None or context[0] != source['endpoint']:
                raise DomainError('DOMAIN_IDENTITY_CHANGED')
            self._docker_endpoint, self._docker_env = context
            inspected = self._inspect_docker(source['container_id'], context[1])
            if inspected is None:
                raise DomainError('DOMAIN_IDENTITY_CHANGED')
            running = inspected.get('State', {}).get('Running') is True
            epoch = _hash(context[0], source['container_id'], inspected.get('State', {}).get('StartedAt'))
            connected = (running and source_id not in self._disconnected_containers and
                         self._connection_epochs.get(source_id) == epoch)
            source = {**source, 'running': running, 'connected': connected,
                      'epoch': epoch,
                      'mounts': inspected.get('Mounts', []),
                      'capabilities': {'scan': connected, 'apply': connected}}
        elif source['kind'] == 'WSL':
            registration = _wsl_registry().get(source['label'])
            if registration is None or registration['guid'] != source['guid']:
                raise DomainError('DOMAIN_IDENTITY_CHANGED')
            try:
                running_raw = _wsl_text(_run(['wsl.exe', '--list', '--running', '--quiet'], timeout=5))
                running = source['label'] in {line.strip().lstrip('\ufeff') for line in running_raw.replace('\x00', '').splitlines()}
            except DomainError:
                running = False
            channel = self._channels.get(source_id)
            connected = (registration['disk_identity'] is not None and channel is not None and
                         channel.process.poll() is None)
            source = {**source, 'running': running, 'connected': connected,
                      'disk_identity': registration['disk_identity'],
                      'epoch': _hash(source['guid'], registration['disk_identity'],
                                     self._connection_epochs.get(source_id)),
                      'capabilities': {'scan': connected, 'apply': connected}}
        self._sources[source_id] = source
        return source

    def resolve_source_id(self, source_id: str) -> str:
        resolver = getattr(self, 'source_resolver', None)
        if resolver is not None:
            source_id = resolver(source_id)
        for source in self._sources.values():
            if source.get('container_id') and legacy_docker_source_id(source['container_id']) == source_id:
                return source['source_id']
        return source_id

    @_locked
    def connect(self, source_id: str, start: bool = False) -> dict:
        source = self._source(source_id)
        source_id = source['source_id']
        if source['kind'] == 'WSL':
            if source['disk_identity'] is None:
                raise DomainError('DOMAIN_IDENTITY_CHANGED')
            if not source['running'] and not start:
                raise DomainError('WSL_START_NOT_AUTHORIZED')
            if source_id not in self._channels or self._channels[source_id].process.poll() is not None:
                try:
                    channel = _WslChannel(source['label'], _helper_source())
                    self._channels[source_id] = channel
                    self._verify_ping(channel.request({'action': 'ping'}))
                except DomainError:
                    failed = self._channels.pop(source_id, None)
                    if failed is not None:
                        failed.close()
                    raise
                self._connection_epochs[source_id] = uuid.uuid4().hex
        elif source['kind'] == 'CONTAINER':
            if not source['running']:
                raise DomainError('DOMAIN_NOT_RUNNING')
            try:
                self._verify_ping(self._call(source, {'action': 'ping'}, connecting=True))
                current = self._source(source_id)
                if not current['running'] or current['epoch'] != source['epoch']:
                    raise DomainError('DOMAIN_IDENTITY_CHANGED')
            except DomainError:
                self._connection_epochs.pop(source_id, None)
                raise
            self._connection_epochs[source_id] = source['epoch']
            self._disconnected_containers.discard(source_id)
        return self._source(source_id)

    @staticmethod
    def _verify_ping(result: dict) -> None:
        if (result.get('platform') != 'posix' or type(result.get('pid')) is not int or
                result['pid'] <= 0):
            raise DomainError('DOMAIN_PROTOCOL_INVALID')

    @_locked
    def disconnect(self, source_id: str) -> dict:
        source_id = self.resolve_source_id(source_id)
        if re.fullmatch(r'docker-[0-9a-f]{24}', source_id):
            self._disconnected_containers.add(source_id)
            self._connection_epochs.pop(source_id, None)
        source = self._source(source_id)
        if source['kind'] == 'NATIVE':
            raise DomainError('DOMAIN_DISCONNECT_UNSUPPORTED')
        channel = self._channels.pop(source_id, None)
        if channel is not None:
            channel.close()
        return self._source(source_id)

    def is_docker_disconnected(self, source_id: str) -> bool:
        return self.resolve_source_id(source_id) in self._disconnected_containers

    def discovery_query(self, source_id: str, payload: dict) -> dict:
        allowed = {'application_inventory', 'application_entry', 'session_stores',
                   'inspect_session_store', 'read_session_store'}
        if payload.get('action') not in allowed:
            raise DomainError('DOMAIN_ACTION_UNSUPPORTED')
        # Local read-only observation has no channel to serialize and must not
        # wait behind Docker/WSL transports or recovery. Write paths keep locks.
        if self.resolve_source_id(source_id) == 'local':
            result = self._call(self._source('local'), payload, observing=True)
        else:
            with self._lock:
                result = self._discovery_query_remote(source_id, payload)
        if len(json.dumps(result, ensure_ascii=True).encode()) > 256 * 1024:
            raise DomainError('DISCOVERY_OUTPUT_LIMITED')
        return result

    def _discovery_query_remote(self, source_id: str, payload: dict) -> dict:
        source = self._source(source_id)
        if source['kind'] == 'CONTAINER':
            if not source['running'] or self.is_docker_disconnected(source_id):
                raise DomainError('DOMAIN_DISCONNECTED')
        elif source['kind'] == 'WSL' and not source['connected']:
            raise DomainError('DOMAIN_DISCONNECTED')
        return self._call(source, payload, observing=True)

    @_locked
    def probe_processes(self, source_id: str):
        source = self._source(source_id)
        if source['kind'] != 'WSL' or not source['connected']:
            raise DomainError('DOMAIN_DISCONNECTED')
        response = self._call(source, {'action': 'processes'})
        try:
            return parse_container_processes(json.dumps(response, ensure_ascii=True).encode())
        except ContainerProcessProtocolError as exc:
            raise DomainError('DOMAIN_PROCESS_PROTOCOL_INVALID') from exc

    @_locked
    def read_sessions(self, source_id: str, provider: str) -> dict:
        source = self._source(source_id)
        if source['kind'] != 'WSL' or not source['connected']:
            raise DomainError('DOMAIN_DISCONNECTED')
        if provider not in {'CODEX', 'KIMI_CODE'}:
            raise DomainError('SESSION_PROVIDER_UNSUPPORTED')
        result = self._call(source, {'action': 'sessions', 'provider': provider})
        if (result.get('schema_version') != 'discovery-sessions-1' or
                result.get('provider') != provider or
                result.get('status') not in {'AVAILABLE', 'EMPTY', 'UNKNOWN', 'UNSUPPORTED'} or
                not isinstance(result.get('sessions'), list) or len(result['sessions']) > 50):
            raise DomainError('DOMAIN_PROTOCOL_INVALID')
        return result

    @_locked
    def close(self) -> None:
        for channel in self._channels.values():
            channel.close()
        self._channels.clear()

    def _call(self, source: dict, payload: dict, *, connecting: bool = False, observing: bool = False) -> dict:
        if source['kind'] == 'NATIVE':
            try:
                return protection_helper.handle(payload)
            except protection_helper.HelperError as exc:
                raise DomainError(exc.reason_code) from exc
        if source['kind'] == 'WSL':
            channel = self._channels.get(source['source_id'])
            if channel is None or channel.process.poll() is not None:
                raise DomainError('DOMAIN_DISCONNECTED')
            return channel.request(payload)
        if (not (source['connected'] or connecting and payload.get('action') == 'ping' and source['running'] or
                 observing and source['running'] and source['source_id'] not in self._disconnected_containers) or
                source['endpoint'] != self._docker_endpoint or self._docker_env is None):
            raise DomainError('DOMAIN_IDENTITY_CHANGED')
        docker = shutil.which('docker')
        helper_source = _helper_source()
        raw = _run([docker, 'exec', '-i', source['container_id'], 'python3', '-I', '-B', '-u', '-c',
                    _BOOTSTRAP, hashlib.sha256(helper_source.encode()).hexdigest()],
                   input_bytes=json.dumps(helper_source, ensure_ascii=True).encode() + b'\n' +
                               json.dumps(payload, ensure_ascii=True).encode() + b'\n',
                   env=self._docker_env)
        line = raw.splitlines()
        if len(line) != 1:
            raise DomainError('DOMAIN_PROTOCOL_INVALID')
        return _unwrap(_decode_json(line[0]))

    @staticmethod
    def _storage(source: dict, path: str) -> dict:
        mounts = [mount for mount in source['mounts'] if isinstance(mount, dict) and
                  isinstance(mount.get('Destination'), str) and
                  (path == mount['Destination'] or path.startswith(mount['Destination'].rstrip('/') + '/'))]
        mount = max(mounts, key=lambda item: len(item['Destination'])) if mounts else None
        if mount is None:
            return {'kind': 'writable_layer'}
        if mount.get('RW') is not True:
            raise DomainError('WORKSPACE_MOUNT_READ_ONLY')
        return {'kind': mount.get('Type'), 'source': mount.get('Source'),
                'destination': mount.get('Destination'), 'rw': mount.get('RW'),
                'name': mount.get('Name'), 'driver': mount.get('Driver')}

    @_locked
    def register(self, source_id: str, path: str) -> dict:
        source = self._source(source_id)
        source_id = source['source_id']
        if source['kind'] == 'WSL' and source['disk_identity'] is None:
            raise DomainError('DOMAIN_IDENTITY_CHANGED')
        if source['kind'] == 'WSL' and not source['connected']:
            raise DomainError('DOMAIN_DISCONNECTED')
        if source['kind'] == 'CONTAINER':
            if not source['connected']:
                raise DomainError('DOMAIN_DISCONNECTED')
            if not isinstance(path, str) or not path.startswith('/'):
                raise DomainError('WORKSPACE_ROOT_INVALID')
            storage = self._storage(source, path)
        else:
            storage = None
        result = self._call(source, {'action': 'register', 'root': path})
        root = result['root']
        if source['kind'] == 'NATIVE':
            resolved = Path(root).resolve(strict=True)
            if resolved in {Path(resolved.anchor), Path.home()} or (os.name == 'nt' and
                any(str(resolved).casefold().startswith(str(Path(os.environ.get(key, resolved.anchor))).casefold() + os.sep)
                    for key in ('WINDIR', 'ProgramFiles', 'ProgramFiles(x86)'))):
                raise DomainError('WORKSPACE_ROOT_UNSUPPORTED')
        identity = {'root': result['identity']}
        if source['kind'] == 'CONTAINER':
            identity['container_id'] = source['container_id']
            identity['endpoint'] = source['endpoint']
            identity['storage'] = storage
        elif source['kind'] == 'WSL':
            identity['guid'] = source['guid']
            identity['disk_identity'] = source['disk_identity']
        binding = {'source_id': source_id, 'kind': source['kind'], 'root': root,
                   'identity': identity, 'epoch': source['epoch']}
        return binding

    def _bound(self, binding: dict, *, require_epoch: bool = True) -> tuple[dict, dict]:
        if not isinstance(binding, dict) or not isinstance(binding.get('source_id'), str):
            raise DomainError('DOMAIN_BINDING_INVALID')
        source = self._source(binding['source_id'])
        if source['kind'] != binding.get('kind') or not isinstance(binding.get('identity'), dict):
            raise DomainError('DOMAIN_BINDING_INVALID')
        if source['kind'] == 'WSL' and source['disk_identity'] is None:
            raise DomainError('DOMAIN_IDENTITY_CHANGED')
        if require_epoch and source['epoch'] != binding.get('epoch'):
            raise DomainError('DOMAIN_EPOCH_CHANGED')
        if source['kind'] == 'CONTAINER':
            if binding['identity']['endpoint'] != source['endpoint'] or binding['identity']['container_id'] != source['container_id']:
                raise DomainError('DOMAIN_IDENTITY_CHANGED')
            if binding['identity'].get('storage') != self._storage(source, binding['root']):
                raise DomainError('DOMAIN_MOUNT_IDENTITY_CHANGED')
            if not source['connected']:
                raise DomainError('DOMAIN_DISCONNECTED')
        if source['kind'] == 'WSL' and (binding['identity']['guid'] != source['guid'] or
                                       binding['identity']['disk_identity'] != source['disk_identity']):
            raise DomainError('DOMAIN_IDENTITY_CHANGED')
        return source, {'root': binding['root'], 'identity': binding['identity']['root']}

    @_locked
    def execution_launch(self, identity: dict, provider: str) -> dict:
        """Fixed provider transports reuse the verified source and directory binding."""
        if provider not in {'CODEX', 'KIMI_CODE'}:
            raise DomainError('EXECUTION_PROVIDER_UNSUPPORTED')
        source, payload = self._bound(identity['binding'])
        directory = self._call(source, {'action': 'register', 'root': payload['root']})
        if directory.get('identity') != payload['identity']:
            raise DomainError('WORKSPACE_ROOT_IDENTITY_CHANGED')
        entry = self._call(source, {'action': 'application_entry', 'path': identity['entry_path']})
        if entry.get('status') != 'AVAILABLE' or entry.get('identity') != identity['application']:
            raise DomainError('EXECUTION_PROGRAM_IDENTITY_CHANGED')
        store = self._call(source, {'action': 'inspect_session_store', 'provider': provider, 'path': identity['store_path']})
        if store.get('identity') != identity['store']:
            raise DomainError('EXECUTION_STORE_IDENTITY_CHANGED')
        entry_path = entry['entry_path']
        arguments = ['app-server', '--listen', 'stdio://'] if provider == 'CODEX' else ['acp']
        key = 'CODEX_HOME' if provider == 'CODEX' else 'KIMI_SHARE_DIR'
        if source['kind'] == 'NATIVE':
            # Batch/PowerShell wrappers would require a second command parser.
            if os.name == 'nt' and Path(entry_path).suffix.lower() != '.exe':
                raise DomainError('EXECUTION_ENTRY_WRAPPER_UNSUPPORTED')
            return {'argv': [entry_path, *arguments], 'cwd': payload['root'],
                    'env': {**os.environ, key: identity['store_path']}}
        if not source['connected']:
            raise DomainError('DOMAIN_DISCONNECTED')
        if source['kind'] == 'CONTAINER':
            docker = shutil.which('docker')
            if not docker or self._docker_env is None:
                raise DomainError('DOMAIN_RUNTIME_UNAVAILABLE')
            return {'argv': [docker, 'exec', '-i', '-w', payload['root'], '-e',
                             key + '=' + identity['store_path'], source['container_id'], entry_path, *arguments],
                    'env': dict(self._docker_env)}
        return {'argv': ['wsl.exe', '--distribution', source['label'], '--cd', payload['root'],
                         '--exec', 'env', key + '=' + identity['store_path'], entry_path, *arguments]}

    @_locked
    def scan(self, binding: dict) -> dict:
        source, payload = self._bound(binding, require_epoch=False)
        if source['kind'] == 'NATIVE':
            root = Path(payload['root'])
            result = self._call(source, {'action': 'register', 'root': str(root)})
            if result['identity'] != payload['identity']:
                raise DomainError('WORKSPACE_ROOT_IDENTITY_CHANGED')
            with protection_helper._pin_directory(root, payload['identity'], ()):
                scan = scan_workspace(root, permission_backend=current_user_permission_backend())
            entries = [{**entry.to_extension_dict(), **({'content_b64': base64.b64encode(entry.content).decode('ascii')}
                        if entry.category == 'restorable' else {})} for entry in scan.entries]
            result = {'entries': entries, 'complete': scan.complete,
                      'reason_code': scan.reason_code}
        else:
            result = self._call(source, {'action': 'scan', **payload})
        if binding['epoch'] != source['epoch']:
            binding['epoch'] = source['epoch']
        return {**result, 'epoch': source['epoch']}

    @_locked
    def fingerprint(self, binding: dict) -> str:
        source, payload = self._bound(binding, require_epoch=False)
        result = self._call(source, {'action': 'fingerprint', **payload})
        digest = result.get('digest')
        if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            raise DomainError('DOMAIN_PROTOCOL_INVALID')
        return digest

    @_locked
    def available_bytes(self, binding: dict) -> int:
        source, payload = self._bound(binding, require_epoch=False)
        result = self._call(source, {'action': 'available_bytes', **payload})
        size = result.get('bytes')
        if type(size) is not int or size < 0:
            raise DomainError('DOMAIN_PROTOCOL_INVALID')
        return size

    @_locked
    def apply(self, binding: dict, operation_id: str, step_id: str, relative_path: str,
              expected: dict | None, target: dict | None) -> dict:
        source, payload = self._bound(binding)
        return self._call(source, {'action': 'apply', **payload, 'operation_id': operation_id,
                    'step_id': step_id, 'relative_path': relative_path,
                    'expected': expected, 'target': target})

    @_locked
    def reconcile(self, binding: dict, operation_id: str, step_id: str,
                  relative_path: str | None = None) -> dict:
        source, payload = self._bound(binding, require_epoch=False)
        request = {'action': 'reconcile', **payload, 'operation_id': operation_id,
                   'step_id': step_id}
        if relative_path is not None:
            request['relative_path'] = relative_path
        return self._call(source, request)

    @_locked
    def operation_storage(self, binding: dict, operation_id: str) -> int:
        source, payload = self._bound(binding, require_epoch=False)
        result = self._call(source, {'action': 'operation_storage', **payload,
                                     'operation_id': operation_id})
        size = result.get('bytes')
        if type(size) is not int or size < 0:
            raise DomainError('DOMAIN_PROTOCOL_INVALID')
        return size

    @_locked
    def cleanup_operation(self, binding: dict, operation_id: str) -> dict:
        source, payload = self._bound(binding, require_epoch=False)
        return self._call(source, {'action': 'cleanup_operation', **payload,
                                   'operation_id': operation_id})


__all__ = ['DomainError', 'DomainRegistry']
