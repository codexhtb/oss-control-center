"""Exact subscriber identity checks for active linked tickets."""

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.modules.tickets.models import SubscriberSnapshot, Ticket
from app.modules.tickets.status import TicketStatus, TicketType


def _conditions(snapshot: SubscriberSnapshot):
    identity = (
        SubscriberSnapshot.subscriber_id == snapshot.subscriber_id
        if snapshot.subscriber_id
        else and_(
            (SubscriberSnapshot.subscriber_id.is_(None) | (SubscriberSnapshot.subscriber_id == "")),
            SubscriberSnapshot.account_no == snapshot.account_no,
        )
    )
    return (
        Ticket.ticket_type == TicketType.SUBSCRIBER_LINKED,
        Ticket.deleted_at.is_(None),
        Ticket.status.in_((TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED)),
        SubscriberSnapshot.source == snapshot.source,
        identity,
    )


def active_query(snapshot: SubscriberSnapshot):
    return (
        select(Ticket)
        .join(SubscriberSnapshot, Ticket.subscriber_snapshot_id == SubscriberSnapshot.id)
        .where(*_conditions(snapshot))
    )


def active_count(db: Session, snapshot: SubscriberSnapshot) -> int:
    return db.scalar(select(func.count()).select_from(active_query(snapshot).subquery())) or 0


def lock_subscriber(db: Session, snapshot: SubscriberSnapshot) -> None:
    """Serialize creation for this account and for all accounts of the same subscriber."""
    keys = [f"{snapshot.source}:account:{snapshot.account_no}"]
    if snapshot.subscriber_id:
        keys.append(f"{snapshot.source}:subscriber:{snapshot.subscriber_id}")
    for key in sorted(keys):
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
