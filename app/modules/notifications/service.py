"""One visibility policy and one database snapshot for the portal inbox."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, cast, func, literal, select, true, union_all, update
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.models import FieldReportNotificationRead
from app.modules.field_dispatch.notifications import _visible_notifications
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role
from app.modules.internal_tasks.models import InternalTaskNotification
from app.modules.internal_tasks.service import visible_notifications


def _visible(db: Session, user: User, now: datetime):
    sources = []
    can_read = has_permission(db, user, Permission.TICKET_READ) or has_permission(
        db, user, Permission.TICKET_READ_OWN
    )
    if not can_read:
        raise ApiError(403, "forbidden", "Permission denied")
    if has_permission(db, user, Permission.TASK_CREATE) or has_permission(
        db, user, Permission.TASK_EXECUTE
    ):
        task = visible_notifications(db, user, now=now).subquery()
        sources.append(select(
            task.c.id, literal("task").label("source"), task.c.ticket_id,
            task.c.ticket_number, task.c.task_id, task.c.kind, task.c.title,
            task.c.created_at, task.c.read_at,
        ))
    if user.role in (Role.ENGINEER, Role.ADMIN) and has_permission(
        db, user, Permission.TICKET_FIELD_WORK
    ):
        field = _visible_notifications(db, user).subquery()
        sources.append(select(
            field.c.id, literal("field_report").label("source"), field.c.ticket_id,
            field.c.ticket_number, cast(literal(None), PGUUID).label("task_id"),
            literal("FIELD_REPORT").label("kind"),
            (literal("Отчёт: ") + field.c.sender_name).label("title"),
            field.c.reported_at.label("created_at"), field.c.read_at,
        ))
    if not sources:
        raise ApiError(403, "forbidden", "Permission denied")
    return union_all(*sources).cte("visible_notifications")


def list_inbox(db: Session, user: User, state: str, page: int, size: int) -> dict:
    visible = _visible(db, user, datetime.now(UTC))
    state_filter = (
        visible.c.read_at.is_(None) if state == "unread" else visible.c.read_at.is_not(None)
    )
    stats = select(
        func.count().filter(state_filter).label("total"),
        func.count().filter(visible.c.read_at.is_(None)).label("unread_count"),
    ).select_from(visible).subquery()
    items = (select(visible).where(state_filter)
             .order_by(visible.c.created_at.desc(), visible.c.source, visible.c.id.desc())
             .offset((page - 1) * size).limit(size).subquery())
    # Counts and page are read in a single statement, including for an empty page.
    rows = db.execute(select(stats, items).select_from(stats.outerjoin(items, true()))
                      .order_by(items.c.created_at.desc(), items.c.source, items.c.id.desc())
                      ).mappings().all()
    return {
        "items": [{key: row[key] for key in items.c.keys()} for row in rows if row["id"]],
        "total": rows[0]["total"], "unread_count": rows[0]["unread_count"],
        "page": page, "size": size,
    }


def mark_read(db: Session, user: User, source: str | None = None, notice_id: UUID | None = None):
    now = datetime.now(UTC)
    visible = _visible(db, user, now)
    chosen = select(visible.c.id, visible.c.source, visible.c.read_at)
    if source is not None:
        chosen = chosen.where(visible.c.source == source, visible.c.id == notice_id)
    # Freeze the visible set: future reminders and events arriving during this action stay unread.
    rows = db.execute(chosen).all()
    if source is not None and not rows:
        raise ApiError(404, "notification_not_found", "Notification not found")
    task_ids = [row.id for row in rows if row.source == "task" and row.read_at is None]
    field_ids = [row.id for row in rows if row.source == "field_report" and row.read_at is None]
    if task_ids:
        db.execute(update(InternalTaskNotification).where(
            InternalTaskNotification.id.in_(task_ids),
            InternalTaskNotification.recipient_id == user.id,
            InternalTaskNotification.read_at.is_(None),
        ).values(read_at=now))
    if field_ids:
        db.execute(insert(FieldReportNotificationRead).from_select(
            ["notification_id", "user_id", "read_at"],
            select(visible.c.id, literal(user.id), cast(literal(now), DateTime(timezone=True)))
            .where(visible.c.id.in_(field_ids), visible.c.source == "field_report"),
        ).on_conflict_do_nothing(index_elements=["notification_id", "user_id"]))
    db.commit()
