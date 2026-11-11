"""Authenticated, ticket-scoped internal assignments."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.internal_tasks import service

router = APIRouter(prefix="/tasks", tags=["internal-tasks"])


class TaskView(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_number: int
    ticket_subject: str | None
    creator_id: UUID
    creator_name: str
    assignee_id: UUID
    assignee_name: str
    title: str
    description: str
    due_at: datetime | None
    status: Literal["OPEN", "COMPLETED", "NO_ANSWER"]
    version: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TaskPage(BaseModel):
    items: list[TaskView]
    page: int
    size: int
    total: int


class TaskEventView(BaseModel):
    id: UUID
    actor_id: UUID
    actor_name: str
    kind: Literal["CREATED", "MESSAGE", "COMPLETED", "NO_ANSWER", "REOPENED"]
    body: str | None
    created_at: datetime


class TaskEventPage(BaseModel):
    items: list[TaskEventView]
    page: int
    size: int
    total: int


class TaskNotificationView(BaseModel):
    id: UUID
    task_id: UUID
    ticket_id: UUID
    ticket_number: int
    title: str
    kind: str
    created_at: datetime
    read_at: datetime | None


class TaskNotificationPage(BaseModel):
    items: list[TaskNotificationView]
    page: int
    size: int
    total: int
    unread_count: int


class AssigneeView(BaseModel):
    id: UUID
    display_name: str


class CreateTask(BaseModel):
    ticket_id: UUID
    assignee_id: UUID
    client_request_id: UUID
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    due_at: AwareDatetime | None = None

    @field_validator("title", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        result = value.strip()
        if not result or any(ord(char) < 32 and char not in "\n\t" for char in result):
            raise ValueError("Text must not be empty or contain control characters")
        return result


class TaskAction(BaseModel):
    client_request_id: UUID
    kind: Literal["MESSAGE", "COMPLETED", "NO_ANSWER", "REOPENED"]
    body: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def normalize_body(self) -> "TaskAction":
        if self.body is not None:
            self.body = self.body.strip()
        if self.kind == "MESSAGE" and not self.body:
            raise ValueError("Message text is required")
        if self.body is not None and (
            not self.body or any(ord(char) < 32 and char not in "\n\t" for char in self.body)
        ):
            raise ValueError("Invalid message text")
        return self


def _version(header: str | None) -> int:
    if header is None:
        raise ApiError(428, "version_required", "If-Match task version is required")
    if len(header) < 3 or header[0] != '"' or header[-1] != '"' or not header[1:-1].isdigit():
        raise ApiError(400, "invalid_if_match", "Invalid If-Match header")
    return int(header[1:-1])


@router.get("/assignees", response_model=list[AssigneeView])
def assignees(ticket_id: UUID, identity: CurrentIdentityDep, db: SessionDep):
    return service.eligible_assignees(db, identity.user, ticket_id)


@router.get("/notifications", response_model=TaskNotificationPage)
def notifications(
    identity: CurrentIdentityDep,
    db: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return service.list_notifications(db, identity.user, page, size)


@router.post("/notifications/{notification_id}/read", status_code=204)
def read_notification(notification_id: UUID, identity: CsrfIdentityDep, db: SessionDep) -> None:
    service.mark_read(db, identity.user, notification_id)


@router.get("", response_model=TaskPage)
def list_tasks(
    identity: CurrentIdentityDep,
    db: SessionDep,
    scope: Literal["assigned", "created", "all"] = "assigned",
    state: Literal["all", "active", "completed", "overdue"] = "active",
    ticket_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return service.list_tasks(
        db, identity.user, page=page, size=size, scope=scope, state=state, ticket_id=ticket_id
    )


@router.post("", response_model=TaskView, status_code=201)
def create_task(payload: CreateTask, identity: CsrfIdentityDep, db: SessionDep):
    return service.create_task(
        db,
        identity.user,
        payload.ticket_id,
        payload.assignee_id,
        payload.title,
        payload.description,
        payload.due_at,
        payload.client_request_id,
    )


@router.get("/{task_id}", response_model=TaskView)
def get_task(task_id: UUID, identity: CurrentIdentityDep, db: SessionDep):
    return service.get_task(db, identity.user, task_id)


@router.get("/{task_id}/events", response_model=TaskEventPage)
def events(
    task_id: UUID,
    identity: CurrentIdentityDep,
    db: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 50,
):
    return service.list_events(db, identity.user, task_id, page, size)


@router.post("/{task_id}/events", response_model=TaskView)
def add_event(
    task_id: UUID,
    payload: TaskAction,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    return service.add_event(
        db,
        identity.user,
        task_id,
        payload.client_request_id,
        payload.kind,
        payload.body,
        _version(if_match),
    )
