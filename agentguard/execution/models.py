"""Bounded commands; the API never accepts executable argv or native methods."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentguard.work.models import Command


class CreateSession(Command):
    expected_revision: int = Field(strict=True, ge=1)
    application_id: str = Field(min_length=1, max_length=96)
    store_id: str = Field(min_length=1, max_length=96)
    scope_id: str = Field(min_length=1, max_length=96)
    task_id: UUID | None = None
    mode: Literal['NEW', 'CONTINUE', 'FORK'] = 'NEW'
    binding_id: str | None = Field(default=None, max_length=96)
    read_only: bool = False
    control_consent: Literal[True]


class ContextRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['requirement', 'artifact', 'file', 'review']
    ref_id: str = Field(min_length=1, max_length=96)
    version: int | None = Field(default=None, strict=True, ge=1)
    version_id: str | None = Field(default=None, max_length=96)
    file_id: str | None = Field(default=None, max_length=96)


class StartRun(Command):
    expected_revision: int = Field(strict=True, ge=1)
    requirement_version: int = Field(strict=True, ge=1)
    input: str = Field(min_length=1, max_length=65536)
    context_refs: list[ContextRef] = Field(default_factory=list, max_length=32)

    @field_validator('input')
    @classmethod
    def input_bytes(cls, value):
        if not value.strip() or len(value.encode('utf-8')) > 65536:
            raise ValueError('input exceeds 64 KiB or is empty')
        return value


class SessionMutation(Command):
    expected_revision: int = Field(strict=True, ge=1)


class RenameSession(SessionMutation):
    nickname: str = Field(max_length=120)


class ConnectSession(SessionMutation):
    control_consent: Literal[True]


class CancelRun(Command):
    expected_revision: int | None = Field(default=None, strict=True, ge=1)


class RespondRun(Command):
    event_seq: int = Field(strict=True, ge=1)
    action_ref: str = Field(min_length=1, max_length=96)
    response: dict = Field(max_length=32)
