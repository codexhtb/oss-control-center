"""Engineer inbox for Telegram field reports."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.modules.field_dispatch.notifications import list_notifications, mark_notification_read

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
