"""Engineer inbox for Telegram field reports."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.modules.field_dispatch.notifications import list_notifications, mark_notification_read
from app.modules.notifications import service as center

router = APIRouter(prefix="/notifications", tags=["notifications"])


class FieldReportNotificationView(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_number: int
    sender_name: str
    reported_at: datetime
    read_at: datetime | None
    awaiting_verification: bool


class NotificationPage(BaseModel):
    items: list[FieldReportNotificationView]
    page: int
    size: int
    total: int
    unread_count: int


class CenterNotification(BaseModel):
    id: UUID
    source: Literal["task", "field_report"]
    ticket_id: UUID
    ticket_number: int
    task_id: UUID | None
    kind: str
    title: str
    created_at: datetime
    read_at: datetime | None


class CenterPage(BaseModel):
    items: list[CenterNotification]
    page: int
    size: int
    total: int
    unread_count: int


@router.get("/center", response_model=CenterPage)
def notification_center(
    identity: CurrentIdentityDep,
    db: SessionDep,
    state: Literal["unread", "read"] = "unread",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
):
    return center.list_inbox(db, identity.user, state, page, size)


@router.post("/center/read-all", status_code=204)
def read_all(identity: CsrfIdentityDep, db: SessionDep) -> None:
    center.mark_read(db, identity.user)


@router.post("/center/{source}/{notification_id}/read", status_code=204)
def read_center_notification(
    source: Literal["task", "field_report"], notification_id: UUID,
    identity: CsrfIdentityDep, db: SessionDep,
) -> None:
    center.mark_read(db, identity.user, source, notification_id)


@router.get("", response_model=NotificationPage)
def inbox(
    identity: CurrentIdentityDep,
    db: SessionDep,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
):
    return list_notifications(db, identity.user, page, size)


@router.post("/{notification_id}/read", status_code=204)
def read(notification_id: UUID, identity: CsrfIdentityDep, db: SessionDep) -> None:
    mark_notification_read(db, identity.user, notification_id)
