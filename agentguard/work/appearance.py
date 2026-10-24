"""Revisioned display preferences for already-linked Work Agents."""
from typing import Literal

from pydantic import ConfigDict, Field

from .models import Command
from .service import WorkError, WorkService

AgentColor = Literal["#7cc6bc", "#b9a6f4", "#8fb8e8", "#e3a5bf", "#d9c08a", "#a8c98f"]


class SaveAgentAppearance(Command):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=0)
    color: AgentColor | None


class AgentAppearanceService(WorkService):
    def appearances(self, work_id: str):
        with self.db.transaction(immediate=False) as conn:
            self._work(conn, work_id)
            rows = conn.execute(
                """SELECT link.agent_id, appearance.color, COALESCE(appearance.revision,0), appearance.updated_at
                   FROM work_agent_links AS link
                   LEFT JOIN work_agent_appearances AS appearance
                     ON appearance.work_id=link.work_id AND appearance.agent_id=link.agent_id
                   WHERE link.work_id=? ORDER BY link.agent_id""", (work_id,)
            ).fetchall()
            return {"work_id": work_id, "items": [
                {"agent_id": row[0], "color": row[1], "revision": row[2], "updated_at": row[3]}
                for row in rows
            ]}

    def save_appearance(self, work_id: str, agent_id: str, command: SaveAgentAppearance):
        def apply(conn, now):
            self._work(conn, work_id)
            if not conn.execute("SELECT 1 FROM work_agent_links WHERE work_id=? AND agent_id=?", (work_id, agent_id)).fetchone():
                raise WorkError("WORK_AGENT_LINK_NOT_FOUND", 404)
            previous = conn.execute("SELECT revision FROM work_agent_appearances WHERE work_id=? AND agent_id=?", (work_id, agent_id)).fetchone()
            revision = previous[0] if previous else 0
            if revision != command.expected_revision:
                raise WorkError("WORK_APPEARANCE_CONFLICT")
            conn.execute(
                """INSERT INTO work_agent_appearances(work_id,agent_id,color,revision,created_at,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(work_id,agent_id) DO UPDATE
                   SET color=excluded.color,revision=excluded.revision,updated_at=excluded.updated_at""",
                (work_id, agent_id, command.color, revision + 1, now, now),
            )
            return {"work_id": work_id, "agent_id": agent_id, "color": command.color,
                    "revision": revision + 1, "updated_at": now}
        return self._command("agent_appearance", [work_id, agent_id], command, apply)
