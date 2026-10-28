"""Preview retention verifies historical bytes once without eager hydration."""
import gzip
from collections import Counter

import pytest

from agentguard.recovery.protection import ProtectionError
from tests.test_protection_core import _enroll, _file_ids, _service


def test_preview_verifies_each_target_once_without_hydrating_or_caching_across_requests(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for index in range(3):
        (project / f"{index}.txt").write_text(f"historical-{index}")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        ids = list(_file_ids(service, baseline).values())
        targets = {entry["content_digest"] for entry in service.version(baseline)["files"]}
        for index in range(3):
            (project / f"{index}.txt").write_text(f"current-{index}")
        service.capture(scope_id, force=True)
        original_get, original_hydrate = service.blobs.get, service._hydrate
        reads, hydrations = Counter(), []

        def observed_get(sha):
            reads[sha] += 1
            return original_get(sha)

        def observed_hydrate(entry):
            hydrations.append(entry)
            return original_hydrate(entry)

        monkeypatch.setattr(service.blobs, "get", observed_get)
        monkeypatch.setattr(service, "_hydrate", observed_hydrate)
        for attempt in range(1, 3):
            preview = service.preview(scope_id, "restore_version", ids, version_id=baseline)
            assert preview["status"] == "READY"
            assert {sha: reads[sha] for sha in targets} == dict.fromkeys(targets, attempt)
            assert hydrations == []
            assert all("content_b64" not in entry for item in preview["selection"]
                       for entry in (item["before"], item["after"]) if entry)
            assert service.conn.execute(
                "SELECT COUNT(*) FROM protection_blob_refs WHERE owner_kind='preview' AND owner_id=?",
                (preview["preview_id"],),
            ).fetchone()[0] == 6
        for sha in targets:
            assert service.conn.execute("SELECT ref_count FROM blobs WHERE sha256=?", (sha,)).fetchone()[0] == 3
    finally:
        service.close()


@pytest.mark.parametrize("damage", ["missing", "wrong-hash", "bad-gzip", "missing-metadata"])
def test_damaged_preview_target_rolls_back_all_retained_references(tmp_path, damage):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("a", "b"):
        (project / (name + ".txt")).write_text("historical-" + name)
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        ids = _file_ids(service, baseline)
        targets = {entry["relative_path"]: entry for entry in service.version(baseline)["files"]}
        for name in ("a", "b"):
            (project / (name + ".txt")).write_text("current-" + name)
        service.capture(scope_id, force=True)
        sha = targets["b.txt"]["content_digest"]
        path = service.blobs._blob_path(sha)
        if damage == "missing":
            path.unlink()
        elif damage == "wrong-hash":
            path.write_bytes(gzip.compress(b"changed-b"))
        elif damage == "bad-gzip":
            path.write_bytes(b"bad gzip")
        else:
            with service.db.transaction() as conn:
                conn.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
        before = service.conn.execute("SELECT sha256,ref_count FROM blobs ORDER BY sha256").fetchall()
        references = service.conn.execute("SELECT * FROM protection_blob_refs ORDER BY owner_kind,owner_id,sha256").fetchall()
        with pytest.raises(ProtectionError, match="PROTECTION_CONTENT_UNAVAILABLE"):
            service.preview(scope_id, "restore_version", [ids["a.txt"], ids["b.txt"]], version_id=baseline)
        assert service.conn.execute("SELECT sha256,ref_count FROM blobs ORDER BY sha256").fetchall() == before
        assert service.conn.execute("SELECT * FROM protection_blob_refs ORDER BY owner_kind,owner_id,sha256").fetchall() == references
        assert service.conn.execute("SELECT COUNT(*) FROM protection_previews").fetchone()[0] == 0
        assert service.conn.execute("SELECT COUNT(*) FROM protection_operations").fetchone()[0] == 0
        assert (project / "a.txt").read_text() == "current-a"
        assert (project / "b.txt").read_text() == "current-b"
    finally:
        service.close()


def test_preview_deduplicates_identical_blob_references_per_owner(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("a.txt", "b.txt"):
        (project / name).write_text("same historical bytes")
    service = _service(tmp_path)
    try:
        scope_id, baseline = _enroll(service, project)
        ids = list(_file_ids(service, baseline).values())
        sha = service.version(baseline)["files"][0]["content_digest"]
        for name in ("a.txt", "b.txt"):
            (project / name).write_text("same current bytes")
        service.capture(scope_id, force=True)
        before = service.conn.execute("SELECT ref_count FROM blobs WHERE sha256=?", (sha,)).fetchone()[0]
        preview = service.preview(scope_id, "restore_version", ids, version_id=baseline)
        assert service.conn.execute("SELECT ref_count FROM blobs WHERE sha256=?", (sha,)).fetchone()[0] == before + 1
        assert service.conn.execute("SELECT COUNT(*) FROM protection_blob_refs WHERE owner_kind='preview' AND owner_id=?",
                                    (preview["preview_id"],)).fetchone()[0] == 2
    finally:
        service.close()


@pytest.mark.parametrize("payload,reason", [
    (b"x" * (4 * 1024 * 1024 + 1), "PROTECTION_CONTENT_INVALID"),
    (b"API_KEY=sk-ant-fake-test1234567890", "PROTECTION_SENSITIVE_CONTENT_NOT_STORED"),
])
def test_retained_bytes_still_receive_limits_and_privacy_checks_before_commit(tmp_path, payload, reason):
    service = _service(tmp_path)
    try:
        with service.db.transaction() as conn:
            sha = service.blobs.put(payload, conn)
        before = service.conn.execute("SELECT ref_count FROM blobs WHERE sha256=?", (sha,)).fetchone()[0]
        with pytest.raises(ProtectionError, match=reason), service.db.transaction() as conn:
            service._save_manifest(conn, "unpublished-scope", "unpublished-preview",
                                   {"file": {"category": "restorable", "content_digest": sha}}, owner_kind="preview")
        assert service.conn.execute("SELECT ref_count FROM blobs WHERE sha256=?", (sha,)).fetchone()[0] == before
        assert service.conn.execute("SELECT COUNT(*) FROM protection_blob_refs WHERE owner_id='unpublished-preview'").fetchone()[0] == 0
    finally:
        service.close()
