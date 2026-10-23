"""Environment summaries describe observed sources, never a phantom default target."""
from importlib import import_module
from types import SimpleNamespace

from agentguard.core import versions
from agentguard.discovery.capabilities import CapabilityStatus


def test_status_lists_actual_running_containers_instead_of_legacy_default(monkeypatch):
    module = import_module('agentguard.commands.status')
    targets = (SimpleNamespace(name='agent-claude-code', status='Up'), SimpleNamespace(name='agent-codex-b', status='Up'))
    monkeypatch.setattr(module, 'DockerHostProbe', lambda: SimpleNamespace(list_targets=lambda: SimpleNamespace(
        status=CapabilityStatus.AVAILABLE, reason_code='DOCKER_TARGETS_LISTED', targets=targets)))
    monkeypatch.setattr(module, 'docker_available', lambda: True)
    monkeypatch.setattr(module, '_check_port', lambda _port: False)
    monkeypatch.setattr(module, 'all_versions', dict)
    value = module.status({'container_name': 'agent-dev'})
    assert value['checks']['container_running'] is True
    assert 'agent-claude-code' in value['checks']['container_info']
    assert 'agent-codex-b' in value['checks']['container_info']
    assert 'agent-dev' not in value['checks']['container_info']


def test_status_cannot_treat_failed_container_enumeration_as_stopped(monkeypatch):
    module = import_module('agentguard.commands.status')
    monkeypatch.setattr(module, 'DockerHostProbe', lambda: SimpleNamespace(list_targets=lambda: SimpleNamespace(
        status=CapabilityStatus.UNREACHABLE, reason_code='DOCKER_CONTEXT_UNAVAILABLE', targets=())))
    monkeypatch.setattr(module, 'docker_available', lambda: True)
    monkeypatch.setattr(module, '_check_port', lambda _port: False)
    monkeypatch.setattr(module, 'all_versions', dict)
    value = module.status({})
    assert value['checks']['container_running'] is None
    assert value['checks']['container_info'] == 'DOCKER_CONTEXT_UNAVAILABLE'


def test_core_python_version_does_not_require_python3_on_host_path(monkeypatch):
    monkeypatch.setattr(versions, '_get_version', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(versions.platform, 'python_version', lambda: '3.13.7')
    assert versions.all_versions()['python'] == 'Python 3.13.7 (Core)'


def test_doctor_windows_skips_linux_fixture_requirements_and_uses_actual_targets(monkeypatch):
    module = import_module('agentguard.commands.doctor')
    monkeypatch.setattr(module.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(module, '_in_container', lambda: False)
    monkeypatch.setattr(module, 'which', lambda name: name if name == 'docker' else None)
    monkeypatch.setattr(module, 'run_command', lambda *_args, **_kwargs: SimpleNamespace(success=True, stdout='27.0', stderr=''))
    monkeypatch.setattr(module, 'DockerHostProbe', lambda: SimpleNamespace(list_targets=lambda: SimpleNamespace(
        status=CapabilityStatus.AVAILABLE, reason_code='DOCKER_TARGETS_LISTED', targets=(SimpleNamespace(name='agent-codex-b', status='Up'),))))
    rows = module.doctor({'container_name': 'agent-dev'})
    assert any(row['check'] == 'container' and row['status'] == 'PASS' and 'agent-codex-b' in row['message'] for row in rows)
    assert not any('agent-dev' in str(row) for row in rows)
    assert next(row for row in rows if row['check'] == 'config-files')['status'] == 'SKIP'
    assert next(row for row in rows if row['check'] == 'python')['status'] == 'PASS'


def test_doctor_does_not_probe_an_unvalidated_remote_docker_daemon(monkeypatch):
    module = import_module('agentguard.commands.doctor')
    monkeypatch.setattr(module, 'which', lambda name: name if name == 'docker' else None)
    monkeypatch.setattr(module, '_check_port', lambda _port: False)
    monkeypatch.setattr(module, '_in_container', lambda: False)
    monkeypatch.setattr(module.platform, 'system', lambda: 'Windows')
    calls = []
    monkeypatch.setattr(module, 'run_command', lambda *args, **_kwargs: calls.append(args[0]) or SimpleNamespace(success=True, stdout='27', stderr=''))
    monkeypatch.setattr(module, 'DockerHostProbe', lambda: SimpleNamespace(list_targets=lambda: SimpleNamespace(
        status=CapabilityStatus.UNSUPPORTED, reason_code='DOCKER_REMOTE_ENDPOINT_REJECTED', targets=())))
    rows = module.doctor({})
    assert not any(command[0] == 'docker' for command in calls)
    assert next(row for row in rows if row['check'] == 'docker-daemon')['status'] == 'UNREACHABLE'
    assert any(row['message'] == 'DOCKER_REMOTE_ENDPOINT_REJECTED' for row in rows)
