"""Durable field report inbox with the same ticket visibility rules as Service Desk."""

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.models import (
    FieldExternalMessage,
    FieldReportNotification,
    FieldReportNotificationRead,
    FieldWorkHandoff,
)
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role
from app.modules.tickets.models import Ticket


def _visibility(db: Session, user: User):
    if user.role != Role.ENGINEER or not has_permission(db, user, Permission.TICKET_FIELD_WORK):
        raise ApiError(403, "forbidden", "Permission denied")
    if has_permission(db, user, Permission.TICKET_READ):
        return Ticket.deleted_at.is_(None)
    if has_permission(db, user, Permission.TICKET_READ_OWN):
        return and_(Ticket.deleted_at.is_(None), Ticket.created_by == user.id)
    raise ApiError(403, "forbidden", "Permission denied")


def _visible_notifications(db: Session, user: User):
    return (
        select(
            FieldReportNotification.id,
            Ticket.id.label("ticket_id"),
            Ticket.number.label("ticket_number"),
            FieldExternalMessage.sender_name,
            FieldExternalMessage.telegram_created_at.label("reported_at"),
            FieldReportNotificationRead.read_at,
            (FieldWorkHandoff.work_status == "REPORTED").label("awaiting_verification"),
        )
        .join(FieldWorkHandoff, FieldWorkHandoff.id == FieldReportNotification.handoff_id)
        .join(Ticket, Ticket.id == FieldWorkHandoff.ticket_id)
        .join(FieldExternalMessage, FieldExternalMessage.id == FieldReportNotification.message_id)
        .outerjoin(
            FieldReportNotificationRead,
            and_(
                FieldReportNotificationRead.notification_id == FieldReportNotification.id,
                FieldReportNotificationRead.user_id == user.id,
            ),
        )
        .where(_visibility(db, user))
    )


def list_notifications(db: Session, user: User, page: int, size: int) -> dict:
    visible = _visible_notifications(db, user).subquery()
    total = db.scalar(select(func.count()).select_from(visible)) or 0
    unread_count = (
        db.scalar(select(func.count()).select_from(visible).where(visible.c.read_at.is_(None))) or 0
    )
    rows = (
        db.execute(
            select(visible)
            .order_by(visible.c.reported_at.desc(), visible.c.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "size": size,
        "total": total,
        "unread_count": unread_count,
    }


def mark_notification_read(db: Session, user: User, notification_id: UUID) -> None:
    visible = _visible_notifications(db, user).where(FieldReportNotification.id == notification_id)
    if db.execute(visible).first() is None:
        raise ApiError(404, "notification_not_found", "Notification not found")
    db.execute(
        insert(FieldReportNotificationRead)
        .values(notification_id=notification_id, user_id=user.id)
        .on_conflict_do_nothing(index_elements=["notification_id", "user_id"])
    )
    db.commit()
