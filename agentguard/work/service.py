"""Work mutations and retry receipts commit together in the existing state DB."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.core.sanitizer import contains_sensitive_data
from agentguard.evidence.ledger import verify_ledger
from agentguard.storage.db import StateDB

from .models import (
    AssignAgentTasks,
    AttachAgent,
    CreateWork,
    DetachAgent,
    EditWork,
    ReviseRequirement,
    SaveArtifact,
    SaveView,
    WriteTask,
)


class WorkError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


class WorkService:
    def __init__(self, database: StateDB):
        self.db = database

    @staticmethod
    def _command_digest(operation, target, command):
        payload = command.model_dump(mode="json", exclude={"request_id"})
        if any(
            contains_sensitive_data(value)
            for value in payload.values()
            if isinstance(value, str)
        ):
            raise WorkError("WORK_CONTENT_SENSITIVE", 422)
        return hashlib.sha256(
            json.dumps(
                [operation, target, payload],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

    def receipt(self, operation, target, command):
        digest = self._command_digest(operation, target, command)
        previous = self.db._conn.execute(
            "SELECT request_digest,response_json FROM work_requests WHERE request_id=?",
            (str(command.request_id),),
        ).fetchone()
        if previous:
            if previous[0] != digest:
                raise WorkError("WORK_REQUEST_REUSED")
            return json.loads(previous[1])
        return None

    def _command(self, operation, target, command, apply):
        digest = self._command_digest(operation, target, command)
        with self.db.transaction() as conn:
            previous = conn.execute(
                "SELECT request_digest, response_json FROM work_requests WHERE request_id=?",
                (str(command.request_id),),
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise WorkError("WORK_REQUEST_REUSED")
                return json.loads(previous[1])
            now = datetime.now(UTC).isoformat()
            result = apply(conn, now)
            conn.execute(
                "INSERT INTO work_requests VALUES (?, ?, ?, ?)",
                (
                    str(command.request_id),
                    digest,
                    json.dumps(result),
                    now,
                ),
            )
            return result

    @staticmethod
    def _work(conn, work_id, expected=None):
        cursor = conn.execute("SELECT * FROM works WHERE id=?", (work_id,))
        row = cursor.fetchone()
        if row is None:
            raise WorkError("WORK_NOT_FOUND", 404)
        work = dict(zip((col[0] for col in cursor.description), row))
        if expected is not None and work["revision"] != expected:
            raise WorkError("WORK_REVISION_CONFLICT")
        return work

    @staticmethod
    def _bump(conn, work_id, now):
        conn.execute(
            "UPDATE works SET revision=revision+1, updated_at=? WHERE id=?",
            (now, work_id),
        )
        revision = conn.execute(
            "SELECT revision FROM works WHERE id=?", (work_id,)
        ).fetchone()[0]
        return {"work_id": work_id, "revision": revision}

    def _current_agents(self):
        # The projection verifies the evidence ledger and selects only the latest discovery.
        return R4ReadProjectionService(self.db, None).agents()

    def agents(self, work_id: str, *, view: str = "observed"):
        with self.db.transaction(immediate=False) as conn:
            self._work(conn, work_id)
            # No relationship exists to enrich. Do not rescan discovery merely
            # to establish an empty local link set; UNKNOWN makes no discovery claim.
            if conn.execute("SELECT 1 FROM work_agent_links WHERE work_id=? LIMIT 1", (work_id,)).fetchone() is None:
                return {"items": [], "discovery_status": "UNKNOWN"}
            projection = {"items": [], "status": "UNKNOWN"} if view == "links" else self._current_agents()
            current = {
                item["agent_id"]: item
                for item in projection["items"]
                if item.get("agent_id") is not None
            }
            task_ids: dict[str, list[str]] = {}
            for agent_id, task_id in conn.execute(
                """SELECT agent_id, task_id FROM work_agent_tasks
                   WHERE work_id=? ORDER BY task_id""",
                (work_id,),
            ):
                task_ids.setdefault(agent_id, []).append(task_id)
            items = []
            for row in conn.execute(
                """SELECT agent_id, detected_identity, role, execution_domain_id,
                          observed_at, evidence_ref, linked_at
                   FROM work_agent_links WHERE work_id=? ORDER BY linked_at, agent_id""",
                (work_id,),
            ):
                agent_id = row[0]
                if agent_id in current:
                    item = {**current[agent_id], "association": "USER_LINKED"}
                else:
                    item = {
                        "agent_id": agent_id,
                        "detected_identity": row[1],
                        "role": row[2],
                        "execution_domain_id": row[3],
                        "lifecycle": "UNKNOWN",
                        "workspace": {"status": "UNKNOWN", "binding_ref": None},
                        "confidence": 0,
                        "reason_code": "WORK_AGENT_OBSERVATION_PENDING" if view == "links" else "WORK_AGENT_NOT_CURRENT",
                        "observed_at": row[4],
                        "evidence_refs": [row[5]],
                        "uncertainty": True,
                        "association": "USER_LINKED",
                        "activity": "UNKNOWN",
                        "presence": "UNKNOWN" if view == "links" else "NOT_CURRENT",
                    }
                item["linked_at"] = row[6]
                item["task_ids"] = task_ids.get(agent_id, [])
                items.append(item)
            return {"items": items, "discovery_status": projection["status"]}

    def attach_agent(self, work_id: str, command: AttachAgent):
        def apply(conn, now):
            self._work(conn, work_id, command.expected_revision)
            # Attaching grants a durable relationship: its write transaction must
            # run the full validator even when read-only projections are warm.
            if verify_ledger(conn):
                raise WorkError("WORK_AGENT_EVIDENCE_INVALID", 409)
            projection = self._current_agents()
            if projection["reason_code"] == "R4_LEDGER_INVALID":
                raise WorkError("WORK_AGENT_EVIDENCE_INVALID", 409)
            agent = next(
                (
                    item for item in projection["items"]
                    if item.get("agent_id") == command.agent_id
                    and item.get("lifecycle") != "UNKNOWN"
                    and item.get("presence") == "CURRENT"
                ),
                None,
            )
            if agent is None:
                raise WorkError("WORK_AGENT_NOT_CURRENT", 422)
            if conn.execute(
                "SELECT 1 FROM work_agent_links WHERE work_id=? AND agent_id=?",
                (work_id, command.agent_id),
            ).fetchone():
                raise WorkError("WORK_AGENT_ALREADY_LINKED")
            conn.execute(
                """INSERT INTO work_agent_links VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    work_id,
                    command.agent_id,
                    agent["detected_identity"],
                    agent["role"],
                    agent["execution_domain_id"],
                    agent["observed_at"],
                    agent["evidence_refs"][0],
                    now,
                ),
            )
            return self._bump(conn, work_id, now)

        return self._command("attach_agent", work_id, command, apply)

    def detach_agent(self, work_id: str, agent_id: str, command: DetachAgent):
        def apply(conn, now):
            self._work(conn, work_id, command.expected_revision)
            conn.execute(
                "DELETE FROM work_agent_tasks WHERE work_id=? AND agent_id=?",
                (work_id, agent_id),
            )
            deleted = conn.execute(
                "DELETE FROM work_agent_links WHERE work_id=? AND agent_id=?",
                (work_id, agent_id),
            )
            if deleted.rowcount != 1:
                raise WorkError("WORK_AGENT_LINK_NOT_FOUND", 404)
            return self._bump(conn, work_id, now)

        return self._command("detach_agent", [work_id, agent_id], command, apply)

    def assign_agent_tasks(
        self, work_id: str, agent_id: str, command: AssignAgentTasks
    ):
        def apply(conn, now):
            self._work(conn, work_id, command.expected_revision)
            if not conn.execute(
                "SELECT 1 FROM work_agent_links WHERE work_id=? AND agent_id=?",
                (work_id, agent_id),
            ).fetchone():
                raise WorkError("WORK_AGENT_LINK_NOT_FOUND", 404)
            task_ids = [str(task_id) for task_id in command.task_ids]
            if len(set(task_ids)) != len(task_ids):
                raise WorkError("WORK_AGENT_TASK_DUPLICATE", 422)
            owned = {
                row[0]
                for row in conn.execute(
                    "SELECT id FROM work_tasks WHERE work_id=?", (work_id,)
                )
            }
            if any(task_id not in owned for task_id in task_ids):
                raise WorkError("WORK_AGENT_TASK_NOT_IN_WORK", 422)
            conn.execute(
                "DELETE FROM work_agent_tasks WHERE work_id=? AND agent_id=?",
                (work_id, agent_id),
            )
            conn.executemany(
                "INSERT INTO work_agent_tasks VALUES (?, ?, ?)",
                ((work_id, agent_id, task_id) for task_id in task_ids),
            )
            return {**self._bump(conn, work_id, now), "task_ids": task_ids}

        return self._command("assign_agent_tasks", [work_id, agent_id], command, apply)

    def create(self, command: CreateWork):
        def apply(conn, now):
            work_id = str(uuid4())
            conn.execute(
                "INSERT INTO works VALUES (?, ?, ?, 1, 1, ?, ?)",
                (
                    work_id,
                    command.title,
                    command.owner,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO work_requirements VALUES (?, 1, ?, ?)",
                (
                    work_id,
                    command.requirement,
                    now,
                ),
            )
            return {"work_id": work_id, "revision": 1}

        return self._command("create", None, command, apply)

    def edit(self, work_id: str, command: EditWork):
        def apply(conn, now):
            self._work(conn, work_id, command.expected_revision)
            conn.execute(
                "UPDATE works SET title=?, owner=? WHERE id=?",
                (
                    command.title,
                    command.owner,
                    work_id,
                ),
            )
            return self._bump(conn, work_id, now)

        return self._command("edit", work_id, command, apply)

    def revise(self, work_id: str, command: ReviseRequirement):
        def apply(conn, now):
            work = self._work(conn, work_id, command.expected_revision)
            version = work["requirement_version"] + 1
            conn.execute(
                "INSERT INTO work_requirements VALUES (?, ?, ?, ?)",
                (
                    work_id,
                    version,
                    command.body,
                    now,
                ),
            )
            conn.execute(
                "UPDATE works SET requirement_version=? WHERE id=?", (version, work_id)
            )
            return {**self._bump(conn, work_id, now), "requirement_version": version}

        return self._command("requirement", work_id, command, apply)

    def write_task(self, work_id: str, command: WriteTask, task_id: str | None = None):
        def apply(conn, now):
            self._work(conn, work_id, command.expected_revision)
            graph = {
                row[0]: json.loads(row[1])
                for row in conn.execute(
                    "SELECT id, dependencies_json FROM work_tasks WHERE work_id=?",
                    (work_id,),
                )
            }
            if task_id is not None and task_id not in graph:
                raise WorkError("WORK_TASK_NOT_FOUND", 404)
            if task_id is None and len(graph) >= 500:
                raise WorkError("WORK_TASK_LIMIT", 422)
            current_id = task_id or str(uuid4())
            dependencies = [str(value) for value in command.depends_on]
            if len(set(dependencies)) != len(dependencies):
                raise WorkError("WORK_DEPENDENCY_DUPLICATE", 422)
            if current_id in dependencies:
                raise WorkError("WORK_DEPENDENCY_CYCLE", 422)
            if any(dependency not in graph for dependency in dependencies):
                raise WorkError("WORK_DEPENDENCY_NOT_IN_WORK", 422)
            graph[current_id] = dependencies
            # Iterative traversal also handles long chains without recursion limits.
            pending, visited = list(dependencies), set()
            while pending:
                node = pending.pop()
                if node == current_id:
                    raise WorkError("WORK_DEPENDENCY_CYCLE", 422)
                if node not in visited:
                    visited.add(node)
                    pending.extend(graph[node])
            if task_id is None:
                conn.execute(
                    "INSERT INTO work_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        current_id,
                        work_id,
                        command.title,
                        command.body,
                        command.assignee,
                        json.dumps(dependencies),
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """UPDATE work_tasks SET title=?, body=?, assignee=?,
                    dependencies_json=?, updated_at=? WHERE id=? AND work_id=?""",
                    (
                        command.title,
                        command.body,
                        command.assignee,
                        json.dumps(dependencies),
                        now,
                        current_id,
                        work_id,
                    ),
                )
            return {**self._bump(conn, work_id, now), "task_id": current_id}

        return self._command("task", [work_id, task_id], command, apply)

    def list(self, limit=50, offset=0):
        with self.db.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """SELECT id, title, owner, revision, requirement_version,
                created_at, updated_at FROM works ORDER BY created_at DESC, id LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            names = [col[0] for col in cursor.description]
            return [dict(zip(names, row)) for row in cursor]

    def get(self, work_id: str):
        with self.db.transaction(immediate=False) as conn:
            work = self._work(conn, work_id)
            work["requirement"] = conn.execute(
                "SELECT body FROM work_requirements WHERE work_id=? AND version=?",
                (work_id, work["requirement_version"]),
            ).fetchone()[0]
            # Task bodies have a separate detail endpoint to keep this projection bounded.
            cursor = conn.execute(
                """SELECT id, title, assignee, dependencies_json
                FROM work_tasks WHERE work_id=? ORDER BY created_at, id""",
                (work_id,),
            )
            work["tasks"] = [
                {
                    "id": row[0],
                    "title": row[1],
                    "assignee": row[2],
                    "depends_on": json.loads(row[3]),
                }
                for row in cursor
            ]
            work["artifacts"] = [
                {
                    "id": row[0],
                    "version": row[1],
                    "title": row[2],
                    "requirement_version": row[3],
                    "created_at": row[4],
                }
                for row in conn.execute(
                    """SELECT a.id, v.version, v.title,
                    v.requirement_version, v.created_at FROM work_artifacts a
                    JOIN work_artifact_versions v ON v.artifact_id=a.id
                    WHERE a.work_id=? ORDER BY a.created_at, v.version""",
                    (work_id,),
                )
            ]
            return work

    @staticmethod
    def _store_artifact(conn, work_id, artifact_id, title, body, requirement_version, now,
                        *, source='MANUAL', source_run_id=None, source_events=()):
        existing = artifact_id is not None
        artifact_id = str(artifact_id or uuid4())
        if existing:
            row = conn.execute('SELECT latest_version FROM work_artifacts WHERE id=? AND work_id=?',
                               (artifact_id, work_id)).fetchone()
            if row is None:
                raise WorkError('WORK_ARTIFACT_NOT_FOUND', 404)
            version = row[0] + 1
            conn.execute('UPDATE work_artifacts SET latest_version=? WHERE id=?', (version, artifact_id))
        else:
            version = 1
            conn.execute('INSERT INTO work_artifacts VALUES (?,?,?,?)', (artifact_id, work_id, version, now))
        conn.execute('''INSERT INTO work_artifact_versions(artifact_id,version,title,body,requirement_version,
            created_at,source,source_run_id,source_events_json) VALUES (?,?,?,?,?,?,?,?,?)''',
            (artifact_id, version, title, body, requirement_version, now, source, source_run_id, json.dumps(source_events)))
        return artifact_id, version

    def save_artifact(self, work_id: str, command: SaveArtifact):
        def apply(conn, now):
            self._work(conn, work_id, command.expected_revision)
            if not conn.execute(
                'SELECT 1 FROM work_requirements WHERE work_id=? AND version=?',
                (work_id, command.requirement_version),
            ).fetchone():
                raise WorkError('WORK_REQUIREMENT_NOT_FOUND', 404)
            artifact_id, version = self._store_artifact(conn, work_id, command.artifact_id,
                command.title, command.body, command.requirement_version, now)
            return {
                **self._bump(conn, work_id, now),
                "artifact_id": artifact_id,
                "version": version,
            }

        return self._command("artifact", work_id, command, apply)

    def artifact(self, work_id: str, artifact_id: str, version: int):
        with self.db.transaction(immediate=False) as conn:
            row = conn.execute(
                """SELECT v.title, v.body, v.requirement_version, v.created_at,v.source,v.source_run_id,v.source_events_json
                FROM work_artifact_versions v JOIN work_artifacts a ON a.id=v.artifact_id
                WHERE a.work_id=? AND a.id=? AND v.version=?""",
                (work_id, artifact_id, version),
            ).fetchone()
            if row is None:
                raise WorkError("WORK_ARTIFACT_NOT_FOUND", 404)
            return {
                "id": artifact_id,
                "version": version,
                "title": row[0],
                "body": row[1],
                "requirement_version": row[2],
                "created_at": row[3],
                "source": row[4],
                "source_run_id": row[5],
                "source_events": json.loads(row[6]),
            }

    def view(self, work_id: str):
        with self.db.transaction(immediate=False) as conn:
            self._work(conn, work_id)
            row = conn.execute(
                "SELECT revision, state_json FROM work_views WHERE work_id=?",
                (work_id,),
            ).fetchone()
            return (
                {"revision": row[0], "state": json.loads(row[1])}
                if row
                else {"revision": 0, "state": None}
            )

    def save_view(self, work_id: str, command: SaveView):
        def apply(conn, now):
            self._work(conn, work_id)
            row = conn.execute(
                "SELECT revision FROM work_views WHERE work_id=?", (work_id,)
            ).fetchone()
            revision = row[0] if row else 0
            if revision != command.expected_revision:
                raise WorkError("WORK_VIEW_CONFLICT")
            groups = [command.state.cards]
            if command.state.space is not None:
                groups.append(command.state.space.nodes)
            for references in groups:
                seen = set()
                for card in references:
                    key = (card.kind, str(card.ref_id), card.version)
                    if key in seen:
                        raise WorkError("WORK_VIEW_DUPLICATE", 422)
                    seen.add(key)
                    if card.kind == "requirement":
                        valid = (
                            str(card.ref_id) == work_id
                            and conn.execute(
                                "SELECT 1 FROM work_requirements WHERE work_id=? AND version=?",
                                (work_id, card.version),
                            ).fetchone()
                        )
                    elif card.kind == "artifact":
                        valid = conn.execute(
                            """SELECT 1 FROM work_artifacts a JOIN work_artifact_versions v
                            ON a.id=v.artifact_id WHERE a.work_id=? AND a.id=? AND v.version=?""",
                            (work_id, str(card.ref_id), card.version),
                        ).fetchone()
                    else:
                        valid = (
                            card.version is None
                            and conn.execute(
                                "SELECT 1 FROM work_tasks WHERE work_id=? AND id=?",
                                (work_id, str(card.ref_id)),
                            ).fetchone()
                        )
                    if not valid:
                        raise WorkError("WORK_VIEW_REFERENCE_INVALID", 422)
            conn.execute(
                """INSERT INTO work_views VALUES (?, ?, ?, ?)
                ON CONFLICT(work_id) DO UPDATE SET revision=excluded.revision,
                state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (work_id, revision + 1, command.state.model_dump_json(), now),
            )
            return {"revision": revision + 1}

        return self._command("view", work_id, command, apply)

    def requirement(self, work_id: str, version: int):
        with self.db.transaction(immediate=False) as conn:
            self._work(conn, work_id)
            row = conn.execute(
                "SELECT body, created_at FROM work_requirements WHERE work_id=? AND version=?",
                (work_id, version),
            ).fetchone()
            if row is None:
                raise WorkError("WORK_REQUIREMENT_NOT_FOUND", 404)
            return {
                "work_id": work_id,
                "version": version,
                "body": row[0],
                "created_at": row[1],
            }

    def task(self, work_id: str, task_id: str):
        with self.db.transaction(immediate=False) as conn:
            self._work(conn, work_id)
            cursor = conn.execute(
                "SELECT * FROM work_tasks WHERE work_id=? AND id=?", (work_id, task_id)
            )
            row = cursor.fetchone()
            if row is None:
                raise WorkError("WORK_TASK_NOT_FOUND", 404)
            task = dict(zip((col[0] for col in cursor.description), row))
            task["depends_on"] = json.loads(task.pop("dependencies_json"))
            return task
