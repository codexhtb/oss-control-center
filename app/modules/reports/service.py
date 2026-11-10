"""Service Desk aggregates over visible, non-deleted PostgreSQL tickets."""

from datetime import UTC, date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.modules.identity.models import User
from app.modules.identity.roles import Role
from app.modules.tickets.models import Ticket, TicketEvent

LOCAL_TZ = timezone(timedelta(hours=3))
PG_LOCAL_TZ = "Etc/GMT-3"  # PostgreSQL's POSIX timezone sign is reversed.
RESOLVED = "ticket_resolved"
CLOSED = ("ticket_closed_without_call", "callback_confirmed_closed")
RETURNED = "callback_returned_engineers"


def utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive local dates become a half-open UTC interval."""
    return (
        datetime.combine(start, time.min, LOCAL_TZ).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(UTC),
    )


def report(
    db: Session, *, start: date, end: date, user: User, read_all: bool, page: int, size: int
) -> dict:
    lower, upper = utc_bounds(start, end)
    visible = [Ticket.deleted_at.is_(None)]
    if user.role == Role.OPERATOR:
        visible.append(Ticket.ticket_type != "INTERNAL")
    if not read_all:
        visible.append(Ticket.created_by == user.id)
    cohort = [*visible, Ticket.created_at >= lower, Ticket.created_at < upper]
    events = [*visible, TicketEvent.occurred_at >= lower, TicketEvent.occurred_at < upper]

    def ticket_count(where: list) -> int:
        return db.scalar(select(func.count(Ticket.id)).where(*where)) or 0

    def event_count(kinds: tuple[str, ...] | str) -> int:
        kinds = (kinds,) if isinstance(kinds, str) else kinds
        return (
            db.scalar(
                select(func.count(TicketEvent.id))
                .join(Ticket, Ticket.id == TicketEvent.ticket_id)
                .where(*events, TicketEvent.event_type.in_(kinds))
            )
            or 0
        )

    def distribution(column) -> dict[str, int]:
        return {
            key: count
            for key, count in db.execute(
                select(column, func.count(Ticket.id)).where(*cohort).group_by(column)
            )
        }

    day = func.date(func.timezone(PG_LOCAL_TZ, Ticket.created_at))
    created_days = dict(
        db.execute(select(day, func.count(Ticket.id)).where(*cohort).group_by(day)).all()
    )
    closed_day = func.date(func.timezone(PG_LOCAL_TZ, TicketEvent.occurred_at))
    closed_days = dict(
        db.execute(
            select(closed_day, func.count(TicketEvent.id))
            .join(Ticket, Ticket.id == TicketEvent.ticket_id)
            .where(*events, TicketEvent.event_type.in_(CLOSED))
            .group_by(closed_day)
        ).all()
    )

    # A new resolution cycle starts at the last confirmed return, or ticket creation.
    # An alias avoids accidental self-correlation of the event history.
    prior = aliased(TicketEvent)
    previous_return = (
        select(func.max(prior.occurred_at))
        .where(
            prior.ticket_id == Ticket.id,
            prior.event_type == RETURNED,
            prior.occurred_at < TicketEvent.occurred_at,
        )
        .correlate(Ticket, TicketEvent)
        .scalar_subquery()
    )
    resolution_seconds = func.extract(
        "epoch", TicketEvent.occurred_at - func.coalesce(previous_return, Ticket.created_at)
    )
    close_seconds = func.extract("epoch", TicketEvent.occurred_at - Ticket.created_at)

    def average_seconds(kinds, expression) -> float | None:
        value = db.scalar(
            select(func.avg(expression))
            .select_from(TicketEvent)
            .join(Ticket, Ticket.id == TicketEvent.ticket_id)
            .where(*events, TicketEvent.event_type.in_(kinds))
        )
        return float(value) if value is not None else None

    assignee = aliased(User)
    rows = db.execute(
        select(
            Ticket.id,
            Ticket.number,
            Ticket.subject,
            Ticket.ticket_type,
            Ticket.status,
            Ticket.priority,
            Ticket.created_at,
            assignee.display_name,
        )
        .outerjoin(assignee, assignee.id == Ticket.assigned_engineer_id)
        .where(*cohort)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    ).all()
    return {
        "start": start,
        "end": end,
        "metrics": {
            "created": ticket_count(cohort),
            "resolved_events": event_count(RESOLVED),
            "closed_events": event_count(CLOSED),
            "returned_events": event_count(RETURNED),
            "active_now": ticket_count([*visible, Ticket.status.in_(("OPEN", "IN_PROGRESS"))]),
            "average_resolution_seconds": average_seconds((RESOLVED,), resolution_seconds),
            "average_closure_seconds": average_seconds(CLOSED, close_seconds),
        },
        "daily": [
            {
                "date": start + timedelta(days=offset),
                "created": created_days.get(start + timedelta(days=offset), 0),
                "closed": closed_days.get(start + timedelta(days=offset), 0),
            }
            for offset in range((end - start).days + 1)
        ],
        "distribution": {
            "types": distribution(Ticket.ticket_type),
            "statuses": distribution(Ticket.status),
            "priorities": distribution(Ticket.priority),
        },
        "details": {
            "items": [
                {
                    "id": item.id,
                    "number": item.number,
                    "subject": item.subject,
                    "ticket_type": item.ticket_type,
                    "status": item.status,
                    "priority": item.priority,
                    "created_at": item.created_at,
                    "assignee_name": item.display_name,
                }
                for item in rows
            ],
            "page": page,
            "size": size,
            "total": ticket_count(cohort),
        },
    }
