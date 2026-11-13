"""Transactional preparation and confirmed outbound Telegram delivery."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unicodedata import category
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.binding import destination_chat_id, mark_bot_unavailable
from app.modules.field_dispatch.directions import (
    Direction,
    destination_for_direction,
    direction_title,
)
from app.modules.field_dispatch.gateway import (
    DispatchReceipt,
    DispatchRejected,
    DispatchUncertain,
    FieldDispatchGateway,
)
from app.modules.field_dispatch.models import (
    FieldExternalMessage,
    FieldWorkCompletionPrompt,
    FieldWorkDeliveryAttempt,
    FieldWorkHandoff,
    FieldWorkReschedule,
)
from app.modules.field_dispatch.reschedule_dialog import active_dialog
from app.modules.field_dispatch.rescheduling import active_prompt, record_reschedule
from app.modules.field_dispatch.schemas import FieldWorkHandoffView
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission
from app.modules.tickets.models import SubscriberSnapshot, Ticket, TicketAudit, TicketEvent
from app.modules.tickets.status import TicketStatus, TicketType


def _actor(db: Session, actor_id: UUID) -> User:
    actor = db.execute(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        not actor
        or not actor.is_active
        or not has_permission(db, actor, Permission.TICKET_FIELD_WORK)
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    return actor


def _address(address: str | None, house: str | None) -> str | None:
    base = address.strip() if address else ""
    number = house.strip() if house else ""
    if not base:
        return None
    if not number:
        return base
    escaped = re.escape(number)
    if re.search(
        rf"(?:^|[,;\s])(?:д(?:ом)?\.?|строение|стр\.?)\s*{escaped}(?:$|[,;\s])", base, re.I
    ):
        return base
    if re.search(rf"(?:^|[,;\s]){escaped}\s*$", base):
        return base
    return f"{base.rstrip(' ,.')}, д. {number}"


@dataclass(frozen=True)
class MessageData:
    text: str | None
    missing: tuple[str, ...]


def _message(db: Session, handoff: FieldWorkHandoff, sender: User) -> MessageData:
    ticket = db.get(Ticket, handoff.ticket_id)
    if ticket is None or ticket.deleted_at is not None:
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    if ticket.ticket_type == TicketType.MANUAL:
        values = {
            "caller_full_name": ticket.caller_full_name,
            "caller_phone": ticket.caller_phone,
            "city": ticket.city,
        }
        missing = tuple(key for key, value in values.items() if not value)
        if missing:
            return MessageData(None, missing)
        engineer = db.get(User, handoff.created_by) or sender
        lines = [
            f"Заявка #{ticket.number}",
            f"Тема: {ticket.subject or 'Ручное обращение'}",
        ]
        if ticket.category_name:
            lines.append(f"Категория: {ticket.category_name}")
        lines.extend((
            f"Обратившийся: {ticket.caller_full_name}",
            f"Контактный номер: {ticket.caller_phone}",
            f"Город: {ticket.city}",
            f"Адрес: {ticket.address_override or handoff.visit_address or 'не указан'}",
            f"Причина передачи: {handoff.reason}",
            f"Отправил инженер: {engineer.display_name}",
        ))
        text = "\n".join(lines)
        if len(text) > 4096:
            raise ApiError(422, "telegram_message_too_long", "Telegram message is too long")
        return MessageData(text, ())
    snapshot = (
        db.get(SubscriberSnapshot, ticket.subscriber_snapshot_id)
        if ticket.subscriber_snapshot_id is not None
        else None
    )
    name = None if snapshot is None else (snapshot.full_name or snapshot.organization_name)
    contact = ticket.caller_phone or (snapshot.phone if snapshot is not None else None)
    contact_label = "Телефон звонящего" if ticket.caller_phone else "Контакт NetUP"
    account = ticket.account_no
    source_address = (
        None if snapshot is None else _address(snapshot.actual_address, snapshot.house_number)
    )
    address = ticket.address_override or source_address or handoff.visit_address or "не указан"
    values = {"subscriber": name, "contact": contact, "account": account}
    missing = tuple(key for key, value in values.items() if not value)
    if missing:
        return MessageData(None, missing)
    text = "\n".join(
        (
            f"Заявка #{ticket.number}",
            f"Абонент: {name}",
            f"{contact_label}: {contact}",
            f"Лицевой счёт: {account}",
            f"Город: {ticket.city or 'не указан'}",
            f"Адрес: {address}",
            f"Причина передачи: {handoff.reason}",
            f"Отправил инженер: {sender.display_name}",
        )
    )
    if len(text) > 4096:
        raise ApiError(422, "telegram_message_too_long", "Telegram message is too long")
    return MessageData(text, ())


def create_handoff(db: Session, ticket: Ticket, actor_id: UUID, reason: str) -> FieldWorkHandoff:
    try:
        initial_chat_id = destination_chat_id(db)
    except ApiError as exc:
        if exc.code not in {"telegram_group_not_bound", "telegram_bot_not_in_bound_group"}:
            raise
        initial_chat_id = destination_for_direction(db, "OCHAMCHIRA")
    handoff = FieldWorkHandoff(
        ticket_id=ticket.id,
        ticket_version=ticket.version,
        reason=reason,
        chat_id=initial_chat_id,
        created_by=actor_id,
    )
    db.add(handoff)
    db.flush()
    return handoff


def latest_handoff(db: Session, ticket_id: UUID) -> FieldWorkHandoff | None:
    return db.scalar(
        select(FieldWorkHandoff)
        .where(FieldWorkHandoff.ticket_id == ticket_id)
        .order_by(FieldWorkHandoff.created_at.desc(), FieldWorkHandoff.id.desc())
        .limit(1)
    )


def handoff_view(db: Session, handoff: FieldWorkHandoff, viewer: User) -> FieldWorkHandoffView:
    data = _message(db, handoff, viewer)
    sender_name = (
        db.scalar(select(User.display_name).where(User.id == handoff.sent_by))
        if handoff.sent_by
        else None
    )
    verified_by_name = (
        db.scalar(select(User.display_name).where(User.id == handoff.verified_by))
        if handoff.verified_by
        else None
    )
    completion_prompt = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(
            FieldWorkCompletionPrompt.handoff_id == handoff.id,
            FieldWorkCompletionPrompt.prompt_type == "COMPLETION",
        )
        .order_by(FieldWorkCompletionPrompt.created_at.desc(), FieldWorkCompletionPrompt.id.desc())
        .limit(1)
    )
    latest_schedule = db.scalar(select(FieldWorkReschedule).where(
        FieldWorkReschedule.handoff_id == handoff.id
    ).order_by(FieldWorkReschedule.schedule_version.desc()).limit(1))
    return FieldWorkHandoffView.model_validate(handoff).model_copy(
        update={
            "message_preview": handoff.message_text or data.text,
            "group_name": direction_title(handoff.direction),
            "schedule_reason": latest_schedule.reason if latest_schedule else None,
            "scheduled_by_name": latest_schedule.author_name if latest_schedule else None,
            "schedule_changed_at": latest_schedule.created_at if latest_schedule else None,
            "generated_preview": data.text,
            "ready_to_send": not data.missing and handoff.delivery_status in {"DRAFT", "FAILED"},
            "missing_fields": list(data.missing),
            "sent_by_name": sender_name,
            "verified_by_name": verified_by_name,
            "completion_prompt_status": completion_prompt.status if completion_prompt else None,
            "completion_prompt_sent_at": completion_prompt.sent_at if completion_prompt else None,
        }
    )


@dataclass(frozen=True)
class PreparedSend:
    handoff_id: UUID
    attempt_id: UUID
    chat_id: str
    text: str
    completion_callback_data: str


def prepare_send(
    db: Session,
    actor_id: UUID,
    ticket_id: UUID,
    handoff_id: UUID,
    request_id: UUID,
    visit_address: str | None = None,
    update_visit_address: bool = False,
    message_text: str | None = None,
    direction: Direction | None = None,
) -> PreparedSend | FieldWorkHandoff:
    actor = _actor(db, actor_id)
    handoff = db.execute(
        select(FieldWorkHandoff)
        .where(FieldWorkHandoff.id == handoff_id, FieldWorkHandoff.ticket_id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if handoff is None:
        raise ApiError(404, "field_handoff_not_found", "Field work handoff not found")
    ticket = db.execute(
        select(Ticket).where(Ticket.id == ticket_id).with_for_update()
    ).scalar_one_or_none()
    if ticket is None or ticket.deleted_at is not None:
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise ApiError(409, "invalid_transition", "Field work can be sent only in progress")
    prior = db.scalar(
        select(FieldWorkDeliveryAttempt).where(
            FieldWorkDeliveryAttempt.handoff_id == handoff.id,
            FieldWorkDeliveryAttempt.client_request_id == request_id,
        )
    )
    if prior is not None:
        if direction is not None and direction != handoff.direction:
            raise ApiError(
                409, "field_send_request_conflict", "Request destination differs from prior send"
            )
        if message_text is not None and message_text != handoff.message_text:
            raise ApiError(
                409, "field_send_request_conflict", "Request text differs from prior send"
            )
        if prior.status == "SENDING":
            raise ApiError(409, "field_send_in_progress", "Telegram delivery is in progress")
        return handoff
    if handoff.delivery_status == "SENT":
        if direction is not None and direction != handoff.direction:
            raise ApiError(409, "field_send_locked", "Sent destination cannot be changed")
        if message_text is not None and message_text != handoff.message_text:
            raise ApiError(409, "field_send_locked", "Sent text cannot be changed")
        return handoff
    if handoff.delivery_status == "SENDING":
        raise ApiError(409, "field_send_in_progress", "Telegram delivery is in progress")
    if handoff.delivery_status == "UNCERTAIN":
        raise ApiError(409, "field_send_uncertain", "Telegram delivery outcome is uncertain")
    if direction is None:
        raise ApiError(422, "field_direction_required", "Select a field work destination")
    if handoff.direction is not None and handoff.direction != direction:
        raise ApiError(409, "field_direction_locked", "Field work destination cannot be changed")
    snapshot = (
        db.get(SubscriberSnapshot, ticket.subscriber_snapshot_id)
        if ticket.subscriber_snapshot_id is not None
        else None
    )
    if update_visit_address:
        if visit_address is not None and (
            ticket.address_override
            or (snapshot is not None and _address(snapshot.actual_address, snapshot.house_number))
        ):
            raise ApiError(422, "field_address_from_snapshot", "Address is already in snapshot")
        handoff.visit_address = visit_address
    handoff.chat_id = destination_for_direction(db, direction)
    handoff.direction = direction
    message = _message(db, handoff, actor)
    if message.missing:
        raise ApiError(409, "field_dispatch_incomplete", "Required dispatch data is missing")
    text = message_text if message_text is not None else message.text
    if not text or not text.strip() or len(text) > 4096 or any(
        category(char) == "Cc" and char != "\n" for char in text
    ):
        raise ApiError(422, "field_message_invalid", "Telegram message must contain valid text")
    now = datetime.now(UTC)
    attempt = FieldWorkDeliveryAttempt(
        handoff_id=handoff.id,
        client_request_id=request_id,
        actor_id=actor_id,
        status="SENDING",
        started_at=now,
    )
    db.add(attempt)
    handoff.delivery_status = "SENDING"
    handoff.message_text = text
    handoff.sent_by = actor_id
    handoff.send_started_at = now
    handoff.last_error_code = None
    handoff.updated_at = now
    db.add(
        TicketAudit(
            ticket_id=ticket_id,
            actor_id=actor_id,
            action="field_send_started",
            outcome="pending",
            origin="API",
        )
    )
    db.commit()
    return PreparedSend(
        handoff.id,
        attempt.id,
        handoff.chat_id,
        text,
        f"field_done:{handoff.id}",
    )


def finalize_send(
    db: Session,
    prepared: PreparedSend,
    *,
    receipt: DispatchReceipt | None = None,
    error_code: str | None = None,
    uncertain: bool = False,
) -> FieldWorkHandoff:
    handoff = db.execute(
        select(FieldWorkHandoff).where(FieldWorkHandoff.id == prepared.handoff_id).with_for_update()
    ).scalar_one()
    attempt = db.execute(
        select(FieldWorkDeliveryAttempt)
        .where(FieldWorkDeliveryAttempt.id == prepared.attempt_id)
        .with_for_update()
    ).scalar_one()
    now = datetime.now(UTC)
    if receipt is not None:
        handoff.delivery_status = attempt.status = "SENT"
        handoff.telegram_message_id = attempt.telegram_message_id = int(receipt.message_id)
        handoff.sent_at = now
        audit_outcome = "success"
        event_type = "field_work_sent"
    else:
        status = "UNCERTAIN" if uncertain else "FAILED"
        handoff.delivery_status = attempt.status = status
        handoff.last_error_code = attempt.error_code = error_code or "telegram_delivery_failed"
        audit_outcome = status.lower()
        event_type = "field_work_delivery_uncertain" if uncertain else "field_work_delivery_failed"
    attempt.completed_at = now
    handoff.updated_at = now
    ticket = db.get(Ticket, handoff.ticket_id)
    db.add(
        TicketEvent(
            ticket_id=handoff.ticket_id,
            event_type=event_type,
            actor_id=handoff.sent_by,
            event_data={"handoff_id": str(handoff.id), "delivery_status": handoff.delivery_status},
            ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
        )
    )
    db.add(
        TicketAudit(
            ticket_id=handoff.ticket_id,
            actor_id=handoff.sent_by,
            action=event_type,
            outcome=audit_outcome,
            origin="API",
        )
    )
    db.commit()
    db.refresh(handoff)
    return handoff


def send_handoff(
    db: Session,
    gateway: FieldDispatchGateway,
    actor_id: UUID,
    ticket_id: UUID,
    handoff_id: UUID,
    request_id: UUID,
    visit_address: str | None = None,
    update_visit_address: bool = False,
    message_text: str | None = None,
    direction: Direction | None = None,
) -> FieldWorkHandoff:
    prepared = prepare_send(
        db, actor_id, ticket_id, handoff_id, request_id, visit_address,
        update_visit_address, message_text, direction,
    )
    if isinstance(prepared, FieldWorkHandoff):
        return prepared
    try:
        receipt = gateway.send_message(
            prepared.chat_id,
            prepared.text,
            completion_callback_data=prepared.completion_callback_data,
        )
    except DispatchRejected as exc:
        result = finalize_send(db, prepared, error_code=exc.code)
        if exc.code.endswith("403"):
            mark_bot_unavailable(db, prepared.chat_id)
        return result
    except DispatchUncertain:
        return finalize_send(
            db, prepared, error_code="telegram_transport_uncertain", uncertain=True
        )
    return finalize_send(db, prepared, receipt=receipt)


def confirm_absent_delivery(
    db: Session, actor_id: UUID, ticket_id: UUID, handoff_id: UUID
) -> FieldWorkHandoff:
    """Unlock an uncertain send only after a human checked the destination group.

    The original attempt remains UNCERTAIN as evidence. This never sends a message.
    """
    _actor(db, actor_id)
    handoff = db.execute(
        select(FieldWorkHandoff)
        .where(FieldWorkHandoff.id == handoff_id, FieldWorkHandoff.ticket_id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    ticket = db.execute(
        select(Ticket).where(Ticket.id == ticket_id).with_for_update()
    ).scalar_one_or_none()
    if handoff is None or ticket is None or ticket.deleted_at is not None:
        raise ApiError(404, "field_handoff_not_found", "Field work handoff not found")
    if (
        handoff.delivery_status == "FAILED"
        and handoff.last_error_code == "telegram_absence_confirmed"
    ):
        return handoff
    if handoff.delivery_status != "UNCERTAIN" or handoff.telegram_message_id is not None:
        raise ApiError(409, "field_recovery_state_changed", "Delivery state changed")
    if ticket.status != TicketStatus.IN_PROGRESS or handoff.work_status != "AWAITING_REPORT":
        raise ApiError(409, "invalid_transition", "Field work cannot be retried")
    if (
        handoff.send_started_at is None
        or datetime.now(UTC) - handoff.send_started_at < timedelta(minutes=2)
    ):
        raise ApiError(409, "field_recovery_too_early", "Wait before checking the group")
    if (
        handoff.direction is None
        or destination_for_direction(db, handoff.direction) != handoff.chat_id
    ):
        raise ApiError(409, "field_destination_changed", "Destination requires review")
    latest_attempt = db.scalar(
        select(FieldWorkDeliveryAttempt)
        .where(FieldWorkDeliveryAttempt.handoff_id == handoff.id)
        .order_by(FieldWorkDeliveryAttempt.started_at.desc(), FieldWorkDeliveryAttempt.id.desc())
        .limit(1)
    )
    if latest_attempt is None or latest_attempt.status != "UNCERTAIN":
        raise ApiError(409, "field_recovery_state_changed", "Delivery state changed")
    handoff.delivery_status = "FAILED"
    handoff.last_error_code = "telegram_absence_confirmed"
    handoff.updated_at = datetime.now(UTC)
    db.add(TicketEvent(
        ticket_id=ticket_id, event_type="field_work_delivery_absence_confirmed",
        actor_id=actor_id,
        event_data={"handoff_id": str(handoff.id), "delivery_status": "FAILED"},
        ticket_version=ticket.version,
    ))
    db.add(TicketAudit(
        ticket_id=ticket_id, actor_id=actor_id,
        action="field_work_delivery_absence_confirmed", outcome="success", origin="API",
    ))
    db.commit()
    db.refresh(handoff)
    return handoff


def verify_handoff(
    db: Session, actor_id: UUID, ticket_id: UUID, handoff_id: UUID
) -> FieldWorkHandoff:
    actor = _actor(db, actor_id)
    handoff = db.execute(
        select(FieldWorkHandoff)
        .where(FieldWorkHandoff.id == handoff_id, FieldWorkHandoff.ticket_id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if handoff is None:
        raise ApiError(404, "field_handoff_not_found", "Field work handoff not found")
    if handoff.work_status == "VERIFIED":
        return handoff
    if handoff.work_status != "REPORTED":
        raise ApiError(409, "field_work_not_reported", "Field work result is not reported")
    now = datetime.now(UTC)
    handoff.work_status = "VERIFIED"
    handoff.verified_by = actor.id
    handoff.verified_at = now
    handoff.updated_at = now
    ticket = db.get(Ticket, ticket_id)
    db.add(
        TicketEvent(
            ticket_id=ticket_id,
            event_type="field_work_verified",
            actor_id=actor.id,
            event_data={"handoff_id": str(handoff.id)},
            ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
        )
    )
    db.add(
        TicketAudit(
            ticket_id=ticket_id,
            actor_id=actor.id,
            action="field_work_verified",
            outcome="success",
            origin="API",
        )
    )
    db.commit()
    db.refresh(handoff)
    return handoff


def handoff_messages(
    db: Session, handoff_id: UUID, page: int, size: int
) -> tuple[list[FieldExternalMessage], int]:
    where = FieldExternalMessage.handoff_id == handoff_id
    total = db.scalar(select(func.count()).select_from(FieldExternalMessage).where(where)) or 0
    items = list(
        db.scalars(
            select(FieldExternalMessage)
            .where(where)
            .order_by(FieldExternalMessage.telegram_created_at.asc(), FieldExternalMessage.id.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    return items, total


def reschedule_handoff(
    db: Session, actor_id: UUID, ticket_id: UUID, handoff_id: UUID,
    reason: str, scheduled_for: datetime, expected_version: int,
) -> FieldWorkHandoff:
    actor = _actor(db, actor_id)
    handoff = db.scalar(select(FieldWorkHandoff).where(
        FieldWorkHandoff.id == handoff_id,
        FieldWorkHandoff.ticket_id == ticket_id,
    ).with_for_update())
    if handoff is None:
        raise ApiError(404, "field_handoff_not_found", "Field work handoff not found")
    if active_prompt(db, handoff.id) is not None or active_dialog(db, handoff.id) is not None:
        raise ApiError(409, "field_dialog_active", "A Telegram dialog is still active")
    record_reschedule(
        db, handoff, reason=reason, scheduled_for=scheduled_for,
        source="OSS", author_name=actor.display_name, actor_id=actor.id,
        expected_version=expected_version,
    )
    db.commit()
    db.refresh(handoff)
    return handoff


def handoff_reschedules(
    db: Session, handoff_id: UUID, page: int, size: int,
) -> tuple[list[FieldWorkReschedule], int]:
    where = FieldWorkReschedule.handoff_id == handoff_id
    total = db.scalar(select(func.count()).select_from(FieldWorkReschedule).where(where)) or 0
    items = list(db.scalars(select(FieldWorkReschedule).where(where)
        .order_by(FieldWorkReschedule.schedule_version.desc())
        .offset((page - 1) * size).limit(size)))
    return items, total
