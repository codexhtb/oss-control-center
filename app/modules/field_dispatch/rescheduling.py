"""Visit schedule transitions, independent from Service Desk and work completion."""

import re
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.models import (
    FieldWorkCompletionPrompt,
    FieldWorkHandoff,
    FieldWorkReschedule,
)
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent
from app.modules.tickets.status import TicketStatus

ABKHAZIA = timezone(timedelta(hours=3))
DATE_PATTERN = re.compile(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}\Z")


def parse_telegram_reschedule(value: str) -> tuple[str, datetime | None]:
    lines = [line.strip() for line in value.strip().splitlines()]
    if len(lines) != 2 or not lines[0].startswith("Причина: ") or not lines[1].startswith("Дата: "):
        raise ValueError("reschedule_format_invalid")
    reason = lines[0][len("Причина: "):].strip()
    date_text = lines[1][len("Дата: "):].strip()
    if not reason or len(reason) > 2000 or any(ord(char) < 32 for char in reason):
        raise ValueError("reschedule_reason_invalid")
    if date_text.casefold() == "не согласована":
        return reason, None
    if not DATE_PATTERN.fullmatch(date_text):
        raise ValueError("reschedule_date_invalid")
    try:
        local = datetime.strptime(date_text, "%d.%m.%Y %H:%M").replace(tzinfo=ABKHAZIA)
    except ValueError:
        raise ValueError("reschedule_date_invalid") from None
    if local.astimezone(UTC) <= datetime.now(UTC):
        raise ValueError("reschedule_date_past")
    return reason, local.astimezone(UTC)


def record_reschedule(
    db: Session,
    handoff: FieldWorkHandoff,
    *,
    reason: str,
    scheduled_for: datetime | None,
    scheduled_date: date | None = None,
    source: str,
    author_name: str,
    actor_id: UUID | None = None,
    telegram_user_id: int | None = None,
    telegram_chat_id: str | None = None,
    telegram_message_id: int | None = None,
    update_record_id: UUID | None = None,
    expected_version: int | None = None,
) -> FieldWorkReschedule:
    if handoff.delivery_status != "SENT" or handoff.work_status != "AWAITING_REPORT":
        raise ApiError(409, "field_work_already_completed", "Field work cannot be rescheduled")
    ticket = db.get(Ticket, handoff.ticket_id)
    if ticket is None or ticket.deleted_at is not None or ticket.status != TicketStatus.IN_PROGRESS:
        raise ApiError(409, "field_ticket_not_in_progress", "Ticket is not in progress")
    if expected_version is not None and handoff.schedule_version != expected_version:
        raise ApiError(409, "field_schedule_conflict", "Visit schedule has changed")
    if not reason.strip() or len(reason) > 2000 or any(ord(char) < 32 for char in reason):
        raise ApiError(422, "field_schedule_reason_invalid", "A valid reason is required")
    if scheduled_for is not None:
        if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
            raise ApiError(422, "field_schedule_date_invalid", "Date must include timezone")
        if scheduled_for.astimezone(UTC) <= datetime.now(UTC):
            raise ApiError(422, "field_schedule_date_past", "Date must be in the future")
        scheduled_date = scheduled_for.astimezone(ABKHAZIA).date()
    elif scheduled_date is not None and scheduled_date <= datetime.now(ABKHAZIA).date():
        raise ApiError(422, "field_schedule_date_past", "Date must be in the future")
    now = datetime.now(UTC)
    handoff.schedule_version += 1
    handoff.schedule_status = "SCHEDULED" if scheduled_date is not None else "DATE_PENDING"
    handoff.scheduled_for = scheduled_for.astimezone(UTC) if scheduled_for else None
    handoff.scheduled_date = scheduled_date
    handoff.schedule_card_status = "PENDING"
    handoff.schedule_card_attempts = 0
    handoff.schedule_card_error_code = None
    handoff.updated_at = now
    item = FieldWorkReschedule(
        handoff_id=handoff.id,
        schedule_version=handoff.schedule_version,
        reason=reason.strip(),
        scheduled_for=handoff.scheduled_for,
        scheduled_date=scheduled_date,
        source=source,
        author_name=author_name,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        update_record_id=update_record_id,
    )
    db.add(item)
    db.add(TicketEvent(
        ticket_id=handoff.ticket_id, event_type="field_work_rescheduled",
        actor_id=actor_id,
        event_data={"handoff_id": str(handoff.id), "schedule_version": handoff.schedule_version,
                    "schedule_status": handoff.schedule_status},
        ticket_version=ticket.version,
    ))
    db.add(TicketAudit(
        ticket_id=handoff.ticket_id, actor_id=actor_id,
        action="field_work_rescheduled", outcome="success",
        origin="API" if source == "OSS" else "SYSTEM",
    ))
    return item


def active_prompt(db: Session, handoff_id: UUID) -> FieldWorkCompletionPrompt | None:
    return db.scalar(select(FieldWorkCompletionPrompt).where(
        FieldWorkCompletionPrompt.handoff_id == handoff_id,
        FieldWorkCompletionPrompt.status.in_(("REQUESTING", "WAITING", "UNCERTAIN")),
    ))
