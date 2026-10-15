"""Persistent work commands through the real, session-protected Core API."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.db import StateDB
from agentguard.storage.migrations import MigrationEngine


def _client(root):
    client = TestClient(
        create_app(state_db_path=root / "state.db", config={"base_dir": str(root)})
    )
    client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
    return client


def _request(**values):
    return {"request_id": str(uuid4()), **values}


def _work(client):
    response = client.post(
        "/api/v1/works",
        json=_request(
            title="真实工作",
            owner="本地负责人",
            requirement="  保留缩进\n真实要求\n",
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()["work_id"]


def test_work_readers_remain_available_during_an_uncommitted_writer(tmp_path):
    client = _client(tmp_path)
    work_id = _work(client)
    root = '/api/v1/works/' + work_id
    task = client.post(root + '/tasks', json=_request(expected_revision=1, title='Task', body='Saved task body'))
    assert task.status_code == 201, task.text
    artifact = client.post(root + '/artifacts', json=_request(expected_revision=2,
        title='Artifact', body='Saved artifact body', requirement_version=1))
    assert artifact.status_code == 201, artifact.text
    with sqlite3.connect(tmp_path / 'state.db') as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE works SET title='Uncommitted title' WHERE id=?", (work_id,))
        try:
            paths = [root + '/tasks/' + task.json()['task_id'], root, root + '/requirements/1',
                     root + '/view', root + '/artifacts/' + artifact.json()['artifact_id'] + '/1',
                     root + '/agents', '/api/v1/works']
            for path in paths:
                response = client.get(path)
                assert response.status_code == 200, (path, response.text)
                if path == root:
                    assert response.json()['title'] != 'Uncommitted title'
        finally:
            writer.rollback()


def _record_agent(root, snapshot_id, agent_id=None, *, legacy=False):
    database = StateDB(root / "state.db")
    database.connect()
    try:
        observed_at = datetime.now(UTC)
        with database.transaction() as conn:
            EvidenceLedger().append(
                conn,
                EvidenceEvent(
                    schema_version=1,
                    event_id=f"{snapshot_id}-{agent_id or 'runtime'}-observation",
                    recorded_at=observed_at,
                    observed_at=observed_at,
                    event_family=EventFamily.DISCOVERY,
                    event_type=(
                        EventType.AGENT_DETECTED
                        if agent_id is not None
                        else EventType.RUNTIME_DETECTED
                    ),
                    source="local-process-metadata",
                    result="available",
                    execution_domain_id="windows-current",
                    supervision_session_id=None,
                    transaction_id=None,
                    checkpoint_id=None,
                    subject_ref=agent_id or "self-runtime-test",
                    evidence_refs=(f"{snapshot_id}-{agent_id or 'runtime'}-source",),
                    payload_safe={
                        "snapshot_id": snapshot_id,
                        "fact_type": (
                            "agent.metadata" if agent_id is not None else "runtime.metadata"
                        ),
                        **(
                            {
                                **({} if legacy else {"agent_id": agent_id}),
                                "agent_type": "CODEX",
                                "lifecycle": "RUNNING",
                            }
                            if agent_id is not None
                            else {}
                        ),
                        "value": (
                            {"agent_kind": "CODEX", "role": "EXECUTION_AGENT"}
                            if agent_id is not None
                            else {"runtime_kind": "SELF_RUNTIME"}
                        ),
                    },
                ),
            )
    finally:
        database.close()


def test_work_agent_link_uses_verified_current_identity_and_survives_refresh(tmp_path):
    client = _client(tmp_path)
    work_id = _work(client)
    root = f"/api/v1/works/{work_id}"
    agent_id = "external-agent-" + "a" * 24
    unknown = client.post(
        root + "/agents",
        json=_request(expected_revision=1, agent_id=agent_id),
    )
    assert unknown.status_code == 422
    assert unknown.json()["reason_code"] == "WORK_AGENT_NOT_CURRENT"

    _record_agent(tmp_path, "agent-snapshot", agent_id)
    candidate = client.get("/api/v1/agents").json()["items"][0]
    assert candidate["agent_id"] == agent_id
    assert candidate["association"] is None
    assert candidate["activity"] == "UNKNOWN"
    assert candidate["lifecycle"] == "RUNNING"
    assert client.get(root + "/agents").json()["items"] == []

    command = _request(expected_revision=1, agent_id=agent_id)
    attached = client.post(root + "/agents", json=command)
    assert attached.status_code == 201
    assert client.post(root + "/agents", json=command).json() == attached.json()
    assert attached.json()["revision"] == 2
    linked = client.get(root + "/agents").json()["items"][0]
    assert linked["association"] == "USER_LINKED"
    assert linked["presence"] == "CURRENT"
    assert linked["activity"] == "UNKNOWN"
    assert linked["evidence_refs"] == candidate["evidence_refs"]

    _record_agent(tmp_path, "empty-snapshot")
    assert client.get("/api/v1/agents").json()["items"] == []
    restarted = _client(tmp_path)
    stale = restarted.get(root + "/agents").json()["items"][0]
    assert stale["agent_id"] == agent_id
    assert stale["association"] == "USER_LINKED"
    assert stale["presence"] == "NOT_CURRENT"
    assert stale["lifecycle"] == "UNKNOWN"
    assert stale["workspace"] == {"status": "UNKNOWN", "binding_ref": None}
    assert stale["confidence"] == 0
    assert stale["reason_code"] == "WORK_AGENT_NOT_CURRENT"
    assert stale["activity"] == "UNKNOWN"
    assert stale["uncertainty"] is True
    assert restarted.get(root).json()["revision"] == 2

    detach = _request(expected_revision=2)
    result = restarted.post(root + f"/agents/{agent_id}/detach", json=detach)
    assert result.status_code == 200
    assert restarted.post(root + f"/agents/{agent_id}/detach", json=detach).json() == result.json()
    assert restarted.get(root + "/agents").json()["items"] == []
    with sqlite3.connect(tmp_path / "state.db") as conn:
        assert conn.execute(
            "SELECT 1 FROM evidence_ledger_events WHERE subject_ref=?", (agent_id,)
        ).fetchone() is not None


def test_legacy_agent_observation_remains_readable_but_cannot_be_linked(tmp_path):
    client = _client(tmp_path)
    work_id = _work(client)
    agent_id = "external-agent-" + "b" * 24
    _record_agent(tmp_path, "legacy-snapshot", agent_id, legacy=True)
    candidate = client.get("/api/v1/agents").json()["items"][0]
    assert candidate["detected_identity"] == "CODEX"
    assert candidate["agent_id"] is None
    response = client.post(
        f"/api/v1/works/{work_id}/agents",
        json=_request(expected_revision=1, agent_id=agent_id),
    )
    assert response.status_code == 422
    assert response.json()["reason_code"] == "WORK_AGENT_NOT_CURRENT"
    assert client.get(f"/api/v1/works/{work_id}/agents").json()["items"] == []


def test_agent_task_assignments_are_explicit_bounded_to_work_and_reversible(tmp_path):
    client = _client(tmp_path)
    work_id = _work(client)
    root = f"/api/v1/works/{work_id}"
    first_task = client.post(
        root + "/tasks",
        json=_request(expected_revision=1, title="First", assignee="CODEX"),
    ).json()["task_id"]
    second_task = client.post(
        root + "/tasks", json=_request(expected_revision=2, title="Second")
    ).json()["task_id"]
    other_work = _work(client)
    foreign_task = client.post(
        f"/api/v1/works/{other_work}/tasks",
        json=_request(expected_revision=1, title="Foreign"),
    ).json()["task_id"]
    assert client.get(root + "/agents").json()["items"] == []

    first_agent = "external-agent-" + "c" * 24
    second_agent = "external-agent-" + "d" * 24
    _record_agent(tmp_path, "multi-agent-snapshot", first_agent)
    _record_agent(tmp_path, "multi-agent-snapshot", second_agent)
    assert len(client.get("/api/v1/agents").json()["items"]) == 2
    assert client.post(
        root + "/agents", json=_request(expected_revision=3, agent_id=first_agent)
    ).json()["revision"] == 4
    assert client.post(
        root + "/agents", json=_request(expected_revision=4, agent_id=second_agent)
    ).json()["revision"] == 5

    first_path = root + f"/agents/{first_agent}/tasks"
    second_path = root + f"/agents/{second_agent}/tasks"
    assignment = _request(
        expected_revision=5, task_ids=[first_task, second_task]
    )
    first = client.post(first_path, json=assignment)
    assert first.status_code == 200
    assert first.json()["revision"] == 6
    assert client.post(first_path, json=assignment).json() == first.json()
    assert client.get(root).json()["revision"] == 6
    assert client.post(
        second_path,
        json=_request(expected_revision=6, task_ids=[first_task]),
    ).json()["revision"] == 7
    linked = {
        item["agent_id"]: item for item in client.get(root + "/agents").json()["items"]
    }
    assert set(linked[first_agent]["task_ids"]) == {first_task, second_task}
    assert linked[second_agent]["task_ids"] == [first_task]
    assert all(item["activity"] == "UNKNOWN" for item in linked.values())

    foreign = client.post(
        first_path, json=_request(expected_revision=7, task_ids=[foreign_task])
    )
    assert foreign.status_code == 422
    assert foreign.json()["reason_code"] == "WORK_AGENT_TASK_NOT_IN_WORK"
    duplicate = client.post(
        first_path,
        json=_request(expected_revision=7, task_ids=[first_task, first_task]),
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["reason_code"] == "WORK_AGENT_TASK_DUPLICATE"
    stale = client.post(
        first_path, json=_request(expected_revision=6, task_ids=[])
    )
    assert stale.status_code == 409
    assert stale.json()["reason_code"] == "WORK_REVISION_CONFLICT"
    unlinked_work = client.post(
        f"/api/v1/works/{other_work}/agents/{first_agent}/tasks",
        json=_request(expected_revision=2, task_ids=[foreign_task]),
    )
    assert unlinked_work.status_code == 404
    assert unlinked_work.json()["reason_code"] == "WORK_AGENT_LINK_NOT_FOUND"
    assert client.get(root).json()["revision"] == 7

    detached = client.post(
        root + f"/agents/{first_agent}/detach",
        json=_request(expected_revision=7),
    )
    assert detached.json()["revision"] == 8
    remaining = client.get(root + "/agents").json()["items"]
    assert len(remaining) == 1
    assert remaining[0]["task_ids"] == [first_task]
    assert client.get(root + f"/tasks/{first_task}").status_code == 200
    assert client.get(root + f"/tasks/{second_task}").status_code == 200
    with sqlite3.connect(tmp_path / "state.db") as conn:
        assert conn.execute(
            "SELECT count(*) FROM work_agent_tasks WHERE work_id=? AND agent_id=?",
            (work_id, first_agent),
        ).fetchone()[0] == 0

    cleared = client.post(
        second_path, json=_request(expected_revision=8, task_ids=[])
    )
    assert cleared.status_code == 200
    assert cleared.json()["task_ids"] == []
    assert client.get(root + "/agents").json()["items"][0]["task_ids"] == []


def test_work_content_versions_and_assignment_survive_restart(tmp_path):
    client = _client(tmp_path)
    work_id = _work(client)
    root = f"/api/v1/works/{work_id}"
    task = client.post(
        root + "/tasks",
        json=_request(
            expected_revision=1,
            title="实现",
            body="  真正的详细正文\n",
            assignee="执行者",
        ),
    )
    assert task.status_code == 201
    task_id = task.json()["task_id"]
    result = client.post(
        root + "/requirements", json=_request(expected_revision=2, body="新要求")
    )
    assert result.status_code == 201
    restarted = _client(tmp_path)
    detail = restarted.get(root).json()
    assert detail["revision"] == 3
    assert detail["requirement_version"] == 2
    assert detail["requirement"] == "新要求"
    assert (
        restarted.get(root + "/requirements/1").json()["body"]
        == "  保留缩进\n真实要求\n"
    )
    assert (
        restarted.get(root + f"/tasks/{task_id}").json()["body"] == "  真正的详细正文\n"
    )
    assert detail["tasks"][0]["assignee"] == "执行者"
    assert restarted.get("/api/v1/works?limit=1").json()["items"][0]["id"] == work_id
    with (
        sqlite3.connect(tmp_path / "state.db") as conn,
        pytest.raises(
            sqlite3.IntegrityError,
            match="IMMUTABLE",
        ),
    ):
        conn.execute("UPDATE work_requirements SET body='overwrite'")


def test_retries_are_idempotent_but_reused_requests_and_stale_edits_conflict(tmp_path):
    client = _client(tmp_path)
    command = _request(title="工作", owner="自己", requirement="要求")
    first = client.post("/api/v1/works", json=command)
    assert client.post("/api/v1/works", json=command).json() == first.json()
    changed = client.post("/api/v1/works", json={**command, "title": "不同请求"})
    assert changed.status_code == 409
    work_id = first.json()["work_id"]
    root = f"/api/v1/works/{work_id}"
    revision = _request(expected_revision=1, body="第二版")
    response = client.post(root + "/requirements", json=revision)
    assert response.status_code == 201
    assert client.post(root + "/requirements", json=revision).json() == response.json()
    conflict = client.patch(
        root, json=_request(expected_revision=1, title="旧窗口", owner="自己")
    )
    assert conflict.status_code == 409
    assert conflict.json()["reason_code"] == "WORK_REVISION_CONFLICT"
    assert client.get(root).json()["title"] == "工作"
    assert client.get(root).json()["requirement_version"] == 2


def test_cycles_foreign_dependencies_and_missing_tasks_roll_back(tmp_path):
    client = _client(tmp_path)
    root = f"/api/v1/works/{_work(client)}"
    a = client.post(
        root + "/tasks", json=_request(expected_revision=1, title="A")
    ).json()["task_id"]
    b = client.post(
        root + "/tasks", json=_request(expected_revision=2, title="B", depends_on=[a])
    ).json()["task_id"]
    cycle = client.put(
        root + f"/tasks/{a}",
        json=_request(expected_revision=3, title="A", depends_on=[b]),
    )
    assert cycle.status_code == 422
    assert cycle.json()["reason_code"] == "WORK_DEPENDENCY_CYCLE"
    foreign_root = f"/api/v1/works/{_work(client)}"
    foreign = client.post(
        foreign_root + "/tasks", json=_request(expected_revision=1, title="X")
    ).json()["task_id"]
    result = client.put(
        root + f"/tasks/{a}",
        json=_request(expected_revision=3, title="A", depends_on=[foreign]),
    )
    assert result.status_code == 422
    assert result.json()["reason_code"] == "WORK_DEPENDENCY_NOT_IN_WORK"
    assert client.get(root).json()["revision"] == 3
    assert client.get(root + f"/tasks/{a}").json()["depends_on"] == []
    assert client.get(root + f"/tasks/{foreign}").status_code == 404


def test_work_routes_require_session_and_reject_authority_fields(tmp_path):
    client = _client(tmp_path)
    assert (
        client.get("/api/v1/works", headers={"X-Session-Token": "wrong"}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/works",
            json=_request(
                title="工作",
                owner="我",
                requirement="要求",
                state="accepted",
            ),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/works",
            json=_request(
                title="   ",
                owner="我",
                requirement="要求",
            ),
        ).status_code
        == 422
    )
    assert client.get("/api/v1/works?limit=10000").status_code == 422


def test_competing_windows_cannot_silently_overwrite(tmp_path):
    client = _client(tmp_path)
    root = f"/api/v1/works/{_work(client)}"

    def edit(title):
        return client.patch(
            root, json=_request(expected_revision=1, title=title, owner="我")
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(edit, ("窗口 A", "窗口 B")))
    assert sorted(result.status_code for result in results) == [200, 409]
    assert client.get(root).json()["revision"] == 2


def test_known_credential_content_is_rejected_before_persistence(tmp_path):
    client = _client(tmp_path)
    secret_example = "api_key=sk-test-0123456789abcdefghij"
    result = client.post(
        "/api/v1/works",
        json=_request(
            title="工作",
            owner="我",
            requirement=secret_example,
        ),
    )
    assert result.status_code == 422
    assert result.json()["reason_code"] == "WORK_CONTENT_SENSITIVE"
    assert secret_example not in result.text
    assert client.get("/api/v1/works").json()["items"] == []


def test_upgrade_v9_retains_guard_checkpoint_and_adds_work_tables(tmp_path):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,
            name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)""")
        engine = MigrationEngine(path)
        assert engine.migrate(conn, target=9)["errors"] == []
        conn.execute("""INSERT INTO checkpoints(label, created_at, snapshot_path, hash_sha256, file_count)
            VALUES ('keep-me', '2026-09-27', 'snapshots/example', 'hash', 1)""")
    database = StateDB(path)
    database.connect()
    try:
        with database.transaction() as conn:
            assert (
                conn.execute("SELECT label FROM checkpoints").fetchone()[0] == "keep-me"
            )
            assert engine.current_version(conn) == 19
            assert conn.execute("SELECT count(*) FROM works").fetchone()[0] == 0
    finally:
        database.close()


def test_failed_receipt_write_rolls_back_work_and_requirement(tmp_path):
    from agentguard.work.models import CreateWork
    from agentguard.work.service import WorkService

    database = StateDB(tmp_path / "state.db")
    database.connect()
    try:
        with database.transaction() as conn:
            conn.execute("""CREATE TRIGGER reject_receipt BEFORE INSERT ON work_requests
                BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END""")
        command = CreateWork(**_request(title="工作", owner="自己", requirement="要求"))
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            WorkService(database).create(command)
        with database.transaction() as conn:
            assert conn.execute("SELECT count(*) FROM works").fetchone()[0] == 0
            assert (
                conn.execute("SELECT count(*) FROM work_requirements").fetchone()[0]
                == 0
            )
            conn.execute("DROP TRIGGER reject_receipt")
        assert WorkService(database).create(command)["revision"] == 1
    finally:
        database.close()


def test_artifact_versions_and_saved_reading_position_survive_reopen(tmp_path):
    client = _client(tmp_path)
    work_id = _work(client)
    root = f"/api/v1/works/{work_id}"
    command = _request(
        expected_revision=1,
        requirement_version=1,
        title="成果",
        body="真实交付\n  保留正文",
    )
    response = client.post(root + "/artifacts", json=command)
    assert response.status_code == 201
    first = response.json()
    assert client.post(root + "/artifacts", json=command).json() == first
    second = client.post(
        root + "/artifacts",
        json=_request(
            expected_revision=2,
            artifact_id=first["artifact_id"],
            requirement_version=1,
            title="成果修订",
            body="第二版正文",
        ),
    )
    assert second.status_code == 201
    assert second.json()["version"] == 2
    state = {
        "schema_version": 1,
        "viewport": {"x": -210, "y": 42, "zoom": 0.75},
        "cards": [
            {
                "kind": "requirement",
                "ref_id": work_id,
                "version": 1,
                "x": 0,
                "y": 0,
                "width": 400,
                "height": 430,
                "scroll": 80,
            },
            {
                "kind": "artifact",
                "ref_id": first["artifact_id"],
                "version": 1,
                "x": 470,
                "y": 100,
                "width": 520,
                "height": 500,
                "scroll": 140,
            },
        ],
    }
    save = _request(expected_revision=0, state=state)
    assert client.post(root + "/view", json=save).json()["revision"] == 1
    assert client.post(root + "/view", json=save).json()["revision"] == 1
    reopened = _client(tmp_path)
    assert reopened.get(root + "/view").json()["state"] == state
    assert (
        reopened.get(root + f"/artifacts/{first['artifact_id']}/1").json()["body"]
        == command["body"]
    )
    assert (
        reopened.get(root + f"/artifacts/{first['artifact_id']}/2").json()["body"]
        == "第二版正文"
    )
    assert (
        reopened.get(root).json()["revision"] == 3
    )  # Saving layout does not mutate work facts.
    assert (
        client.post(
            root + "/view", json=_request(expected_revision=0, state=state)
        ).status_code
        == 409
    )
    assert (
        client.post(
            root + "/view",
            json=_request(expected_revision=1, state={**state, "cards": []}),
        ).status_code
        == 200
    )
    assert (
        reopened.get(root + f"/artifacts/{first['artifact_id']}/1").status_code == 200
    )


def test_view_and_artifact_cannot_reference_another_work(tmp_path):
    client = _client(tmp_path)
    a, b = _work(client), _work(client)
    artifact = client.post(
        f"/api/v1/works/{a}/artifacts",
        json=_request(
            expected_revision=1,
            requirement_version=1,
            title="A",
            body="A 内容",
        ),
    ).json()["artifact_id"]
    root = f"/api/v1/works/{b}"
    assert client.get(root + f"/artifacts/{artifact}/1").status_code == 404
    assert (
        client.post(
            root + "/artifacts",
            json=_request(
                expected_revision=1,
                artifact_id=artifact,
                requirement_version=1,
                title="B",
                body="B 内容",
            ),
        ).status_code
        == 404
    )
    state = {
        "cards": [
            {"kind": "artifact", "ref_id": artifact, "version": 1, "x": 0, "y": 0}
        ]
    }
    assert (
        client.post(
            root + "/view", json=_request(expected_revision=0, state=state)
        ).status_code
        == 422
    )
    assert client.get(root + "/view").json()["revision"] == 0
    assert (
        client.post(
            root + "/view",
            json=_request(expected_revision=0, state={"viewport": {"zoom": 0}}),
        ).status_code
        == 422
    )


def test_relation_space_and_board_survive_restart_without_changing_work(tmp_path):
    client = _client(tmp_path)
    work_id = _work(client)
    root = f"/api/v1/works/{work_id}"
    task = client.post(
        root + "/tasks", json=_request(expected_revision=1, title="阅读成果")
    ).json()["task_id"]
    node = {"kind": "task", "ref_id": task, "version": None, "x": 650, "y": 180}
    state = {
        "surface": "board",
        "viewport": {"x": -320, "y": 60, "zoom": 0.8},
        "cards": [{**node, "width": 480, "height": 500, "scroll": 120}],
        "space": {
            "viewport": {"x": -150, "y": 100, "zoom": 0.65},
            "nodes": [node],
        },
    }
    request = _request(expected_revision=0, state=state)
    response = client.post(root + "/view", json=request)
    assert response.status_code == 200, response.text
    reopened = _client(tmp_path)
    saved = reopened.get(root + "/view").json()["state"]
    assert saved == {"schema_version": 1, **state}
    assert reopened.post(root + "/view", json=request).json() == response.json()
    assert reopened.get(root).json()["revision"] == 2
    assert reopened.get(root + f"/tasks/{task}").json()["depends_on"] == []
    assert (
        reopened.post(
            root + "/view", json=_request(expected_revision=0, state=state)
        ).status_code
        == 409
    )
    assert reopened.get(root + "/view").json()["state"] == saved


def test_relation_space_rejects_foreign_duplicate_and_invalid_positions(tmp_path):
    client = _client(tmp_path)
    a, b = _work(client), _work(client)
    root = f"/api/v1/works/{a}"
    node = {"kind": "requirement", "ref_id": a, "version": 1, "x": 0, "y": 0}
    invalid_nodes = [
        [{**node, "ref_id": b}],
        [node, node],
        [{**node, "version": 99}],
        [{**node, "x": 100001}],
    ]
    for nodes in invalid_nodes:
        response = client.post(
            root + "/view",
            json=_request(expected_revision=0, state={"space": {"nodes": nodes}}),
        )
        assert response.status_code == 422, response.text
    assert client.get(root + "/view").json()["revision"] == 0


def test_old_view_receipt_digest_stays_compatible():
    from agentguard.work.models import SaveView

    # A retry queued by the earlier UI must keep its exact canonical payload.
    command = SaveView(**_request(expected_revision=0, state={"cards": []}))
    assert command.model_dump(mode="json", exclude={"request_id"}) == {
        "expected_revision": 0,
        "state": {
            "schema_version": 1,
            "viewport": {"x": 24, "y": 24, "zoom": 1},
            "cards": [],
        },
    }
