"""Read-only field dispatch and historical reason reports."""

from datetime import date

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.modules.field_dispatch.models import FieldWorkHandoff, FieldWorkReschedule
from app.modules.identity.models import User
from app.modules.reports.service import _day, _visible, utc_bounds
from app.modules.tickets.models import Ticket, TicketEvent, TicketResolution


def initial_category_expression():
    """Return the category snapshot captured when the ticket was registered."""
    category_change = (
        select(TicketEvent.id)
        .where(
            TicketEvent.ticket_id == Ticket.id,
            TicketEvent.event_type == "ticket_edited",
            TicketEvent.event_data["changes"].has_key("category_name"),
        )  # noqa: W601
        .order_by(TicketEvent.occurred_at, TicketEvent.id)
        .limit(1)
        .correlate(Ticket)
    )
    first_category = (
        select(TicketEvent.event_data["changes"]["category_name"]["before"].astext)
        .where(
            TicketEvent.ticket_id == Ticket.id,
            TicketEvent.event_type == "ticket_edited",
            TicketEvent.event_data["changes"].has_key("category_name"),
        )  # noqa: W601
        .order_by(TicketEvent.occurred_at, TicketEvent.id)
        .limit(1)
        .correlate(Ticket)
        .scalar_subquery()
    )
    return case((category_change.exists(), first_category), else_=Ticket.category_name)


def field_work_report(
    db: Session,
    *,
    start: date,
    end: date,
    user: User,
    read_all: bool,
    direction: str | None,
    page: int,
    size: int,
) -> dict:
    lower, upper = utc_bounds(start, end)
    visible = _visible(user, read_all)
    scope = [*visible, *([FieldWorkHandoff.direction == direction] if direction else [])]
    sent = and_(FieldWorkHandoff.sent_at >= lower, FieldWorkHandoff.sent_at < upper)
    reported = and_(FieldWorkHandoff.reported_at >= lower, FieldWorkHandoff.reported_at < upper)
    verified = and_(FieldWorkHandoff.verified_at >= lower, FieldWorkHandoff.verified_at < upper)
    rescheduled = exists(
        select(FieldWorkReschedule.id).where(
            FieldWorkReschedule.handoff_id == FieldWorkHandoff.id,
            FieldWorkReschedule.created_at >= lower,
            FieldWorkReschedule.created_at < upper,
        )
    )
    event_scope = [*scope, or_(sent, reported, verified, rescheduled)]

    def handoff_query(*columns):
        return select(*columns).select_from(FieldWorkHandoff).join(Ticket).where(*scope)

    def reschedule_query(*columns):
        return (
            select(*columns)
            .select_from(FieldWorkReschedule)
            .join(FieldWorkHandoff)
            .join(Ticket)
            .where(
                *scope,
                FieldWorkReschedule.created_at >= lower,
                FieldWorkReschedule.created_at < upper,
            )
        )

    metrics = {}
    for name, condition in (
        ("sent_handoffs", sent),
        ("reported_handoffs", reported),
        ("verified_handoffs", verified),
    ):
        metrics[name] = (
            db.scalar(handoff_query(func.count(FieldWorkHandoff.id)).where(condition)) or 0
        )
    metrics["sent_unique_tickets"] = (
        db.scalar(handoff_query(func.count(func.distinct(FieldWorkHandoff.ticket_id))).where(sent))
        or 0
    )
    metrics["reschedule_events"] = (
        db.scalar(reschedule_query(func.count(FieldWorkReschedule.id))) or 0
    )
    metrics["rescheduled_handoffs"] = (
        db.scalar(reschedule_query(func.count(func.distinct(FieldWorkReschedule.handoff_id)))) or 0
    )
    metrics["awaiting_report_now"] = (
        db.scalar(
            handoff_query(func.count(FieldWorkHandoff.id)).where(
                FieldWorkHandoff.sent_at.is_not(None), FieldWorkHandoff.reported_at.is_(None)
            )
        )
        or 0
    )
    metrics["date_pending_now"] = (
        db.scalar(
            handoff_query(func.count(FieldWorkHandoff.id)).where(
                FieldWorkHandoff.sent_at.is_not(None),
                FieldWorkHandoff.schedule_status == "DATE_PENDING",
            )
        )
        or 0
    )

    directions = {}
    for name, condition in (("sent", sent), ("reported", reported), ("verified", verified)):
        for key, count in db.execute(
            handoff_query(FieldWorkHandoff.direction, func.count(FieldWorkHandoff.id))
            .where(condition)
            .group_by(FieldWorkHandoff.direction)
        ):
            directions.setdefault(
                key or "UNKNOWN", {"sent": 0, "reported": 0, "verified": 0, "reschedules": 0}
            )[name] = count
    for key, count in db.execute(
        reschedule_query(FieldWorkHandoff.direction, func.count(FieldWorkReschedule.id)).group_by(
            FieldWorkHandoff.direction
        )
    ):
        directions.setdefault(
            key or "UNKNOWN", {"sent": 0, "reported": 0, "verified": 0, "reschedules": 0}
        )["reschedules"] = count

    daily = {}
    for name, column in (
        ("sent", FieldWorkHandoff.sent_at),
        ("reported", FieldWorkHandoff.reported_at),
        ("verified", FieldWorkHandoff.verified_at),
    ):
        day = _day(column)
        for day_value, count in db.execute(
            handoff_query(day, func.count(FieldWorkHandoff.id))
            .where(column >= lower, column < upper)
            .group_by(day)
        ):
            daily.setdefault(
                day_value,
                {"date": day_value, "sent": 0, "reported": 0, "verified": 0, "reschedules": 0},
            )[name] = count
    day = _day(FieldWorkReschedule.created_at)
    for day_value, count in db.execute(
        reschedule_query(day, func.count(FieldWorkReschedule.id)).group_by(day)
    ):
        daily.setdefault(
            day_value,
            {"date": day_value, "sent": 0, "reported": 0, "verified": 0, "reschedules": 0},
        )["reschedules"] = count

    reasons = [
        {"reason": reason, "count": count}
        for reason, count in db.execute(
            reschedule_query(FieldWorkReschedule.reason, func.count(FieldWorkReschedule.id))
            .group_by(FieldWorkReschedule.reason)
            .order_by(func.count(FieldWorkReschedule.id).desc(), FieldWorkReschedule.reason)
            .limit(10)
        )
    ]
    total = (
        db.scalar(
            handoff_query(func.count(FieldWorkHandoff.id)).where(
                or_(sent, reported, verified, rescheduled)
            )
        )
        or 0
    )
    rows = db.execute(
        select(
            FieldWorkHandoff.id,
            Ticket.id,
            Ticket.number,
            Ticket.subject,
            FieldWorkHandoff.direction,
            FieldWorkHandoff.work_status,
            FieldWorkHandoff.schedule_status,
            FieldWorkHandoff.sent_at,
            FieldWorkHandoff.reported_at,
            FieldWorkHandoff.verified_at,
        )
        .select_from(FieldWorkHandoff)
        .join(Ticket)
        .where(*event_scope)
        .order_by(FieldWorkHandoff.created_at.desc(), FieldWorkHandoff.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    return {
        "start": start,
        "end": end,
        "metrics": metrics,
        "directions": directions,
        "daily": sorted(daily.values(), key=lambda item: item["date"]),
        "reschedule_reasons": reasons,
        "details": {
            "items": [
                dict(
                    zip(
                        (
                            "id",
                            "ticket_id",
                            "ticket_number",
                            "subject",
                            "direction",
                            "work_status",
                            "schedule_status",
                            "sent_at",
                            "reported_at",
                            "verified_at",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in rows
            ],
            "page": page,
            "size": size,
            "total": total,
        },
    }


def reasons_report(
    db: Session,
    *,
    start: date,
    end: date,
    user: User,
    read_all: bool,
    category: str | None,
    detail_kind: str,
    page: int,
    size: int,
) -> dict:
    lower, upper = utc_bounds(start, end)
    visible = _visible(user, read_all)
    # Ticket edits can change category_name. The first edit's "before" snapshot
    # retains the registration value, including an originally absent category.
    initial_category = initial_category_expression()
    scope = [*visible, *([initial_category == category] if category else [])]
    ticket_scope = [*scope, Ticket.created_at >= lower, Ticket.created_at < upper]
    decision_scope = [
        *scope,
        TicketResolution.created_at >= lower,
        TicketResolution.created_at < upper,
    ]
    categories = [
        {"name": name, "count": count}
        for name, count in db.execute(
            select(initial_category, func.count(Ticket.id))
            .where(*ticket_scope)
            .group_by(initial_category)
            .order_by(func.count(Ticket.id).desc(), initial_category)
            .limit(20)
        )
    ]
    decisions = (
        select(TicketResolution.reason_name, func.count(TicketResolution.id))
        .join(Ticket, Ticket.id == TicketResolution.ticket_id)
        .where(*decision_scope)
    )
    reasons = [
        {"name": name, "count": count}
        for name, count in db.execute(
            decisions.group_by(TicketResolution.reason_name)
            .order_by(func.count(TicketResolution.id).desc(), TicketResolution.reason_name)
            .limit(20)
        )
    ]
    day = _day(Ticket.created_at)
    daily = [
        {"date": day_value, "category": name, "count": count}
        for day_value, name, count in db.execute(
            select(day, initial_category, func.count(Ticket.id))
            .where(*ticket_scope)
            .group_by(day, initial_category)
            .order_by(day)
        )
    ]
    created = db.scalar(select(func.count(Ticket.id)).where(*ticket_scope)) or 0
    without_category = (
        db.scalar(select(func.count(Ticket.id)).where(*ticket_scope, initial_category.is_(None)))
        or 0
    )
    decision_events = db.scalar(decisions.with_only_columns(func.count(TicketResolution.id))) or 0
    decision_tickets = (
        db.scalar(
            decisions.with_only_columns(func.count(func.distinct(TicketResolution.ticket_id)))
        )
        or 0
    )
    without_reason = (
        db.scalar(
            decisions.with_only_columns(func.count(TicketResolution.id)).where(
                TicketResolution.reason_name.is_(None)
            )
        )
        or 0
    )
    # This is a current snapshot of the created cohort, distinct from decision events.
    created_without_reason = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                *ticket_scope,
                ~exists(
                    select(TicketResolution.id).where(
                        TicketResolution.ticket_id == Ticket.id,
                        TicketResolution.reason_name.is_not(None),
                    )
                ),
            )
        )
        or 0
    )

    if detail_kind == "category":
        total = created
        rows = db.execute(
            select(
                Ticket.id,
                Ticket.id,
                Ticket.number,
                Ticket.subject,
                initial_category,
                Ticket.created_at,
            )
            .where(*ticket_scope)
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        )
    else:
        total = decision_events
        rows = db.execute(
            select(
                TicketResolution.id,
                Ticket.id,
                Ticket.number,
                Ticket.subject,
                TicketResolution.reason_name,
                TicketResolution.created_at,
            )
            .join(Ticket, Ticket.id == TicketResolution.ticket_id)
            .where(*decision_scope)
            .order_by(TicketResolution.created_at.desc(), TicketResolution.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        )
    return {
        "start": start,
        "end": end,
        "metrics": {
            "created_tickets": created,
            "without_category": without_category,
            "decision_events": decision_events,
            "decision_unique_tickets": decision_tickets,
            "decisions_without_reason": without_reason,
            "created_without_reason_now": created_without_reason,
        },
        "categories": categories,
        "reasons": reasons,
        "daily": daily,
        "details": {
            "items": [
                dict(
                    zip(
                        ("id", "ticket_id", "ticket_number", "subject", "name", "occurred_at"),
                        row,
                        strict=True,
                    )
                )
                for row in rows
            ],
            "page": page,
            "size": size,
            "total": total,
        },
    }
