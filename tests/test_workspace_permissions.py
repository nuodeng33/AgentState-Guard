"""Current-user-only workspace permission proof contracts."""

from __future__ import annotations

import os
import stat

import pytest

from agentguard.recovery.workspace_permissions import (
    PermissionCapabilityError,
    PermissionProof,
    PosixPermissionBackend,
    WindowsPermissionBackend,
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits require a POSIX filesystem")
def test_posix_mode_capture_apply_and_verify(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("content", encoding="utf-8")
    target.chmod(0o640)
    backend = PosixPermissionBackend()

    proof = backend.capture(target)
    target.chmod(0o600)
    assert backend.verify(target, proof) is False
    backend.apply(target, proof)

    assert backend.verify(target, proof) is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


class _FakeWindowsApi:
    def __init__(self) -> None:
        self.attributes = 0x22
        self.dacl = "D:(A;;FA;;;S-1-5-21-1000)"
        self.elevation_calls = 0

    def get_file_attributes(self, _path):
        return self.attributes

    def set_file_attributes(self, _path, attributes):
        self.attributes = attributes

    def get_dacl_sddl(self, _path):
        return self.dacl

    def set_dacl_sddl(self, _path, dacl):
        self.dacl = dacl


def test_windows_dacl_proof_uses_current_process_only(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("content", encoding="utf-8")
    api = _FakeWindowsApi()
    backend = WindowsPermissionBackend(api=api)

    proof = backend.capture(target)
    api.attributes = 0
    api.dacl = "D:"
    backend.apply(target, proof)

    assert backend.verify(target, proof) is True
    assert proof.kind == "WINDOWS_ATTRIBUTES_DACL"
    assert api.elevation_calls == 0


def test_windows_permission_denial_maps_to_stable_reason(tmp_path):
    class DeniedApi(_FakeWindowsApi):
        def get_dacl_sddl(self, _path):
            raise PermissionError("private operating-system detail")

    target = tmp_path / "file.txt"
    target.write_text("content", encoding="utf-8")

    with pytest.raises(
        PermissionCapabilityError,
        match="WORKSPACE_PERMISSION_CAPTURE_DENIED",
    ):
        WindowsPermissionBackend(api=DeniedApi()).capture(target)


def test_permission_proof_rejects_digest_tampering():
    proof = PermissionProof(kind="POSIX_MODE", values={"mode": "0o640"})
    encoded = proof.to_dict()
    encoded["values"]["mode"] = "0o777"

    with pytest.raises(ValueError, match="WORKSPACE_PERMISSION_PROOF_INVALID"):
        PermissionProof.from_dict(encoded)


def test_permission_backend_never_changes_process_privileges(monkeypatch, tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("content", encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("permission proof must not elevate")

    monkeypatch.setattr(os, "system", forbidden)
    proof = PosixPermissionBackend().capture(target)

    assert proof.kind == "POSIX_MODE"


@pytest.mark.parametrize("changed", [
    "D:AI(A;;FA;;;SY)", "D:P(A;;FA;;;SY)", "D:AR(A;;FA;;;SY)",
    "D:(A;;FR;;;SY)", "D:(A;;FA;;;BA)", "D:(A;ID;FA;;;SY)",
])
def test_windows_permission_comparison_ignores_only_auto_inherited_marker(changed):
    original = PermissionProof(
        kind="WINDOWS_ATTRIBUTES_DACL",
        values={"attributes": 32, "dacl_sddl": "D:(A;;FA;;;SY)"},
    )
    other = PermissionProof(
        kind=original.kind, values={"attributes": 32, "dacl_sddl": changed},
    )
    assert original.matches(other) is changed.startswith("D:AI(")
    assert original.digest != other.digest
    assert PermissionProof.from_dict(other.to_dict()) == other


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows DACL API")
@pytest.mark.parametrize("protected", [False, True])
def test_native_windows_permission_roundtrip_on_regular_file(tmp_path, protected):
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("source")
    target.write_text("target")
    backend = WindowsPermissionBackend()
    proof = backend.capture(source)
    if protected:
        dacl = str(proof.values["dacl_sddl"])
        proof = PermissionProof(kind=proof.kind, values={
            "attributes": proof.values["attributes"],
            "dacl_sddl": "D:P" + dacl[2:],
        })
        backend.apply(source, proof)
        proof = backend.capture(source)
    backend.apply(target, proof)

    assert backend.verify(target, proof)
    captured = backend.capture(target)
    assert ("P" in str(captured.values["dacl_sddl"]).partition("(")[0]) is protected
