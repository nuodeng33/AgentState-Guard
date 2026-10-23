"""Frozen module metadata points to bytecode; audited sources remain adjacent data."""
import importlib
from pathlib import Path


def _modules():
    # Match Core's domains-first composition; importing leaf probes first is not
    # the application's import path.
    domains = importlib.import_module('agentguard.recovery.protection_domains')
    app = importlib.import_module('agentguard.discovery.application_probe')
    sessions = importlib.import_module('agentguard.discovery.session_metadata')
    processes = importlib.import_module('agentguard.discovery.probes.container_processes')
    helper = importlib.import_module('agentguard.recovery.protection_helper')
    return domains, app, sessions, processes, helper


def _bytecode_metadata(module, folder, monkeypatch):
    source = Path(module.__file__).with_suffix('.py').read_text(encoding='utf-8')
    raw = folder / (module.__name__.split('.')[-1] + '.py')
    raw.write_text(source, encoding='utf-8')
    bytecode = raw.with_suffix('.pyc')
    assert not bytecode.exists()
    monkeypatch.setattr(module, '__file__', str(bytecode))


def test_process_probe_reads_audited_source_beside_frozen_bytecode(tmp_path, monkeypatch):
    _, application_probe, _, container_processes, _ = _modules()
    _bytecode_metadata(application_probe, tmp_path, monkeypatch)
    source = container_processes.process_probe_source()
    compile(source, 'packaged-process-probe', 'exec')
    assert 'asg_application_probe' in source and 'MAX_PROCESSES' in source


def test_domain_helper_loads_all_four_adjacent_raw_sources(tmp_path, monkeypatch):
    protection_domains, application_probe, session_metadata, container_processes, protection_helper = _modules()
    for module in [application_probe, session_metadata, container_processes, protection_helper]:
        _bytecode_metadata(module, tmp_path, monkeypatch)
    source = protection_domains._helper_source()
    compile(source, 'packaged-domain-reader', 'exec')
    assert all(name in source for name in ['_asg_applications', '_asg_sessions', '_asg_processes'])
