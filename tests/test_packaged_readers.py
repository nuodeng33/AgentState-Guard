"""Required packaged reader bytes and the local distribution gate."""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.verify_packaged_readers import (
    REQUIRED_READERS,
    PackagedReadersError,
    main,
    validate_carchive_toc,
    verify_packaged_readers,
)


class FakeArchive:
    def __init__(self, members, names=None):
        self.members = members
        self.toc = list(members) if names is None else names

    def extract(self, name):
        return self.members[name]


def fixture(tmp_path):
    root = tmp_path / "source checkout"
    members = {}
    for index, name in enumerate(REQUIRED_READERS):
        content = f"# audited reader {index}\r\nVALUE = {index}\r\n".encode()
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        members[name] = content
    executable = tmp_path / "sidecar with spaces.exe"
    executable.write_bytes(b"MZ-archive-fixture")
    return root, executable, members


def test_exact_raw_checkout_bytes_are_verified_without_pyinstaller(tmp_path, monkeypatch):
    root, executable, members = fixture(tmp_path)
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("PyInstaller"):
            raise AssertionError("Fake archive test imported PyInstaller")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    result = verify_packaged_readers(executable, root, reader_factory=lambda _: FakeArchive(members))
    assert result["result"] == "PASS"
    assert len(result["readers"]) == 4
    for item in result["readers"]:
        assert item["sha256"] == hashlib.sha256(members[item["path"]]).hexdigest()
        assert item["size_bytes"] == len(members[item["path"]])


@pytest.mark.parametrize("missing", REQUIRED_READERS)
def test_each_missing_source_member_rejects_distribution(tmp_path, missing):
    root, executable, members = fixture(tmp_path)
    del members[missing]
    with pytest.raises(PackagedReadersError) as error:
        verify_packaged_readers(executable, root, reader_factory=lambda _: FakeArchive(members))
    assert error.value.reason_code == "PACKAGED_READER_MISSING"
    assert error.value.reader_path == missing


@pytest.mark.parametrize("changed", REQUIRED_READERS)
def test_each_changed_member_rejects_even_when_module_names_exist(tmp_path, changed):
    root, executable, members = fixture(tmp_path)
    members[changed] = b"# different audited code\n"
    with pytest.raises(PackagedReadersError) as error:
        verify_packaged_readers(executable, root, reader_factory=lambda _: FakeArchive(members))
    assert error.value.reason_code == "PACKAGED_READER_HASH_MISMATCH"


def test_windows_archive_separators_are_accepted_but_alias_duplicates_are_not(tmp_path):
    root, executable, members = fixture(tmp_path)
    windows = {name.replace("/", "\\"): value for name, value in members.items()}
    assert verify_packaged_readers(executable, root, reader_factory=lambda _: FakeArchive(windows))["result"] == "PASS"
    names = list(windows) + [REQUIRED_READERS[0]]
    with pytest.raises(PackagedReadersError) as error:
        verify_packaged_readers(executable, root, reader_factory=lambda _: FakeArchive(windows, names))
    assert error.value.reason_code == "PACKAGED_READER_DUPLICATE"


def toc_entry(name, typecode=b"x"):
    encoded = name.encode() + b"\0"
    return struct.pack("!IIIIBc", 18 + len(encoded), 0, 1, 1, 0, typecode) + encoded


def test_raw_toc_catches_exact_duplicates_before_carchive_dict_collapses_them():
    name = REQUIRED_READERS[0]
    with pytest.raises(PackagedReadersError) as error:
        validate_carchive_toc(toc_entry(name) + toc_entry(name))
    assert error.value.reason_code == "PACKAGED_READER_DUPLICATE"
    validate_carchive_toc(toc_entry("option", b"o") + toc_entry("option", b"o"))


def test_archive_failure_and_unavailable_dependency_fail_closed(tmp_path, monkeypatch):
    root, executable, _members = fixture(tmp_path)

    def failed(_):
        raise OSError("Raw private diagnostic must not be exported")

    with pytest.raises(PackagedReadersError) as error:
        verify_packaged_readers(executable, root, reader_factory=failed)
    assert error.value.reason_code == "PACKAGED_READERS_ARCHIVE_INVALID"
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name.startswith("PyInstaller"):
            raise ModuleNotFoundError("PyInstaller absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(PackagedReadersError) as error:
        verify_packaged_readers(executable, root)
    assert error.value.reason_code == "PACKAGED_READERS_DEPENDENCY_UNAVAILABLE"


def test_cli_writes_json_failure_and_returns_nonzero(tmp_path, capsys):
    root, executable, members = fixture(tmp_path)
    del members[REQUIRED_READERS[-1]]
    report = tmp_path / "report.json"
    result = main(["--exe", str(executable), "--source-root", str(root), "--report", str(report)],
                  reader_factory=lambda _: FakeArchive(members))
    assert result == 1
    assert json.loads(report.read_text())["reason_code"] == "PACKAGED_READER_MISSING"
    assert json.loads(capsys.readouterr().out)["result"] == "FAIL"


@pytest.mark.skipif(os.name != "nt", reason="Windows build-script control flow")
def test_local_build_verifier_failure_stops_before_copy_or_bundle(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    root = tmp_path / "project with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(repository / "scripts/build-desktop.ps1", scripts / "build-desktop.ps1")
    shutil.copyfile(repository / "scripts/verify_packaged_readers.py", scripts / "verify_packaged_readers.py")
    provenance = root / "agentguard/core/_build_provenance.py"
    provenance.parent.mkdir(parents=True)
    original = b"# original\r\nPRODUCT_SHA = None\r\n"
    provenance.write_bytes(original)
    (root / "web").mkdir()
    binary = root / "desktop/src-tauri/bin/agentguard-sidecar-x86_64-pc-windows-msvc.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"previous verified binary")
    tools = tmp_path / "tools"
    tools.mkdir()
    driver = tools / "driver.py"
    driver.write_text(
        """import json,os,sys
from pathlib import Path
tool,*args=sys.argv[1:]
root=Path(os.environ['ASG_READER_BUILD_ROOT'])
stage='PyInstaller' if tool=='python' and '-m' in args else tool
if any(arg.endswith('verify_packaged_readers.py') for arg in args): stage='verify-readers'
if tool=='cargo' and 'build' in args: stage='tauri'
with (root/'calls.jsonl').open('a') as output: output.write(json.dumps(stage)+'\\n')
if stage=='verify-readers': sys.exit(9)
if tool=='git' and args[0]=='rev-parse': print('a'*40)
elif tool=='rustc': print('host: x86_64-pc-windows-msvc')
elif stage=='PyInstaller':
 destination=Path(args[args.index('--distpath')+1]);destination.mkdir(parents=True)
 (destination/'agentguard-sidecar.exe').write_bytes(b'new invalid archive')
""", encoding="utf-8")
    for tool in ("python", "git", "rustc", "cargo", "npm"):
        (tools / f"{tool}.cmd").write_text(f'@"{sys.executable}" "{driver}" {tool} %*\n')
    env = dict(os.environ, ASG_READER_BUILD_ROOT=str(root))
    env["PATH"] = str(tools) + os.pathsep + env["PATH"]
    completed = subprocess.run([shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-File", str(scripts / "build-desktop.ps1"),
                                "-Python", str(tools / "python.cmd")], cwd=root, env=env,
                               capture_output=True, timeout=60, check=False)
    assert completed.returncode != 0
    assert (root / "calls.jsonl").exists(), (completed.stdout + completed.stderr).decode(errors="replace")
    calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
    assert calls[-1] == "verify-readers"
    assert "tauri" not in calls
    assert binary.read_bytes() == b"previous verified binary"
    assert provenance.read_bytes() == original
    assert not list(root.glob("artifacts/desktop-build/*/provenance.json"))


def test_line_endings_are_not_normalized_before_hashing(tmp_path):
    root, executable, members = fixture(tmp_path)
    members[REQUIRED_READERS[0]] = members[REQUIRED_READERS[0]].replace(b"\r\n", b"\n")
    with pytest.raises(PackagedReadersError) as error:
        verify_packaged_readers(executable, root, reader_factory=lambda _: FakeArchive(members))
    assert error.value.reason_code == "PACKAGED_READER_HASH_MISMATCH"


def test_bytecode_module_metadata_loads_adjacent_audited_python_sources(tmp_path, monkeypatch):
    from agentguard.recovery import protection_domains, protection_helper
    # isort: split
    from agentguard.discovery import application_probe, session_metadata
    from agentguard.discovery.probes import container_processes

    modules = (application_probe, session_metadata, container_processes, protection_helper)
    markers = []
    for index, module in enumerate(modules):
        source = tmp_path / f"reader_{index}.py"
        marker = f"PACKAGED_READER_SOURCE_{index}"
        source.write_text(f"# {marker}\nVALUE = {index}\n", encoding="utf-8")
        monkeypatch.setattr(module, "__file__", str(source.with_suffix(".pyc")))
        markers.append(marker)
    assert markers[0] in container_processes.process_probe_source()
    assembled = protection_domains._helper_source()
    assert all(marker in assembled for marker in markers)
