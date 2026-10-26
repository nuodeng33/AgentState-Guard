"""Local-only work routes; mounted behind the existing Core session middleware."""

import sqlite3
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from agentguard.storage.db import StateDB
from agentguard.work.models import (
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
from agentguard.work.service import WorkError, WorkService


def work_router(db_path: Path, agent_enricher=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/works")

    def invoke(method, *args, **kwargs):
        # Each synchronous request owns its SQLite connection on the same worker.
        database = StateDB(db_path)
        try:
            database.connect()
            return getattr(WorkService(database), method)(*args, **kwargs)
        except WorkError as error:
            return JSONResponse({"reason_code": error.code}, status_code=error.status)
        except sqlite3.OperationalError:
            return JSONResponse(
                {"reason_code": "WORK_STORAGE_UNAVAILABLE"}, status_code=503
            )
        finally:
            database.close()

    @router.post("", status_code=201)
    def create(command: CreateWork):
        return invoke("create", command)

    @router.get("")
    def list_works(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        result = invoke("list", limit, offset)
        return result if isinstance(result, JSONResponse) else {"items": result}

    @router.get("/{work_id}")
    def get(work_id: UUID):
        return invoke("get", str(work_id))

    @router.get("/{work_id}/agents")
    def agents(work_id: UUID, view: Literal["observed", "links"] = "observed"):
        result = invoke("agents", str(work_id), view=view)
        return agent_enricher(result) if agent_enricher and isinstance(result, dict) else result

    @router.post("/{work_id}/agents", status_code=201)
    def attach_agent(work_id: UUID, command: AttachAgent):
        return invoke("attach_agent", str(work_id), command)

    @router.post("/{work_id}/agents/{agent_id}/detach")
    def detach_agent(work_id: UUID, agent_id: str, command: DetachAgent):
        return invoke("detach_agent", str(work_id), agent_id, command)

    @router.post("/{work_id}/agents/{agent_id}/tasks")
    def assign_agent_tasks(work_id: UUID, agent_id: str, command: AssignAgentTasks):
        return invoke("assign_agent_tasks", str(work_id), agent_id, command)

    @router.patch("/{work_id}")
    def edit(work_id: UUID, command: EditWork):
        return invoke("edit", str(work_id), command)

    @router.post("/{work_id}/requirements", status_code=201)
    def revise(work_id: UUID, command: ReviseRequirement):
        return invoke("revise", str(work_id), command)

    @router.get("/{work_id}/requirements/{version}")
    def requirement(work_id: UUID, version: int):
        return invoke("requirement", str(work_id), version)

    @router.post("/{work_id}/tasks", status_code=201)
    def create_task(work_id: UUID, command: WriteTask):
        return invoke("write_task", str(work_id), command)

    @router.get("/{work_id}/tasks/{task_id}")
    def task(work_id: UUID, task_id: UUID):
        return invoke("task", str(work_id), str(task_id))

    @router.put("/{work_id}/tasks/{task_id}")
    def edit_task(work_id: UUID, task_id: UUID, command: WriteTask):
        return invoke("write_task", str(work_id), command, str(task_id))

    @router.post("/{work_id}/artifacts", status_code=201)
    def save_artifact(work_id: UUID, command: SaveArtifact):
        return invoke("save_artifact", str(work_id), command)

    @router.get("/{work_id}/artifacts/{artifact_id}/{version}")
    def artifact(work_id: UUID, artifact_id: UUID, version: int):
        return invoke("artifact", str(work_id), str(artifact_id), version)

    @router.get("/{work_id}/view")
    def view(work_id: UUID):
        return invoke("view", str(work_id))

    @router.post("/{work_id}/view")
    def save_view(work_id: UUID, command: SaveView):
        return invoke("save_view", str(work_id), command)

    return router
