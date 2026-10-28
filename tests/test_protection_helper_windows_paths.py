"""Private Windows journal paths use a verified-root extended I/O spelling."""
import base64
import errno
import hashlib
import os
import sys

import pytest

from agentguard.recovery import protection_helper as helper


@pytest.mark.parametrize("root,path", [
    ('C:\\checked', 'C:\\checked\\.agentguard\\protection\\aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
    ('C:\\checked', 'c:\\CHECKED\\.agentguard\\protection\\bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'),
])
def test_internal_windows_spelling_preserves_only_verified_local_descendants(root, path):
    assert helper._windows_internal_path(root, path) == "\\\\?\\" + path


@pytest.mark.parametrize("root,path", [
    (r"C:\checked", r"C:\other\private"),
    (r"C:\checked", r"C:\checked-sibling\private"),
    (r"C:\checked", r"D:\checked\private"),
    (r"C:\checked", r"C:\checked\..\other"),
    (r"C:\checked", r"C:\checked\file:stream"),
    (r"C:\checked", r"C:\checked\ambiguous."),
    (r"C:\checked", r"C:\checked\ambiguous "),
    (r"C:\checked", r"\\server\share\private"),
    (r"\\server\share", r"\\server\share\private"),
    (r"C:\checked", r"\\?\C:\checked\private"),
    (r"C:\checked", r"\\.\C:\checked\private"),
    (r"C:\checked", r"C:checked\private"),
])
def test_internal_windows_spelling_rejects_namespace_and_containment_bypasses(root, path):
    with pytest.raises(helper.HelperError, match="WORKSPACE_ISOLATION_UNSAFE"):
        helper._windows_internal_path(root, path)


def _root_length(tmp_path, length):
    root = tmp_path / "workspace"
    root = root / ("w" * (length - len(str(root)) - 1))
    root.mkdir(parents=True)
    assert len(str(root)) == length
    return root


@pytest.mark.skipif(os.name != "nt", reason="native Windows pathname API")
@pytest.mark.parametrize("root_length", [154, 200])
def test_atomic_receipt_read_replace_and_cleanup_cross_the_win32_boundary(tmp_path, monkeypatch, root_length):
    root = _root_length(tmp_path, root_length)
    registered = helper.handle({"action": "register", "root": str(root)})
    operation_id = "length-boundary"
    parts = helper._operation_dir(root, operation_id)
    opened, replaced, unlinked = [], [], []
    original_open, original_replace, original_unlink = os.open, os.replace, os.unlink

    def bounded(path):
        raw = os.fspath(path)
        if len(raw) >= 260 and not raw.startswith("\\\\?\\"):
            raise FileNotFoundError(errno.ENOENT, "legacy MAX_PATH boundary", raw)
        return raw

    def open_checked(path, *args, **kwargs):
        raw = bounded(path); opened.append(raw)
        return original_open(path, *args, **kwargs)

    def replace_checked(first, second, *args, **kwargs):
        replaced.extend((bounded(first), bounded(second)))
        return original_replace(first, second, *args, **kwargs)

    def unlink_checked(path, *args, **kwargs):
        unlinked.append(bounded(path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_checked)
    monkeypatch.setattr(os, "replace", replace_checked)
    monkeypatch.setattr(os, "unlink", unlink_checked)
    with helper._pin_directory(root, registered["identity"], parts, create=True) as isolated:
        stem = helper._digest(b"step")[:32]
        receipt = isolated / (stem + ".receipt.json")
        assert len(str(root.joinpath(*parts) / (receipt.name + ".tmp"))) >= 260
        helper._atomic_json(isolated / "owner.json", {"operation_id": operation_id, "root_identity": registered["identity"]})
        helper._atomic_json(receipt, {"state": "first"})
        helper._atomic_json(receipt, {"state": "replaced"})
        assert helper._load_json(receipt) == {"state": "replaced"}
    request = {**registered, "operation_id": operation_id}
    assert helper.handle({"action": "operation_storage", **request})["bytes"] > 0
    assert helper.handle({"action": "cleanup_operation", **request})["removed"] is True
    assert helper.handle({"action": "operation_storage", **request})["bytes"] == 0
    assert any(len(path) >= 260 for path in opened)
    assert any(len(path) >= 260 for path in replaced)
    assert any(len(path) >= 260 for path in unlinked)


@pytest.mark.skipif(os.name != "nt" or sys.version_info[:2] != (3, 11),
                    reason="shipping Windows helper uses Python 3.11; 3.12 mkdir DACL behavior is a separate known issue")
def test_shipping_windows_helper_apply_reconcile_and_external_edit_keep_long_journals(tmp_path):
    root = _root_length(tmp_path, 200)
    target = root / "note.txt"
    target.write_bytes(b"before")
    registered = helper.handle({"action": "register", "root": str(root)})
    before = helper.handle({"action": "scan", **registered})["entries"][0]
    after = {**before, "content_b64": base64.b64encode(b"after").decode(), "size": 5,
             "content_digest": hashlib.sha256(b"after").hexdigest()}
    request = {**registered, "operation_id": "long-apply", "step_id": "step", "relative_path": "note.txt",
               "expected": before, "target": after}
    applied = helper.handle({"action": "apply", **request})
    assert applied["state"] == "VERIFIED"
    assert target.read_bytes() == b"after"
    assert helper.handle({"action": "reconcile", **request})["state"] == "VERIFIED"
    target.write_bytes(b"external")
    assert helper.handle({"action": "reconcile", **request})["state"] == "NEEDS_ATTENTION"
    assert target.read_bytes() == b"external"
    assert helper.handle({"action": "cleanup_operation", **request})["removed"] is True
    assert target.read_bytes() == b"external"
