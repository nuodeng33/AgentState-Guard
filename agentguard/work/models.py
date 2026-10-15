"""Bounded commands for the first local work API."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID

    @field_validator("title", "owner", "assignee", check_fields=False)
    @classmethod
    def nonblank_label(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("label must not be blank")
        return value.strip()


class CreateWork(Command):
    title: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=120)
    requirement: str = Field(min_length=1, max_length=100_000)


class EditWork(Command):
    expected_revision: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=120)


class ReviseRequirement(Command):
    expected_revision: int = Field(strict=True, ge=1)
    body: str = Field(min_length=1, max_length=100_000)


class WriteTask(Command):
    expected_revision: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=100_000)
    assignee: str | None = Field(default=None, min_length=1, max_length=120)
    depends_on: list[UUID] = Field(default_factory=list, max_length=100)


class SaveArtifact(Command):
    expected_revision: int = Field(strict=True, ge=1)
    artifact_id: UUID | None = None
    requirement_version: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=100_000)


class AttachAgent(Command):
    expected_revision: int = Field(strict=True, ge=1)
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")


class DetachAgent(Command):
    expected_revision: int = Field(strict=True, ge=1)


class AssignAgentTasks(Command):
    expected_revision: int = Field(strict=True, ge=1)
    task_ids: list[UUID] = Field(max_length=500)


class Viewport(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(default=24, ge=-100_000, le=100_000)
    y: float = Field(default=24, ge=-100_000, le=100_000)
    zoom: float = Field(default=1, ge=0.25, le=2)


class ViewNode(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["requirement", "task", "artifact"]
    ref_id: UUID
    version: int | None = Field(default=None, strict=True, ge=1)
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)


class ViewCard(ViewNode):
    width: float = Field(default=400, ge=300, le=1400)
    height: float = Field(default=430, ge=220, le=1400)
    scroll: float = Field(default=0, ge=0, le=10_000_000)


class RelationshipSpace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    viewport: Viewport = Field(default_factory=Viewport)
    nodes: list[ViewNode] = Field(default_factory=list, max_length=1000)


class WorkbenchState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    viewport: Viewport = Field(default_factory=Viewport)
    cards: list[ViewCard] = Field(default_factory=list, max_length=40)
    space: RelationshipSpace | None = None
    surface: Literal["space", "board"] = "space"

    @model_serializer(mode="wrap")
    def serialize_state(self, handler):
        value = handler(self)
        # Preserve old saved payloads and their idempotency receipt digests.
        for field in ("space", "surface"):
            if field not in self.model_fields_set:
                value.pop(field, None)
        return value


class SaveView(Command):
    expected_revision: int = Field(strict=True, ge=0)
    state: WorkbenchState
