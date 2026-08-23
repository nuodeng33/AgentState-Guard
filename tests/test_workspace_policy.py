"""Deterministic four-category Host-native workspace coverage tests."""

from __future__ import annotations

from agentguard.recovery.workspace_permissions import (
    PermissionCapabilityError,
    PosixPermissionBackend,
)
from agentguard.recovery.workspace_policy import WorkspaceScanLimits, scan_workspace


class _SelectivePermissionBackend:
    def __init__(self) -> None:
        self._delegate = PosixPermissionBackend()
        self.elevation_calls = 0

    def capture(self, path):
        if path.name == "locked.txt":
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_CAPTURE_UNAVAILABLE")
        return self._delegate.capture(path)

    def apply(self, path, proof):
        return self._delegate.apply(path, proof)

    def verify(self, path, proof):
        return self._delegate.verify(path, proof)


def test_scan_reports_all_four_categories(tmp_path):
    (tmp_path / "source.txt").write_text("safe content", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=not-retained", encoding="utf-8")
    (tmp_path / "locked.txt").write_text("unreachable", encoding="utf-8")
    dependency = tmp_path / "node_modules"
    dependency.mkdir()
    (dependency / "package.js").write_text("not traversed", encoding="utf-8")
    backend = _SelectivePermissionBackend()

    scan = scan_workspace(tmp_path, permission_backend=backend)

    assert scan.counts == {
        "restorable": 1,
        "audit_only": 1,
        "excluded": 1,
        "unreachable": 1,
    }
    entries = {item.relative_path: item for item in scan.entries}
    assert entries["source.txt"].content == b"safe content"
    assert entries[".env"].content is None
    assert entries["node_modules"].reason_code == "WORKSPACE_DEPENDENCY_EXCLUDED"
    assert "node_modules/package.js" not in entries
    assert entries["locked.txt"].reason_code == (
        "WORKSPACE_PERMISSION_CAPTURE_UNAVAILABLE"
    )
    assert backend.elevation_calls == 0


def test_oversize_file_is_audit_only_without_retained_content(tmp_path):
    target = tmp_path / "large.bin"
    target.write_bytes(b"x" * 17)

    scan = scan_workspace(
        tmp_path,
        permission_backend=PosixPermissionBackend(),
        limits=WorkspaceScanLimits(max_file_bytes=16),
    )

    entry = scan.entries[0]
    assert entry.category == "audit_only"
    assert entry.reason_code == "WORKSPACE_SIZE_LIMIT_AUDIT_ONLY"
    assert entry.content is None


def test_symlink_is_excluded_and_never_followed(tmp_path):
    outside = tmp_path.parent / "outside-workspace.txt"
    outside.write_text("outside", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    try:
        alias.symlink_to(outside)
    except OSError:
        return

    scan = scan_workspace(tmp_path, permission_backend=PosixPermissionBackend())

    assert len(scan.entries) == 1
    assert scan.entries[0].category == "excluded"
    assert scan.entries[0].reason_code == "WORKSPACE_REPARSE_POINT_EXCLUDED"
    assert scan.entries[0].content is None


def test_scan_order_and_digest_are_deterministic(tmp_path):
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    backend = PosixPermissionBackend()

    first = scan_workspace(tmp_path, permission_backend=backend)
    second = scan_workspace(tmp_path, permission_backend=backend)

    assert [item.relative_path for item in first.entries] == ["a.txt", "z.txt"]
    assert first.coverage_digest == second.coverage_digest


def test_large_coverage_digest_is_supported(tmp_path):
    for index in range(65):
        (tmp_path / f"fixture-{index:03d}.txt").write_text("safe", encoding="utf-8")

    scan = scan_workspace(tmp_path, permission_backend=PosixPermissionBackend())

    assert scan.complete is True
    assert scan.counts["restorable"] == 65
    assert len(scan.coverage_digest) == 64


def test_default_entry_budget_completes_above_previous_10000_boundary(tmp_path):
    assert WorkspaceScanLimits().max_entries == 25_000
    for index in range(10_001):
        (tmp_path / f"generated-{index:05d}.pyc").touch()

    scan = scan_workspace(tmp_path, permission_backend=PosixPermissionBackend())

    assert scan.complete is True
    assert scan.reason_code == "WORKSPACE_SCAN_COMPLETE"
    assert scan.counts == {
        "restorable": 0,
        "audit_only": 0,
        "excluded": 10_001,
        "unreachable": 0,
    }


def test_file_count_limit_fails_closed_with_explicit_residue(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")

    scan = scan_workspace(
        tmp_path,
        permission_backend=PosixPermissionBackend(),
        limits=WorkspaceScanLimits(max_entries=1),
    )

    assert scan.complete is False
    assert scan.reason_code == "WORKSPACE_SCAN_ENTRY_LIMIT_REACHED"
