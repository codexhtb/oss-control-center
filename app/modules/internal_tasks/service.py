"""Transactional ticket assignments, scoped conversation and durable notifications."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role
from app.modules.internal_tasks.models import (
    InternalTask,
    InternalTaskEvent,
    InternalTaskNotification,
)
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent
from app.modules.tickets.service import _can_read_ticket
from app.modules.tickets.status import TicketType


def _ticket_scope(db: Session, actor: User):
    if has_permission(db, actor, Permission.TICKET_READ):
        scope = Ticket.deleted_at.is_(None)
    elif has_permission(db, actor, Permission.TICKET_READ_OWN):
        scope = and_(Ticket.deleted_at.is_(None), Ticket.created_by == actor.id)
    else:
        raise ApiError(403, "forbidden", "Permission denied")
    if actor.role == Role.OPERATOR:
        scope = and_(scope, Ticket.ticket_type != TicketType.INTERNAL)
    return scope


def _require_actor(db: Session, actor: User, permission: Permission) -> None:
    if (
        actor.deleted_at is not None
        or not actor.is_active
        or not has_permission(db, actor, permission)
    ):
        raise ApiError(403, "forbidden", "Permission denied")


def _can_view_task(db: Session, actor: User, task: InternalTask, ticket: Ticket) -> bool:
    if ticket.deleted_at is not None or not _can_read_ticket(db, actor, ticket):
        return False
    if actor.id == task.creator_id:
        return has_permission(db, actor, Permission.TASK_CREATE)
    if actor.id == task.assignee_id:
        return has_permission(db, actor, Permission.TASK_EXECUTE)
    return actor.role == Role.ADMIN and has_permission(db, actor, Permission.TASK_CREATE)


def _task(
    db: Session, actor: User, task_id: UUID, *, lock: bool = False
) -> tuple[InternalTask, Ticket]:
    statement = (
        select(InternalTask, Ticket)
        .join(Ticket, Ticket.id == InternalTask.ticket_id)
        .where(InternalTask.id == task_id)
    )
    if lock:
        statement = statement.with_for_update(of=InternalTask)
    row = db.execute(statement).one_or_none()
    if row is None or not _can_view_task(db, actor, row[0], row[1]):
        raise ApiError(404, "task_not_found", "Task not found")
    return row


def _summary(db: Session, task: InternalTask, ticket: Ticket) -> dict:
    names = dict(
        db.execute(
            select(User.id, User.display_name).where(
                User.id.in_((task.creator_id, task.assignee_id))
            )
        ).all()
    )
    return {
        "id": task.id,
        "ticket_id": task.ticket_id,
        "ticket_number": ticket.number,
        "ticket_subject": ticket.subject,
        "creator_id": task.creator_id,
        "creator_name": names.get(task.creator_id, "Сотрудник"),
        "assignee_id": task.assignee_id,
        "assignee_name": names.get(task.assignee_id, "Сотрудник"),
        "title": task.title,
        "description": task.description,
        "due_at": task.due_at,
        "status": task.status,
        "version": task.version,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
    }


def _ticket_fact(db: Session, ticket: Ticket, actor: User, task: InternalTask, kind: str) -> None:
    action = "internal_task_" + kind.lower()
    db.add(
        TicketEvent(
            ticket_id=ticket.id,
            event_type=action,
            actor_id=actor.id,
            event_data={"task_id": str(task.id)},
            ticket_version=ticket.version,
        )
    )
    db.add(
        TicketAudit(
            ticket_id=ticket.id, actor_id=actor.id, action=action, outcome="success", origin="API"
        )
    )


def _event(
    db: Session, task: InternalTask, actor: User, request_id: UUID, kind: str, body: str | None
) -> InternalTaskEvent:
    event = InternalTaskEvent(
        task_id=task.id, actor_id=actor.id, client_request_id=request_id, kind=kind, body=body
    )
    db.add(event)
    db.flush()
    recipient_id = task.assignee_id if actor.id == task.creator_id else task.creator_id
    if recipient_id != actor.id:
        db.add(
            InternalTaskNotification(task_id=task.id, event_id=event.id, recipient_id=recipient_id)
        )
    return event


def eligible_assignees(db: Session, actor: User, ticket_id: UUID) -> list[dict]:
    _require_actor(db, actor, Permission.TASK_CREATE)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None or not _can_read_ticket(db, actor, ticket):
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    users = db.scalars(
        select(User)
        .where(User.is_active.is_(True), User.deleted_at.is_(None))
        .order_by(User.display_name, User.id)
    ).all()
    return [
        {"id": user.id, "display_name": user.display_name}
        for user in users
        if has_permission(db, user, Permission.TASK_EXECUTE) and _can_read_ticket(db, user, ticket)
    ]


def create_task(
    db: Session,
    actor: User,
    ticket_id: UUID,
    assignee_id: UUID,
    title: str,
    description: str,
    due_at: datetime | None,
    request_id: UUID,
) -> dict:
    _require_actor(db, actor, Permission.TASK_CREATE)
    existing = db.scalar(
        select(InternalTask).where(
            InternalTask.creator_id == actor.id, InternalTask.client_request_id == request_id
        )
    )
    if existing is not None:
        if (
            existing.ticket_id,
            existing.assignee_id,
            existing.title,
            existing.description,
            existing.due_at,
        ) != (ticket_id, assignee_id, title, description, due_at):
            raise ApiError(409, "request_conflict", "Request ID already used")
        visible, ticket = _task(db, actor, existing.id)
        return _summary(db, visible, ticket)
    ticket = db.execute(
        select(Ticket).where(Ticket.id == ticket_id).with_for_update(read=True)
    ).scalar_one_or_none()
    if ticket is None or ticket.deleted_at is not None or not _can_read_ticket(db, actor, ticket):
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    assignee = db.get(User, assignee_id)
    if (
        assignee is None
        or not assignee.is_active
        or assignee.deleted_at is not None
        or not has_permission(db, assignee, Permission.TASK_EXECUTE)
        or not _can_read_ticket(db, assignee, ticket)
    ):
        raise ApiError(422, "invalid_assignee", "Choose an eligible active employee")
    if due_at is not None and due_at <= datetime.now(UTC):
        raise ApiError(422, "invalid_due_at", "Due date must be in the future")
    task = InternalTask(
        ticket_id=ticket_id,
        creator_id=actor.id,
        assignee_id=assignee_id,
        client_request_id=request_id,
        title=title,
        description=description,
        due_at=due_at,
    )
    db.add(task)
    try:
        db.flush()
        _event(db, task, actor, request_id, "CREATED", None)
        _ticket_fact(db, ticket, actor, task, "created")
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(InternalTask).where(
                InternalTask.creator_id == actor.id, InternalTask.client_request_id == request_id
            )
        )
        if existing is None:
            raise
        if (
            existing.ticket_id,
            existing.assignee_id,
            existing.title,
            existing.description,
            existing.due_at,
        ) != (ticket_id, assignee_id, title, description, due_at):
            raise ApiError(409, "request_conflict", "Request ID already used") from None
        visible, ticket = _task(db, actor, existing.id)
        return _summary(db, visible, ticket)
    db.refresh(task)
    return _summary(db, task, ticket)


def list_tasks(
    db: Session,
    actor: User,
    *,
    page: int,
    size: int,
    scope: str,
    state: str,
    ticket_id: UUID | None = None,
) -> dict:
    if not (
        has_permission(db, actor, Permission.TASK_CREATE)
        or has_permission(db, actor, Permission.TASK_EXECUTE)
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    ticket_filter = _ticket_scope(db, actor)
    if ticket_id is not None:
        ticket = db.get(Ticket, ticket_id)
        if (
            ticket is None
            or ticket.deleted_at is not None
            or not _can_read_ticket(db, actor, ticket)
        ):
            raise ApiError(404, "ticket_not_found", "Ticket not found")
    party = or_(
        and_(
            InternalTask.creator_id == actor.id, has_permission(db, actor, Permission.TASK_CREATE)
        ),
        and_(
            InternalTask.assignee_id == actor.id, has_permission(db, actor, Permission.TASK_EXECUTE)
        ),
    )
    if (
        actor.role == Role.ADMIN
        and has_permission(db, actor, Permission.TASK_CREATE)
        and ticket_id is not None
    ):
        party = True
    if scope == "assigned":
        party = and_(
            InternalTask.assignee_id == actor.id, has_permission(db, actor, Permission.TASK_EXECUTE)
        )
    elif scope == "created":
        party = and_(
            InternalTask.creator_id == actor.id, has_permission(db, actor, Permission.TASK_CREATE)
        )
    conditions = [ticket_filter, party]
    if ticket_id is not None:
        conditions.append(InternalTask.ticket_id == ticket_id)
    now = datetime.now(UTC)
    if state == "active":
        conditions.append(InternalTask.status == "OPEN")
    elif state == "completed":
        conditions.append(InternalTask.status.in_(("COMPLETED", "NO_ANSWER")))
    elif state == "overdue":
        conditions.extend((InternalTask.status == "OPEN", InternalTask.due_at < now))
    query = (
        select(InternalTask, Ticket)
        .join(Ticket, Ticket.id == InternalTask.ticket_id)
        .where(*conditions)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(InternalTask.updated_at.desc(), InternalTask.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {
        "items": [_summary(db, task, ticket) for task, ticket in rows],
        "page": page,
        "size": size,
        "total": total,
    }


def get_task(db: Session, actor: User, task_id: UUID) -> dict:
    task, ticket = _task(db, actor, task_id)
    return _summary(db, task, ticket)


def list_events(db: Session, actor: User, task_id: UUID, page: int, size: int) -> dict:
    _task(db, actor, task_id)
    total = (
        db.scalar(
            select(func.count())
            .select_from(InternalTaskEvent)
            .where(InternalTaskEvent.task_id == task_id)
        )
        or 0
    )
    rows = db.execute(
        select(InternalTaskEvent, User.display_name)
        .join(User, User.id == InternalTaskEvent.actor_id)
        .where(InternalTaskEvent.task_id == task_id)
        .order_by(InternalTaskEvent.created_at.desc(), InternalTaskEvent.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {
        "items": [
            {
                "id": event.id,
                "actor_id": event.actor_id,
                "actor_name": name,
                "kind": event.kind,
                "body": event.body,
                "created_at": event.created_at,
            }
            for event, name in rows
        ],
        "page": page,
        "size": size,
        "total": total,
    }


def add_event(
    db: Session,
    actor: User,
    task_id: UUID,
    request_id: UUID,
    kind: str,
    body: str | None,
    version: int,
) -> dict:
    permission = (
        Permission.TASK_EXECUTE
        if kind in ("COMPLETED", "NO_ANSWER")
        else Permission.TASK_CREATE
        if kind == "REOPENED"
        else None
    )
    if permission is not None:
        _require_actor(db, actor, permission)
    task, ticket = _task(db, actor, task_id, lock=True)
    existing = db.scalar(
        select(InternalTaskEvent).where(
            InternalTaskEvent.task_id == task_id,
            InternalTaskEvent.actor_id == actor.id,
            InternalTaskEvent.client_request_id == request_id,
        )
    )
    if existing is not None:
        if existing.kind != kind or existing.body != body:
            raise ApiError(409, "request_conflict", "Request ID already used")
        return _summary(db, task, ticket)
    if task.version != version:
        raise ApiError(409, "version_conflict", "Task changed; refresh it")
    if kind in ("COMPLETED", "NO_ANSWER"):
        if actor.id != task.assignee_id or task.status != "OPEN":
            raise ApiError(409, "invalid_task_transition", "Task cannot be completed")
        task.status = kind
        task.completed_at = datetime.now(UTC)
    elif kind == "REOPENED":
        if actor.id != task.creator_id or task.status == "OPEN":
            raise ApiError(409, "invalid_task_transition", "Task cannot be reopened")
        task.status = "OPEN"
        task.completed_at = None
    elif kind == "MESSAGE":
        if task.status != "OPEN":
            raise ApiError(409, "invalid_task_transition", "Reopen task before replying")
        if actor.id == task.creator_id:
            _require_actor(db, actor, Permission.TASK_CREATE)
        elif actor.id == task.assignee_id:
            _require_actor(db, actor, Permission.TASK_EXECUTE)
        else:
            raise ApiError(404, "task_not_found", "Task not found")
    else:
        raise ApiError(422, "invalid_event", "Unsupported task event")
    task.version += 1
    task.updated_at = datetime.now(UTC)
    try:
        _event(db, task, actor, request_id, kind, body)
        _ticket_fact(db, ticket, actor, task, kind)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "request_conflict", "Task was updated; refresh it") from None
    db.refresh(task)
    return _summary(db, task, ticket)


def list_notifications(db: Session, actor: User, page: int, size: int) -> dict:
    if not (
        has_permission(db, actor, Permission.TASK_CREATE)
        or has_permission(db, actor, Permission.TASK_EXECUTE)
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    scope = _ticket_scope(db, actor)
    visible = (
        select(
            InternalTaskNotification.id,
            InternalTaskNotification.task_id,
            InternalTaskNotification.created_at,
            InternalTaskNotification.read_at,
            InternalTaskEvent.kind,
            InternalTask.title,
            Ticket.id.label("ticket_id"),
            Ticket.number.label("ticket_number"),
        )
        .join(InternalTask, InternalTask.id == InternalTaskNotification.task_id)
        .join(Ticket, Ticket.id == InternalTask.ticket_id)
        .join(InternalTaskEvent, InternalTaskEvent.id == InternalTaskNotification.event_id)
        .where(
            InternalTaskNotification.recipient_id == actor.id,
            scope,
            or_(
                and_(
                    InternalTask.assignee_id == actor.id,
                    has_permission(db, actor, Permission.TASK_EXECUTE),
                ),
                and_(
                    InternalTask.creator_id == actor.id,
                    has_permission(db, actor, Permission.TASK_CREATE),
                ),
            ),
        )
    ).subquery()
    total = db.scalar(select(func.count()).select_from(visible)) or 0
    unread = (
        db.scalar(select(func.count()).select_from(visible).where(visible.c.read_at.is_(None))) or 0
    )
    rows = (
        db.execute(
            select(visible)
            .order_by(visible.c.created_at.desc(), visible.c.id.desc())
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
        "unread_count": unread,
    }


def mark_read(db: Session, actor: User, notification_id: UUID) -> None:
    notification = db.get(InternalTaskNotification, notification_id)
    if notification is None or notification.recipient_id != actor.id:
        raise ApiError(404, "notification_not_found", "Notification not found")
    _task(db, actor, notification.task_id)
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        db.commit()
