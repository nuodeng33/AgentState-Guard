"""Independent Agent appearance routes behind the Core session middleware."""
import sqlite3
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from fastapi import Path as PathParameter
from fastapi.responses import JSONResponse

from agentguard.storage.db import StateDB
from agentguard.work.appearance import AgentAppearanceService, SaveAgentAppearance
from agentguard.work.service import WorkError


def appearance_router(db_path: Path) -> APIRouter:
    router = APIRouter(prefix="/api/v1/works")

    def invoke(method, *args):
        database = StateDB(db_path)
        try:
            database.connect()
            return getattr(AgentAppearanceService(database), method)(*args)
        except WorkError as error:
            return JSONResponse({"reason_code": error.code}, status_code=error.status)
        except sqlite3.OperationalError:
            return JSONResponse({"reason_code": "WORK_STORAGE_UNAVAILABLE"}, status_code=503)
        finally:
            database.close()

    @router.get("/{work_id}/agent-appearances")
    def appearances(work_id: UUID):
        return invoke("appearances", str(work_id))

    @router.post("/{work_id}/agents/{agent_id}/appearance")
    def save_appearance(work_id: UUID, agent_id: Annotated[str, PathParameter(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")], command: SaveAgentAppearance):
        return invoke("save_appearance", str(work_id), agent_id, command)

    return router
