from types import SimpleNamespace

from agentguard.discovery.agents import AgentRole
from agentguard.discovery.application_probe import (
    _windows_installed_entries,
    probe_entry,
)
from agentguard.discovery.signatures import classify_metadata


class Key:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def registry(entries):
    def open_key(parent, name):
        if parent == 'CU' and name == r'Software\Microsoft\Windows\CurrentVersion\Uninstall':
            return Key(entries)
        if isinstance(parent, Key):
            return Key(parent.value[int(name)])
        raise OSError()
    return SimpleNamespace(HKEY_CURRENT_USER='CU', HKEY_LOCAL_MACHINE='LM', OpenKey=open_key,
        QueryInfoKey=lambda key: (len(key.value), 0, 0), EnumKey=lambda key, index: str(index),
        QueryValueEx=lambda key, name: (key.value[name], 1))


def test_installed_harness_is_observed_from_bounded_registry_metadata_without_execution(tmp_path):
    target = tmp_path / 'DeepSeek Harness.exe'
    target.write_bytes(b'fixture entry, never executable')
    entries = [{'DisplayName': 'DeepSeek Harness 0.2.1-rc.2', 'DisplayIcon': f'"{target}",0'},
               {'DisplayName': 'DeepSeek Harness Lookalike', 'DisplayIcon': str(target)},
               {'DisplayName': 'DeepSeek Harness', 'DisplayIcon': str(target) + ' --unsafe-argument'}]
    paths = _windows_installed_entries(('deepseek harness',), registry(entries))
    assert paths == (str(target),)
    metadata = probe_entry(paths[0])
    assert metadata['status'] == 'AVAILABLE'
    assert classify_metadata(metadata, 'local') == ('DEEPSEEK_HARNESS', AgentRole.AGENT_HOST)
    assert 'control_capabilities' not in metadata
    assert 'pid' not in metadata
    assert 'lifecycle' not in metadata


def test_harness_signature_requires_exact_executable_not_a_folder_or_generic_dsh_name():
    for path in ['/opt/DeepSeek Harness/node.exe', '/opt/deepseek-harness-helper.exe', '/bin/dsh.exe']:
        assert classify_metadata({'path': path}, 'local') is None
    assert classify_metadata({'path': '/apps/deepseek-harness'}, 'local') == ('DEEPSEEK_HARNESS', AgentRole.AGENT_HOST)
