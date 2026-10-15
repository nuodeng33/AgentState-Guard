"""Contract tests for the standalone provider-store metadata reader."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

from agentguard.discovery import session_metadata
from agentguard.discovery.session_metadata import (
    default_stores,
    inspect_store,
    read_store,
)


def _codex_rollout(root: Path, name: str, session_id: str) -> None:
    folder = root / "sessions" / "2026" / "09" / "29"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"rollout-{name}.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"id": session_id,
                    "cwd": "/work/codex", "timestamp": "2026-09-29T10:00:00Z"}})
        + "\n" + json.dumps({"type": "message", "content": "private body"}) + "\n",
        encoding="utf-8",
    )


def test_default_stores_only_uses_fixed_provider_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "/custom/codex")
    monkeypatch.setenv("KIMI_CODE_HOME", "/custom/kimi")
    monkeypatch.setenv("DATABASE_PATH", "/custom/cloudcli/index.db")
    stores = {item["provider"]: item["path"] for item in default_stores()}
    assert stores["CODEX"] == "/custom/codex"
    assert stores["KIMI_CODE"] == "/custom/kimi"
    assert stores["CLOUDCLI"] == "/custom/cloudcli/index.db"
    assert stores["ZCODE"].endswith(".zcode")


def test_codex_bounded_first_line_and_pages(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    for number in range(3):
        _codex_rollout(root, str(number), f"codex-{number}")
    first = read_store("CODEX", root, limit=2)
    second = read_store("CODEX", root, limit=2, offset=first["next_offset"])
    assert first["status"] == second["status"] == "AVAILABLE"
    assert len(first["sessions"]) == 2
    assert len(second["sessions"]) == 1
    assert first["has_more"] is True and first["next_offset"] == 2
    assert second["has_more"] is False and second["next_offset"] is None
    assert {item["session_id"] for item in first["sessions"] + second["sessions"]} == {
        "codex-0", "codex-1", "codex-2",
    }
    assert all(item["title"] is None and item["provider"] == "CODEX"
               for item in first["sessions"] + second["sessions"])


def test_codex_corrupt_metadata_is_never_empty(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    folder = root / "sessions"
    folder.mkdir(parents=True)
    (folder / "rollout-bad.jsonl").write_text("{broken\n", encoding="utf-8")
    result = read_store("CODEX", root)
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == "SESSION_METADATA_PARTIAL"
    _codex_rollout(root, "good", "codex-good")
    result = read_store("CODEX", root)
    assert result["status"] == "DEGRADED"
    assert [item["session_id"] for item in result["sessions"]] == ["codex-good"]


def test_codex_large_first_line_is_metadata_only_and_excess_is_partial(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    folder = root / "sessions" / "2026" / "09" / "29"
    folder.mkdir(parents=True)
    secret = "private-instructions-" * 1000
    document = {"type": "session_meta", "payload": {"id": "large-meta",
                "cwd": "/work/codex", "timestamp": "2026-09-29T10:00:00Z",
                "base_instructions": secret}}
    large = folder / "rollout-large.jsonl"
    large.write_text(json.dumps(document) + "\n" +
                     json.dumps({"type": "message", "content": "private body"}) + "\n",
                     encoding="utf-8")
    assert 16 * 1024 < len(json.dumps(document).encode()) < 256 * 1024
    result = read_store("CODEX", root)
    assert result["status"] == "AVAILABLE"
    assert result["sessions"][0]["session_id"] == "large-meta"
    assert secret not in json.dumps(result)
    assert "private body" not in json.dumps(result)

    (folder / "rollout-excess.jsonl").write_text(json.dumps({
        "type": "session_meta", "payload": {"id": "too-large",
            "base_instructions": "x" * (256 * 1024)},
    }) + "\n", encoding="utf-8")
    limited = read_store("CODEX", root)
    assert limited["status"] == "DEGRADED"
    assert limited["reason_code"] == "SESSION_METADATA_LIMIT"
    assert [item["session_id"] for item in limited["sessions"]] == ["large-meta"]


def test_kimi_index_pagination_tombstone_and_partial(tmp_path: Path) -> None:
    root = tmp_path / ".kimi-code"
    sessions_root = root / "sessions"
    lines = []
    for number in range(3):
        session_id = f"kimi-{number}"
        folder = sessions_root / "bucket" / session_id
        folder.mkdir(parents=True)
        os.utime(folder, (1_000 + number, 1_000 + number))
        lines.append({"sessionId": session_id, "sessionDir": str(folder),
                      "workDir": "/work/kimi"})
    lines.append({"sessionId": "kimi-2", "deleted": True})
    (root / "session_index.jsonl").write_text(
        "\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8",
    )
    first = read_store("KIMI_CODE", root, limit=1)
    second = read_store("KIMI_CODE", root, limit=1, offset=1)
    assert first["status"] == second["status"] == "AVAILABLE"
    assert first["sessions"][0]["session_id"] == "kimi-1"
    assert second["sessions"][0]["session_id"] == "kimi-0"
    assert first["next_offset"] == 1 and second["next_offset"] is None
    with (root / "session_index.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{broken\n")
    assert read_store("KIMI_CODE", root)["status"] == "DEGRADED"


def _zcode_databases(root: Path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    task_path = root / "v2" / "tasks-index.sqlite"
    session_path = root / "cli" / "db" / "db.sqlite"
    task_path.parent.mkdir(parents=True)
    session_path.parent.mkdir(parents=True)
    tasks = sqlite3.connect(task_path)
    sessions = sqlite3.connect(session_path)
    tasks.execute("CREATE TABLE tasks (task_id TEXT, title TEXT, updated_at INTEGER, "
                  "workspace_path TEXT, archived INTEGER, deleted INTEGER)")
    sessions.execute("CREATE TABLE session (id TEXT, directory TEXT)")
    tasks.commit()
    sessions.commit()
    return tasks, sessions


def test_zcode_tasks_join_session_id_and_report_partial(tmp_path: Path) -> None:
    root = tmp_path / ".zcode"
    tasks, sessions = _zcode_databases(root)
    try:
        tasks.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                      ("task-2", "Second", 1_780_000_002_000, "/work/second", 0, 0))
        tasks.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                      ("task-1", "First", 1_780_000_001_000, "/work/first", 0, 0))
        tasks.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                      ("task-deleted", "Deleted", 1_780_000_003_000, "/work", 0, 1))
        sessions.execute("INSERT INTO session VALUES (?,?)", ("task-1", "/work/first"))
        tasks.commit()
        sessions.commit()
        first = read_store("ZCODE", root, limit=1)
        second = read_store("ZCODE", root, limit=1, offset=1)
        assert first["status"] == second["status"] == "DEGRADED"
        assert first["reason_code"] == "SESSION_LINK_INCOMPLETE"
        assert first["sessions"][0]["mapping_status"] == "UNMATCHED"
        assert second["sessions"][0]["mapping_status"] == "MATCHED"
        assert first["next_offset"] == 1 and second["has_more"] is False
        assert all(item["provider"] == "ZCODE" for item in first["sessions"] + second["sessions"])
    finally:
        tasks.close()
        sessions.close()


def test_zcode_missing_cli_db_keeps_tasks_as_partial(tmp_path: Path) -> None:
    root = tmp_path / ".zcode"
    tasks, sessions = _zcode_databases(root)
    tasks.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                  ("task-1", "First", 1_780_000_001_000, "/work/first", 0, 0))
    tasks.commit()
    tasks.close()
    sessions.close()
    (root / "cli" / "db" / "db.sqlite").unlink()
    result = read_store("ZCODE", root)
    assert result["status"] == "DEGRADED"
    assert result["sessions"][0]["mapping_status"] == "UNMATCHED"


def test_zcode_mismatched_directory_marks_every_page_degraded(tmp_path: Path) -> None:
    root = tmp_path / ".zcode"
    tasks, sessions = _zcode_databases(root)
    try:
        tasks.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                      ("newer", "Newer", 2, "/work/newer", 0, 0))
        tasks.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                      ("older", "Older", 1, "/work/older", 0, 0))
        sessions.execute("INSERT INTO session VALUES (?,?)", ("newer", "/work/newer/."))
        sessions.execute("INSERT INTO session VALUES (?,?)", ("older", "/work/other"))
        tasks.commit()
        sessions.commit()
        first = read_store("ZCODE", root, limit=1)
        second = read_store("ZCODE", root, limit=1, offset=1)
        assert first["status"] == second["status"] == "DEGRADED"
        assert first["sessions"][0]["mapping_status"] == "MATCHED"
        assert second["sessions"][0]["mapping_status"] == "UNMATCHED"
    finally:
        tasks.close()
        sessions.close()


def test_zcode_empty_index_with_missing_link_db_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / ".zcode"
    tasks, sessions = _zcode_databases(root)
    tasks.close()
    sessions.close()
    (root / "cli" / "db" / "db.sqlite").unlink()
    assert read_store("ZCODE", root)["status"] == "UNKNOWN"


def _cloudcli_database(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "auth.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, provider TEXT, "
                       "provider_session_id TEXT, custom_name TEXT, project_path TEXT, "
                       "isArchived INTEGER, created_at TEXT, updated_at TEXT)")
    connection.commit()
    return connection


def test_cloudcli_reads_wal_and_distinguishes_app_from_native_id(tmp_path: Path) -> None:
    root = tmp_path / ".cloudcli"
    writer = _cloudcli_database(root)
    try:
        writer.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                       ("app-2", "codex", "native-2", "Second", "/work/two", 0,
                        "2026-09-29 10:00:00", "2026-09-29 12:00:00"))
        writer.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                       ("app-1", "claude", None, "First", "/work/one", 0,
                        "2026-09-29 09:00:00", "2026-09-29 11:00:00"))
        writer.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                       ("app-archived", "codex", "native-old", "Archived", "/work", 1,
                        "2026-09-29 08:00:00", "2026-09-29 08:00:00"))
        writer.commit()
        first = read_store("CLOUDCLI", root, limit=1)
        second = read_store("CLOUDCLI", root / "auth.db", limit=1, offset=1)
        assert first["status"] == second["status"] == "AVAILABLE"
        assert first["sessions"][0]["native_provider"] == "CODEX"
        assert first["sessions"][0]["native_session_id"] == "native-2"
        assert first["sessions"][0]["app_session_id"] == "app-2"
        assert second["sessions"][0]["mapping_status"] == "APP_ONLY"
        assert second["sessions"][0]["native_session_id"] is None
        assert first["next_offset"] == 1 and second["has_more"] is False
        assert first["store_identity"] == second["store_identity"]
    finally:
        writer.close()


def test_inspect_identity_binds_directory_not_db_file(tmp_path: Path) -> None:
    root = tmp_path / ".cloudcli"
    writer = _cloudcli_database(root)
    writer.close()
    first = inspect_store("CLOUDCLI", root)
    (root / "auth.db").unlink()
    writer = _cloudcli_database(root)
    writer.close()
    second = inspect_store("CLOUDCLI", root)
    assert first["status"] == second["status"] == "AVAILABLE"
    assert first["identity"] == second["identity"]


def test_missing_bad_schema_and_unsafe_paths(tmp_path: Path) -> None:
    assert read_store("CLOUDCLI", tmp_path / "missing")["status"] == "UNKNOWN"
    assert inspect_store("CODEX", "relative/path")["reason_code"] == "SESSION_STORE_PATH_UNSAFE"
    root = tmp_path / ".cloudcli"
    root.mkdir()
    sqlite3.connect(root / "auth.db").close()
    assert read_store("CLOUDCLI", root)["reason_code"] == "SESSION_SCHEMA_UNRECOGNIZED"
    link = tmp_path / "linked"
    try:
        link.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    assert read_store("CLOUDCLI", link)["reason_code"] == "SESSION_STORE_UNSAFE"


def test_corrupt_sqlite_and_expired_query_fail_closed(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / ".cloudcli"
    root.mkdir()
    (root / "auth.db").write_bytes(b"not a sqlite database")
    result = read_store("CLOUDCLI", root)
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == "SESSION_SCHEMA_UNRECOGNIZED"
    (root / "auth.db").unlink()
    _cloudcli_database(root).close()
    zcode = tmp_path / ".zcode"
    tasks, sessions = _zcode_databases(zcode)
    tasks.close()
    sessions.close()
    monkeypatch.setattr(session_metadata, "QUERY_SECONDS", 0.0)
    for provider, path in (("CLOUDCLI", root), ("ZCODE", zcode)):
        expired = read_store(provider, path)
        assert expired["status"] == "UNKNOWN"
        assert expired["reason_code"] == "SESSION_QUERY_LIMIT"


def test_invalid_pagination_fails_closed(tmp_path: Path) -> None:
    result = read_store("CODEX", tmp_path, limit=51)
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == "SESSION_PAGE_INVALID"
    assert inspect_store([], tmp_path)["reason_code"] == "SESSION_PROVIDER_UNSUPPORTED"
    assert read_store("CODEX", tmp_path, limit=True)["reason_code"] == "SESSION_PAGE_INVALID"
    assert read_store("CODEX", tmp_path, offset=True)["reason_code"] == "SESSION_PAGE_INVALID"
    root = tmp_path / ".codex"
    _codex_rollout(root, "only", "codex-only")
    beyond = read_store("CODEX", root, limit=1, offset=1)
    assert beyond["status"] == "AVAILABLE"
    assert beyond["sessions"] == []
    assert beyond["has_more"] is False and beyond["next_offset"] is None


def test_official_kimi_layout_and_revision(tmp_path: Path) -> None:
    root = tmp_path / ".kimi"
    cwd = "/work/kimi"
    bucket = root / "sessions" / hashlib.md5(cwd.encode()).hexdigest()
    first_dir = bucket / "session-one"
    first_dir.mkdir(parents=True)
    context = first_dir / "context.jsonl"
    context.write_text('{"role":"user","content":"private"}\n', encoding="utf-8")
    (root / "kimi.json").write_text(json.dumps({"work_dirs": [{"path": cwd, "kaos": "local"}]}),
                                     encoding="utf-8")
    first = read_store("KIMI_CODE", root)
    assert first["status"] == "AVAILABLE"
    assert first["sessions"][0]["cwd"] == cwd
    assert "private" not in json.dumps(first)
    assert first["revision"]
    unchanged = read_store("KIMI_CODE", root, known_revision=first["revision"])
    assert unchanged["not_modified"] is True and unchanged["sessions"] == []
    old = context.stat()
    context.write_text('{"role":"user","content":"public!"}\n', encoding="utf-8")
    assert context.stat().st_size == old.st_size
    changed = read_store("KIMI_CODE", root, known_revision=first["revision"])
    assert changed["not_modified"] is False
    assert changed["revision"] != first["revision"]


def test_official_kimi_legacy_file_without_migration(tmp_path: Path) -> None:
    root = tmp_path / ".kimi"
    cwd = "/work/kimi"
    bucket = root / "sessions" / hashlib.md5(cwd.encode()).hexdigest()
    bucket.mkdir(parents=True)
    (bucket / "old-session.jsonl").write_text('{"role":"user"}\n', encoding="utf-8")
    (root / "kimi.json").write_text(json.dumps({"work_dirs": [{"path": cwd}]}), encoding="utf-8")
    result = read_store("KIMI_CODE", root)
    assert result["status"] == "AVAILABLE"
    assert result["sessions"][0]["session_id"] == "old-session"
    assert not (bucket / "old-session").exists()


def test_codex_archived_sessions_and_revision(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    (root / "sessions").mkdir(parents=True)
    archived = root / "archived_sessions"
    archived.mkdir()
    rollout = archived / "rollout-old.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": "archived-one", "cwd": "/work/old", "timestamp": "2026-09-29T10:00:00Z"}}) + "\n",
        encoding="utf-8")
    first = read_store("CODEX", root)
    assert [item["session_id"] for item in first["sessions"]] == ["archived-one"]
    assert read_store("CODEX", root, known_revision=first["revision"])["not_modified"] is True
    _codex_rollout(root, "new", "codex-new")
    changed = read_store("CODEX", root, known_revision=first["revision"])
    assert changed["not_modified"] is False
    assert {item["session_id"] for item in changed["sessions"]} == {"archived-one", "codex-new"}


def test_codex_update_uses_rollout_activity_not_creation(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    _codex_rollout(root, "activity", "codex-activity")
    rollout = root / "sessions" / "2026" / "09" / "29" / "rollout-activity.jsonl"
    os.utime(rollout, (1_800_000_000, 1_800_000_000))
    result = read_store("CODEX", root)
    assert result["sessions"][0]["updated_at"] == session_metadata._timestamp(1_800_000_000)


def test_codex_later_bad_page_and_incomplete_inventory(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / ".codex"
    _codex_rollout(root, "good", "session-good")
    folder = root / "sessions" / "2026" / "09" / "29"
    bad = folder / "rollout-bad.jsonl"
    bad.write_text("{unfinished\n", encoding="utf-8")
    os.utime(bad, (1, 1))
    first = read_store("CODEX", root, limit=1)
    assert first["status"] == "AVAILABLE" and first["next_offset"] == 1
    assert first["metadata_scope"] == "PAGE" and first["revision"]
    later = read_store("CODEX", root, limit=1, offset=1)
    assert later["status"] == "UNKNOWN"
    assert later["reason_code"] == "SESSION_METADATA_PARTIAL"
    assert later["revision"] is None
    monkeypatch.setattr(session_metadata, "MAX_ENTRIES", 2)
    limited = read_store("CODEX", root, limit=1, known_revision=first["revision"])
    assert limited["not_modified"] is False and limited["revision"] is None


def test_partial_jsonl_tail_has_no_revision(tmp_path: Path) -> None:
    root = tmp_path / ".kimi-code"
    directory = root / "sessions" / "bucket" / "session-one"
    directory.mkdir(parents=True)
    (root / "session_index.jsonl").write_text(json.dumps({
        "sessionId": "session-one", "sessionDir": str(directory), "workDir": "/work/kimi"}) +
        "\n" + '{"sessionId":"unfinished"', encoding="utf-8")
    result = read_store("KIMI_CODE", root)
    assert result["status"] == "DEGRADED"
    assert result["revision"] is None


def test_cloudcli_wal_change_invalidates_revision(tmp_path: Path) -> None:
    root = tmp_path / ".cloudcli"
    writer = _cloudcli_database(root)
    try:
        first = read_store("CLOUDCLI", root)
        assert first["revision"]
        writer.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                       ("app-one", "codex", "native-one", "One", "/work", 0, "2026-09-29", "2026-09-29"))
        writer.commit()
        changed = read_store("CLOUDCLI", root, known_revision=first["revision"])
        assert changed["not_modified"] is False
        assert changed["sessions"][0]["session_id"] == "app-one"
    finally:
        writer.close()


def test_process_candidates_require_verified_identity_and_whitelisted_paths(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "nt":
        pytest.skip("Windows process environment API")
    custom = tmp_path / "custom-kimi"
    home = tmp_path / "other-user"
    class FakeProcess:
        def __init__(self, pid: int) -> None:
            assert pid == 123
        def create_time(self) -> float:
            return 100.0
        def environ(self) -> dict[str, str]:
            return {"KIMI_SHARE_DIR": str(custom), "USERPROFILE": str(home),
                    "SECRET_TOKEN": "never-return-this"}
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=FakeProcess))
    stores = default_stores([{"provider": "KIMI_CODE", "pid": 123,
                              "create_time": "1970-01-01T00:01:40+00:00"}])
    assert any(item["path"] == str(custom) and item["locator_basis"] == "PROCESS_ENV"
               for item in stores)
    assert any(item["path"] == str(home / ".kimi") and item["locator_basis"] == "PROCESS_HOME"
               for item in stores)
    assert "never-return-this" not in json.dumps(stores)
    assert not any(item["locator_basis"].startswith("PROCESS") for item in
                   default_stores([{"provider": "KIMI_CODE", "pid": 123, "create_time": 101.0}]))
    for invalid in (100.000001, float("nan"), float("inf"), "1970-01-01T00:01:40"):
        assert not any(item["locator_basis"].startswith("PROCESS") for item in
                       default_stores([{"provider": "KIMI_CODE", "pid": 123,
                                        "create_time": invalid}]))


def test_process_candidate_coverage_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_metadata, "_process_environment", lambda hint: None)
    coverage = {}
    default_stores([{"provider": "CODEX", "pid": 1, "create_time": 1},
                    {"provider": "KIMI_CODE", "pid": 2, "create_time": 2}], coverage=coverage)
    assert coverage == {"requested": 2, "verified": 0, "unavailable": 2,
                        "not_inspected": 0, "limit_reason": None}
    limited = {}
    default_stores([{"provider": "CODEX", "pid": number, "create_time": 1}
                    for number in range(65)], coverage=limited)
    assert limited == {"requested": 65, "verified": 0, "unavailable": 64,
                       "not_inspected": 1, "limit_reason": "PROCESS_HINT_LIMIT"}


def test_kimi_revision_ignores_auxiliary_session_directories(tmp_path: Path) -> None:
    root = tmp_path / ".kimi-code"
    directory = root / "sessions" / "bucket" / "session-one"
    auxiliary = directory / "subagents"
    auxiliary.mkdir(parents=True)
    (auxiliary / "state.jsonl").write_text("auxiliary", encoding="utf-8")
    context = directory / "context.jsonl"
    context.write_text('{"role":"user"}\n', encoding="utf-8")
    (root / "session_index.jsonl").write_text(json.dumps({
        "sessionId": "session-one", "sessionDir": str(directory), "workDir": "/work/kimi"}) +
        "\n", encoding="utf-8")
    first = read_store("KIMI_CODE", root)
    assert first["status"] == "AVAILABLE" and first["revision"]
    (auxiliary / "state.jsonl").write_text("unrelated", encoding="utf-8")
    assert read_store("KIMI_CODE", root, known_revision=first["revision"])["not_modified"] is True
    context.write_text('{"role":"user","turn":2}\n', encoding="utf-8")
    changed = read_store("KIMI_CODE", root, known_revision=first["revision"])
    assert changed["status"] == "AVAILABLE" and changed["not_modified"] is False


def test_codex_archive_only_store(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    archived = root / "archived_sessions"
    archived.mkdir(parents=True)
    (archived / "rollout-only.jsonl").write_text(json.dumps({
        "type": "session_meta", "payload": {"id": "archive-only", "cwd": "/work"}}) + "\n",
        encoding="utf-8")
    result = read_store("CODEX", root)
    assert result["status"] == "AVAILABLE"
    assert result["sessions"][0]["session_id"] == "archive-only"
    assert result["revision"]


def test_codex_root_replacement_returns_actual_identity(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / ".codex"
    _codex_rollout(root, "original", "original")
    original_reader = session_metadata._codex
    def replace_after_page(*args: object) -> tuple:
        result = original_reader(*args)
        root.rename(tmp_path / "old-codex")
        _codex_rollout(root, "replacement", "replacement")
        return result
    monkeypatch.setattr(session_metadata, "_codex", replace_after_page)
    result = read_store("CODEX", root)
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == "SESSION_STORE_IDENTITY_CHANGED"
    assert result["sessions"] == [] and result["revision"] is None
    assert result["store_identity"] == inspect_store("CODEX", root)["identity"]


def test_codex_mutation_between_inventories_is_unstable(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / ".codex"
    _codex_rollout(root, "original", "original")
    rollout = root / "sessions" / "2026" / "09" / "29" / "rollout-original.jsonl"
    original_reader = session_metadata._codex
    def mutate_after_page(*args: object) -> tuple:
        result = original_reader(*args)
        with rollout.open("a", encoding="utf-8") as stream:
            stream.write('{"type":"message"}\n')
        return result
    monkeypatch.setattr(session_metadata, "_codex", mutate_after_page)
    result = read_store("CODEX", root)
    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "SESSION_STORE_UNSTABLE"
    assert result["revision"] is None and result["not_modified"] is False


def test_codex_unrelated_directory_entries_count_toward_limit(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / ".codex"
    folder = root / "sessions"
    folder.mkdir(parents=True)
    for number in range(5):
        (folder / f"unrelated-{number}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(session_metadata, "MAX_ENTRIES", 3)
    result = read_store("CODEX", root)
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == "SESSION_INDEX_LIMIT"
    assert result["revision"] is None


def test_codex_ten_thousand_rollouts_revision_skips_metadata_reads(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / ".codex"
    folder = root / "sessions" / "2026" / "09" / "29"
    folder.mkdir(parents=True)
    for number in range(10_000):
        (folder / f"rollout-{number:05}.jsonl").write_text(json.dumps({
            "type": "session_meta", "payload": {"id": f"session-{number:05}",
                                               "cwd": "/work"}}) + "\n", encoding="utf-8")
    opens = 0
    real_open = os.open
    def counted_open(path: object, *args: object, **kwargs: object) -> int:
        nonlocal opens
        if str(path).endswith(".jsonl"):
            opens += 1
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(session_metadata.os, "open", counted_open)
    started = time.perf_counter()
    first = read_store("CODEX", root, limit=20)
    cold_seconds = time.perf_counter() - started
    print(f"codex cold status={first['status']} reason={first['reason_code']} opens={opens} seconds={cold_seconds:.3f}")
    assert first["status"] == "AVAILABLE" and first["has_more"] is True
    assert len(first["sessions"]) == 20 and first["revision"]
    assert opens == 20
    assert first["metadata_scope"] == "PAGE"
    assert first["candidate_count"] == 10_000
    assert first["candidate_end"] == 20
    opens = 0
    started = time.perf_counter()
    hot = read_store("CODEX", root, limit=20, known_revision=first["revision"])
    hot_seconds = time.perf_counter() - started
    assert hot["not_modified"] is True and opens == 0
    print(f"codex 10000 cold={cold_seconds:.3f}s hot={hot_seconds:.3f}s metadata_opens=20/0")
