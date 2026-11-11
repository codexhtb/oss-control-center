"""Service Desk aggregates over visible, non-deleted PostgreSQL tickets."""

from datetime import UTC, date, datetime, time, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.modules.identity.models import User
from app.modules.identity.roles import Role
from app.modules.tickets.models import Ticket, TicketCallback, TicketEvent, TicketResolution

LOCAL_TZ = timezone(timedelta(hours=3))
PG_LOCAL_TZ = "Etc/GMT-3"  # PostgreSQL's POSIX timezone sign is reversed.
RESOLVED = "ticket_resolved"
CLOSED = ("ticket_closed_without_call", "callback_confirmed_closed")
RETURNED = "callback_returned_engineers"
CALLBACK_OUTCOMES = ("CONFIRMED", "STILL_BROKEN", "NO_ANSWER")


def utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive local dates become a half-open UTC interval."""
    return (
        datetime.combine(start, time.min, LOCAL_TZ).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ).astimezone(UTC),
    )


def _visible(user: User, read_all: bool) -> list:
    conditions = [Ticket.deleted_at.is_(None)]
    if user.role == Role.OPERATOR:
        conditions.append(Ticket.ticket_type != "INTERNAL")
    if not read_all:
        conditions.append(Ticket.created_by == user.id)
    return conditions


def _day(column):
    return func.date(func.timezone(PG_LOCAL_TZ, column))


def _activity_days(db: Session, lower: datetime, upper: datetime, visible: list) -> list[dict]:
    """Only grouped actor/day rows leave PostgreSQL, never individual actions."""
    grouped: dict[tuple, dict] = {}
    sources = (
        (Ticket.created_by, Ticket.created_at, Ticket.id, "created", None),
        (
            TicketResolution.engineer_id,
            TicketResolution.created_at,
            TicketResolution.id,
            "decisions",
            TicketResolution.ticket_id == Ticket.id,
        ),
        (
            TicketCallback.actor_id,
            TicketCallback.occurred_at,
            TicketCallback.id,
            "callbacks",
            TicketCallback.ticket_id == Ticket.id,
        ),
    )
    for actor, occurred, identifier, kind, join_condition in sources:
        day = _day(occurred)
        query = (
            select(actor, day, func.count(identifier))
            .select_from(Ticket)
            .where(*visible, occurred >= lower, occurred < upper)
            .group_by(actor, day)
        )
        if join_condition is not None:
            query = query.join(
                TicketResolution if kind == "decisions" else TicketCallback, join_condition
            )
        for actor_id, date_value, count in db.execute(query):
            row = grouped.setdefault(
                (actor_id, date_value),
                {
                    "user_id": actor_id,
                    "date": date_value,
                    "created": 0,
                    "decisions": 0,
                    "callbacks": 0,
                },
            )
            row[kind] = count
    return sorted(grouped.values(), key=lambda row: (row["date"], str(row["user_id"])))


def employees_report(db: Session, *, start: date, end: date, user: User, read_all: bool) -> dict:
    lower, upper = utc_bounds(start, end)
    visible = _visible(user, read_all)
    created = (
        select(Ticket.created_by.label("user_id"), func.count(Ticket.id).label("count"))
        .where(*visible, Ticket.created_at >= lower, Ticket.created_at < upper)
        .group_by(Ticket.created_by)
        .subquery()
    )
    decisions = (
        select(
            TicketResolution.engineer_id.label("user_id"),
            func.count(TicketResolution.id).label("count"),
        )
        .join(Ticket, Ticket.id == TicketResolution.ticket_id)
        .where(*visible, TicketResolution.created_at >= lower, TicketResolution.created_at < upper)
        .group_by(TicketResolution.engineer_id)
        .subquery()
    )
    callbacks = (
        select(
            TicketCallback.actor_id.label("user_id"),
            func.count(TicketCallback.id).label("count"),
            func.count(func.distinct(TicketCallback.ticket_id)).label("unique_tickets"),
            *[
                func.sum(case((TicketCallback.outcome == outcome, 1), else_=0)).label(outcome)
                for outcome in CALLBACK_OUTCOMES
            ],
        )
        .join(Ticket, Ticket.id == TicketCallback.ticket_id)
        .where(*visible, TicketCallback.occurred_at >= lower, TicketCallback.occurred_at < upper)
        .group_by(TicketCallback.actor_id)
        .subquery()
    )
    rows = db.execute(
        select(
            User.id,
            User.display_name,
            User.role,
            User.is_active,
            func.coalesce(created.c.count, 0),
            func.coalesce(decisions.c.count, 0),
            func.coalesce(callbacks.c.count, 0),
            func.coalesce(callbacks.c.unique_tickets, 0),
            *[func.coalesce(callbacks.c[outcome], 0) for outcome in CALLBACK_OUTCOMES],
        )
        .outerjoin(created, created.c.user_id == User.id)
        .outerjoin(decisions, decisions.c.user_id == User.id)
        .outerjoin(callbacks, callbacks.c.user_id == User.id)
        .where(
            (
                created.c.count.is_not(None)
                | decisions.c.count.is_not(None)
                | callbacks.c.count.is_not(None)
            )
            if not read_all
            else True
        )
        .order_by(User.display_name, User.id)
    ).all()
    return {
        "start": start,
        "end": end,
        "items": [
            {
                "user_id": row[0],
                "display_name": row[1],
                "role": row[2],
                "is_active": row[3],
                "created_tickets": row[4],
                "decision_events": row[5],
                "callback_events": row[6],
                "callback_tickets": row[7],
                "outcomes": dict(zip(CALLBACK_OUTCOMES, row[8:], strict=True)),
            }
            for row in rows
        ],
        "daily": _activity_days(db, lower, upper, visible),
    }


def callbacks_report(
    db: Session, *, start: date, end: date, user: User, read_all: bool, page: int, size: int
) -> dict:
    lower, upper = utc_bounds(start, end)
    visible = _visible(user, read_all)
    filtered = [*visible, TicketCallback.occurred_at >= lower, TicketCallback.occurred_at < upper]
    total = (
        db.scalar(
            select(func.count(TicketCallback.id))
            .join(Ticket, Ticket.id == TicketCallback.ticket_id)
            .where(*filtered)
        )
        or 0
    )
    unique_tickets = (
        db.scalar(
            select(func.count(func.distinct(TicketCallback.ticket_id)))
            .join(Ticket, Ticket.id == TicketCallback.ticket_id)
            .where(*filtered)
        )
        or 0
    )
    outcomes = dict.fromkeys(CALLBACK_OUTCOMES, 0)
    outcomes.update(
        db.execute(
            select(TicketCallback.outcome, func.count(TicketCallback.id))
            .join(Ticket, Ticket.id == TicketCallback.ticket_id)
            .where(*filtered)
            .group_by(TicketCallback.outcome)
        ).all()
    )
    day = _day(TicketCallback.occurred_at)
    daily = [
        {"date": date_value, "outcome": outcome, "count": count}
        for date_value, outcome, count in db.execute(
            select(day, TicketCallback.outcome, func.count(TicketCallback.id))
            .join(Ticket, Ticket.id == TicketCallback.ticket_id)
            .where(*filtered)
            .group_by(day, TicketCallback.outcome)
            .order_by(day)
        )
    ]
    actors = [
        {"user_id": actor_id, "display_name": name, "outcome": outcome, "count": count}
        for actor_id, name, outcome, count in db.execute(
            select(
                User.id, User.display_name, TicketCallback.outcome, func.count(TicketCallback.id)
            )
            .select_from(TicketCallback)
            .join(Ticket, Ticket.id == TicketCallback.ticket_id)
            .join(User, User.id == TicketCallback.actor_id)
            .where(*filtered)
            .group_by(User.id, User.display_name, TicketCallback.outcome)
            .order_by(User.display_name)
        )
    ]
    detail_rows = db.execute(
        select(
            TicketCallback.id,
            Ticket.id,
            Ticket.number,
            Ticket.subject,
            TicketCallback.outcome,
            TicketCallback.occurred_at,
            User.id,
            User.display_name,
        )
        .select_from(TicketCallback)
        .join(Ticket, Ticket.id == TicketCallback.ticket_id)
        .join(User, User.id == TicketCallback.actor_id)
        .where(*filtered)
        .order_by(TicketCallback.occurred_at.desc(), TicketCallback.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    details = [
        {
            "id": row[0],
            "ticket_id": row[1],
            "ticket_number": row[2],
            "subject": row[3],
            "outcome": row[4],
            "occurred_at": row[5],
            "actor_id": row[6],
            "actor_name": row[7],
        }
        for row in detail_rows
    ]
    return {
        "start": start,
        "end": end,
        "total_events": total,
        "unique_tickets": unique_tickets,
        "outcomes": outcomes,
        "daily": daily,
        "employees": actors,
        "details": {"items": details, "page": page, "size": size, "total": total},
    }


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
