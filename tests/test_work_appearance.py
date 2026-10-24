"""Independent persisted Agent color commands, never execution authority."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.test_work_api import _client, _record_agent, _request, _work

AGENT = "agent-color-observed"


def setup_work(root):
    client = _client(root)
    work = _work(client)
    _record_agent(root, "color-snapshot", AGENT)
    response = client.post(f"/api/v1/works/{work}/agents", json=_request(expected_revision=1, agent_id=AGENT))
    assert response.status_code == 201, response.text
    return client, work


def appearance_path(work):
    return f"/api/v1/works/{work}/agents/{AGENT}/appearance"


def appearances(client, work):
    response = client.get(f"/api/v1/works/{work}/agent-appearances")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_saved_color_survives_restart_and_old_view_save_without_work_revision_change(tmp_path):
    client, work = setup_work(tmp_path)
    assert appearances(client, work) == [{"agent_id": AGENT, "color": None, "revision": 0, "updated_at": None}]
    before = client.get(f"/api/v1/works/{work}").json()["revision"]
    command = _request(expected_revision=0, color="#b9a6f4")
    saved = client.post(appearance_path(work), json=command)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert client.post(appearance_path(work), json=command).json() == saved.json()
    assert client.get(f"/api/v1/works/{work}").json()["revision"] == before
    view = {"schema_version": 1, "viewport": {"x": 0, "y": 0, "zoom": 1}, "cards": []}
    old = _request(expected_revision=0, state=view)
    first_view = client.post(f"/api/v1/works/{work}/view", json=old)
    assert first_view.status_code == 200
    assert client.post(f"/api/v1/works/{work}/view", json=old).json() == first_view.json()
    client.close()
    restarted = _client(tmp_path)
    assert appearances(restarted, work)[0]["color"] == "#b9a6f4"
    assert appearances(restarted, work)[0]["revision"] == 1


def test_reset_retains_revision_and_receipt_and_detach_retains_preference(tmp_path):
    client, work = setup_work(tmp_path)
    path = appearance_path(work)
    assert client.post(path, json=_request(expected_revision=0, color="#7cc6bc")).status_code == 200
    command = _request(expected_revision=1, color=None)
    reset = client.post(path, json=command)
    assert reset.json()["color"] is None and reset.json()["revision"] == 2
    detach = client.post(f"/api/v1/works/{work}/agents/{AGENT}/detach", json=_request(expected_revision=2))
    assert detach.status_code == 200
    assert appearances(client, work) == []
    rejected = client.post(path, json=_request(expected_revision=2, color="#7cc6bc"))
    assert rejected.status_code == 404
    relink = client.post(f"/api/v1/works/{work}/agents", json=_request(expected_revision=3, agent_id=AGENT))
    assert relink.status_code == 201
    assert appearances(client, work)[0]["revision"] == 2
    assert client.post(path, json=command).json() == reset.json()


@pytest.mark.parametrize("fields", [{"color": "#FFFFFF"}, {"color": "red"}, {"color": "url(example)"},
    {"expected_revision": True}, {"expected_revision": -1}, {"color": "#7cc6bc", "execute": True}])
def test_color_command_strict_validation(tmp_path, fields):
    client, work = setup_work(tmp_path)
    command = _request(expected_revision=0, color="#7cc6bc")
    response = client.post(appearance_path(work), json={**command, **fields})
    assert response.status_code == 422
    assert appearances(client, work)[0]["revision"] == 0


def test_request_reuse_and_revision_conflict_do_not_override(tmp_path):
    client, work = setup_work(tmp_path)
    command = _request(expected_revision=0, color="#7cc6bc")
    assert client.post(appearance_path(work), json=command).status_code == 200
    reused = client.post(appearance_path(work), json={**command, "color": "#b9a6f4"})
    assert reused.status_code == 409 and reused.json()["reason_code"] == "WORK_REQUEST_REUSED"
    conflict = client.post(appearance_path(work), json=_request(expected_revision=0, color="#b9a6f4"))
    assert conflict.status_code == 409 and conflict.json()["reason_code"] == "WORK_APPEARANCE_CONFLICT"
    assert appearances(client, work)[0]["color"] == "#7cc6bc"


def test_concurrent_color_choices_have_one_winner(tmp_path):
    client, work = setup_work(tmp_path)
    def choose(color):
        other = _client(tmp_path)
        try:
            return other.post(appearance_path(work), json=_request(expected_revision=0, color=color)).status_code
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(choose, ["#7cc6bc", "#b9a6f4"]))
    assert sorted(results) == [200, 409]
    assert appearances(client, work)[0]["revision"] == 1


def test_unlinked_cross_work_and_unauthenticated_requests_rejected(tmp_path):
    client, work = setup_work(tmp_path)
    other = _work(client)
    response = client.post(appearance_path(other), json=_request(expected_revision=0, color="#7cc6bc"))
    assert response.status_code == 404
    assert client.get(f"/api/v1/works/{uuid4()}/agent-appearances").status_code == 404
    client.headers.pop("X-Session-Token")
    assert client.get(f"/api/v1/works/{work}/agent-appearances").status_code == 401
    assert client.post(appearance_path(work), json=_request(expected_revision=0, color="#7cc6bc")).status_code == 401


@pytest.mark.parametrize("color", ["#7cc6bc", "#b9a6f4", "#8fb8e8", "#e3a5bf", "#d9c08a", "#a8c98f"])
def test_each_palette_choice_is_saved_without_ledger_events(tmp_path, color):
    import sqlite3
    client, work = setup_work(tmp_path)
    with sqlite3.connect(tmp_path / "state.db") as connection:
        before = connection.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone()[0]
    response = client.post(appearance_path(work), json=_request(expected_revision=0, color=color))
    assert response.status_code == 200, response.text
    assert appearances(client, work)[0]["color"] == color
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone()[0] == before
