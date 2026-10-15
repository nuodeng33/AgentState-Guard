"""Authenticated local program and session-store registration routes."""

import sqlite3
from contextlib import nullcontext
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentguard.discovery.catalog import CatalogError


class ApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1, max_length=128)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    label: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.path) == bool(self.candidate_id):
            raise ValueError("Select a path or a candidate")
        return self


class StoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1, max_length=128)
    provider: Literal["CODEX", "KIMI_CODE", "ZCODE", "CLOUDCLI"]
    path: str = Field(min_length=1, max_length=4096)


def discovery_router(catalog_context, discovery_lock):
    router = APIRouter(prefix="/api/v1/discovery")

    def invoke(method, *args, **kwargs):
        try:
            guard = nullcontext() if method in {"applications", "stores", "read_sessions"} else discovery_lock
            with guard, catalog_context() as catalog:
                return getattr(catalog, method)(*args, **kwargs)
        except CatalogError as exc:
            return JSONResponse({"status": "UNKNOWN", "reason_code": exc.reason_code, "sessions": []}, status_code=exc.status_code)
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            return JSONResponse({"status": "UNAVAILABLE", "reason_code": getattr(exc, "reason_code", "DISCOVERY_CATALOG_UNAVAILABLE")}, status_code=503)

    @router.get("/applications")
    def applications(source_id: str | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), include_unclassified: bool = False):
        return invoke("applications", source_id, limit, offset, include_unclassified)

    @router.post("/applications", status_code=201)
    def register_application(body: ApplicationRequest):
        return invoke("register_application", **body.model_dump())

    @router.get("/sources/{source_id}/session-stores")
    def stores(source_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return invoke("stores", source_id, limit, offset)

    @router.post("/session-stores", status_code=201)
    def register_store(body: StoreRequest):
        return invoke("register_store", **body.model_dump())

    @router.get("/sources/{source_id}/sessions")
    def sessions(source_id: str, store_id: str | None = None,
                 limit: int = Query(50, ge=1, le=50), offset: int = Query(0, ge=0, le=1000000)):
        return invoke("read_sessions", source_id, store_id, limit, offset)

    return router
