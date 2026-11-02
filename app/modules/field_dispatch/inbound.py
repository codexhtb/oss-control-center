"""Verified, idempotent import of Telegram replies and completion reports."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.field_dispatch.binding import chat_id_aliases, current_binding
from app.modules.field_dispatch.models import (
    FieldDispatchUpdate,
    FieldExternalMessage,
    FieldWorkCompletionPrompt,
    FieldWorkHandoff,
)
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent

BOT_IDENTITY = "oss_field_service_bot"
COMPLETION_PREFIX = "field_done:"
ACTIVE_PROMPT_STATUSES = ("REQUESTING", "WAITING", "UNCERTAIN")


@dataclass(frozen=True)
class InboundResult:
    update_id: int
    outcome: str
    kind: str
    handoff_id: UUID | None = None
    callback_query_id: str | None = None
    sender_name: str | None = None
    duplicate: bool = False
    prompt_id: UUID | None = None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sender(value: object) -> tuple[int, str] | None:
    if not isinstance(value, dict) or value.get("is_bot") is True:
        return None
    sender_id = _integer(value.get("id"))
    if sender_id is None or sender_id <= 0:
        return None
    parts = [value.get("first_name"), value.get("last_name")]
    name = " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    if not name and isinstance(value.get("username"), str):
        name = f"@{value['username'].strip()}"
    name = " ".join(name.split())[:160]
    if not name or any(ord(char) < 32 for char in name):
        return None
    return sender_id, name


def _event_time(value: object) -> datetime:
    timestamp = _integer(value)
    try:
        return (
            datetime.fromtimestamp(timestamp, UTC) if timestamp is not None else datetime.now(UTC)
        )
    except (OverflowError, OSError, ValueError):
        return datetime.now(UTC)


def _receipt(
    db: Session,
    update_id: int,
    kind: str,
    outcome: str,
    *,
    handoff_id: UUID | None = None,
    error_code: str | None = None,
    sync_status: str = "NOT_REQUIRED",
    confirmation_status: str = "NOT_REQUIRED",
) -> tuple[FieldDispatchUpdate, bool]:
    statement = (
        insert(FieldDispatchUpdate)
        .values(
            id=func.gen_random_uuid(),
            bot_identity=BOT_IDENTITY,
            update_id=update_id,
            kind=kind,
            outcome=outcome,
            handoff_id=handoff_id,
            error_code=error_code,
            sync_status=sync_status,
            confirmation_status=confirmation_status,
        )
        .on_conflict_do_nothing(index_elements=["bot_identity", "update_id"])
        .returning(FieldDispatchUpdate.id)
    )
    receipt_id = db.scalar(statement)
    if receipt_id is None:
        existing = db.scalar(
            select(FieldDispatchUpdate).where(
                FieldDispatchUpdate.bot_identity == BOT_IDENTITY,
                FieldDispatchUpdate.update_id == update_id,
            )
        )
        if existing is None:
            raise RuntimeError("telegram_update_deduplication_failed")
        return existing, False
    receipt = db.get(FieldDispatchUpdate, receipt_id)
    if receipt is None:
        raise RuntimeError("telegram_update_receipt_missing")
    return receipt, True


def _ignored(db: Session, update_id: int, kind: str, outcome: str, code: str) -> InboundResult:
    receipt, created = _receipt(db, update_id, kind, outcome, error_code=code)
    db.commit()
    return InboundResult(
        update_id, "DUPLICATE" if not created else receipt.outcome, kind, duplicate=not created
    )


def _callback(
    db: Session,
    update_id: int,
    callback: dict[str, Any],
    source_message: dict[str, Any],
    source_message_id: int,
    sender_id: int,
    sender_name: str,
    accepted_chat_ids: set[str],
) -> InboundResult:
    kind = "COMPLETION_CALLBACK"
    callback_id = callback.get("id")
    callback_data = callback.get("data")
    if not isinstance(callback_data, str) or not callback_data.startswith(COMPLETION_PREFIX):
        return _ignored(db, update_id, kind, "IGNORED", "telegram_callback_unknown")
    try:
        requested_handoff = UUID(callback_data[len(COMPLETION_PREFIX) :])
    except (ValueError, TypeError):
        return _ignored(db, update_id, kind, "IGNORED", "telegram_callback_invalid")
    handoff = db.scalar(
        select(FieldWorkHandoff).where(FieldWorkHandoff.id == requested_handoff).with_for_update()
    )
    if (
        handoff is None
        or handoff.delivery_status != "SENT"
        or handoff.chat_id not in accepted_chat_ids
        or handoff.telegram_message_id != source_message_id
    ):
        return _ignored(db, update_id, kind, "DENIED", "telegram_handoff_mismatch")
    if handoff.work_status != "AWAITING_REPORT":
        _receipt(
            db,
            update_id,
            kind,
            "DUPLICATE",
            handoff_id=handoff.id,
            error_code="field_work_already_reported",
        )
        db.commit()
        return InboundResult(
            update_id, "DUPLICATE", kind, handoff.id, callback_id, sender_name, True
        )
    active = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(
            FieldWorkCompletionPrompt.handoff_id == handoff.id,
            FieldWorkCompletionPrompt.status.in_(ACTIVE_PROMPT_STATUSES),
        )
        .with_for_update()
    )
    if active is not None:
        _receipt(
            db,
            update_id,
            kind,
            "DUPLICATE",
            handoff_id=handoff.id,
            error_code="completion_description_already_requested",
        )
        db.commit()
        return InboundResult(
            update_id,
            "DUPLICATE",
            kind,
            handoff.id,
            callback_id,
            sender_name,
            True,
            active.id,
        )
    receipt, created = _receipt(
        db, update_id, kind, "PROCESSED", handoff_id=handoff.id, sync_status="PENDING"
    )
    if not created:
        db.rollback()
        return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
    prompt = FieldWorkCompletionPrompt(
        handoff_id=handoff.id,
        callback_update_id=receipt.id,
        chat_id=str(source_message.get("chat", {}).get("id")),
        sender_telegram_id=sender_id,
        sender_name=sender_name,
        status="REQUESTING",
    )
    db.add(prompt)
    db.commit()
    return InboundResult(
        update_id,
        "PROCESSED",
        kind,
        handoff.id,
        callback_id,
        sender_name,
        False,
        prompt.id,
    )


def _text_reply(
    db: Session,
    update_id: int,
    message: dict[str, Any],
    source_message_id: int,
    sender_id: int,
    sender_name: str,
    accepted_chat_ids: set[str],
) -> InboundResult:
    reply = message.get("reply_to_message")
    reply_message_id = _integer(reply.get("message_id")) if isinstance(reply, dict) else None
    text = message.get("text")
    if reply_message_id is None or not isinstance(text, str) or not text.strip():
        return _ignored(db, update_id, "TEXT_REPLY", "IGNORED", "telegram_reply_invalid")
    body = text.strip()
    if len(body) > 4000 or any(char == "\x00" for char in body):
        return _ignored(db, update_id, "TEXT_REPLY", "IGNORED", "telegram_reply_invalid")

    imported = db.scalar(
        select(FieldExternalMessage).where(
            FieldExternalMessage.chat_id.in_(accepted_chat_ids),
            FieldExternalMessage.telegram_message_id == source_message_id,
        )
    )
    if imported is not None:
        _receipt(
            db,
            update_id,
            imported.kind,
            "DUPLICATE",
            handoff_id=imported.handoff_id,
            error_code="telegram_message_already_imported",
        )
        db.commit()
        return InboundResult(
            update_id, "DUPLICATE", imported.kind, imported.handoff_id, duplicate=True
        )

    prompt = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(
            FieldWorkCompletionPrompt.chat_id.in_(accepted_chat_ids),
            FieldWorkCompletionPrompt.telegram_message_id == reply_message_id,
        )
        .with_for_update()
    )
    if prompt is None and isinstance(reply, dict):
        # A sendMessage can reach Telegram while its response is lost. In that
        # case the ForceReply remains UNCERTAIN and its message_id was never
        # persisted. Telegram includes that prompt (and its reply to the
        # original handoff) in the participant's reply Update, which lets us
        # recover the exact prompt without accepting an arbitrary group reply.
        prompt_text = reply.get("text")
        from_value = reply.get("from")
        quoted_chat = reply.get("chat")
        original = reply.get("reply_to_message")
        quoted_chat_id = _integer(quoted_chat.get("id")) if isinstance(quoted_chat, dict) else None
        bot_sender_id = _integer(from_value.get("id")) if isinstance(from_value, dict) else None
        original_chat = original.get("chat") if isinstance(original, dict) else None
        original_chat_id = (
            _integer(original_chat.get("id")) if isinstance(original_chat, dict) else None
        )
        original_id = _integer(original.get("message_id")) if isinstance(original, dict) else None
        if (
            prompt_text == "Что было сделано?"
            and isinstance(from_value, dict)
            and from_value.get("is_bot") is True
            and bot_sender_id is not None
            and bot_sender_id > 0
            and quoted_chat_id is not None
            and str(quoted_chat_id) in accepted_chat_ids
            and original_chat_id is not None
            and str(original_chat_id) in accepted_chat_ids
            and original_id is not None
        ):
            candidates = db.scalars(
                select(FieldWorkCompletionPrompt)
                .join(FieldWorkHandoff, FieldWorkHandoff.id == FieldWorkCompletionPrompt.handoff_id)
                .where(
                    FieldWorkCompletionPrompt.chat_id.in_(accepted_chat_ids),
                    FieldWorkCompletionPrompt.status == "UNCERTAIN",
                    FieldWorkHandoff.delivery_status == "SENT",
                    FieldWorkHandoff.work_status == "AWAITING_REPORT",
                    FieldWorkHandoff.telegram_message_id == original_id,
                )
                .with_for_update()
            ).all()
            if len(candidates) == 1:
                prompt = candidates[0]
                prompt.telegram_message_id = reply_message_id
                prompt.status = "WAITING"
                prompt.error_code = None
                prompt.sent_at = _event_time(reply.get("date"))
    if prompt is not None:
        kind = "COMPLETION_REPORT"
        handoff = db.scalar(
            select(FieldWorkHandoff)
            .where(FieldWorkHandoff.id == prompt.handoff_id)
            .with_for_update()
        )
        if handoff is None:
            return _ignored(db, update_id, kind, "IGNORED", "telegram_handoff_missing")
        if prompt.status != "WAITING" or handoff.work_status != "AWAITING_REPORT":
            _receipt(
                db,
                update_id,
                kind,
                "DUPLICATE",
                handoff_id=handoff.id,
                error_code="field_work_already_reported",
            )
            db.commit()
            return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
        event_time = _event_time(message.get("date"))
        receipt, created = _receipt(
            db,
            update_id,
            kind,
            "PROCESSED",
            handoff_id=handoff.id,
            sync_status="PENDING",
            confirmation_status="PENDING",
        )
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
        db.add(
            FieldExternalMessage(
                handoff_id=handoff.id,
                update_record_id=receipt.id,
                chat_id=str(message.get("chat", {}).get("id")),
                telegram_message_id=source_message_id,
                sender_telegram_id=sender_id,
                sender_name=sender_name,
                kind=kind,
                body=body,
                telegram_created_at=event_time,
            )
        )
        prompt.status = "COMPLETED"
        prompt.error_code = None
        prompt.completed_at = event_time
        handoff.work_status = "REPORTED"
        handoff.reported_at = event_time
        handoff.updated_at = datetime.now(UTC)
        ticket = db.get(Ticket, handoff.ticket_id)
        db.add(
            TicketEvent(
                ticket_id=handoff.ticket_id,
                event_type="field_work_reported",
                actor_id=None,
                event_data={"handoff_id": str(handoff.id), "message_kind": kind},
                ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
            )
        )
        db.add(
            TicketAudit(
                ticket_id=handoff.ticket_id,
                actor_id=None,
                action="field_work_reported",
                outcome="success",
                origin="SYSTEM",
            )
        )
        db.commit()
        return InboundResult(update_id, "PROCESSED", kind, handoff.id, sender_name=sender_name)

    return _ignored(db, update_id, "TEXT_REPLY", "IGNORED", "telegram_reply_unmatched")


def process_update(
    db: Session,
    update: dict[str, Any],
) -> InboundResult:
    """Import one Telegram Update without retaining raw payload or unauthorized identity."""

    update_id = _integer(update.get("update_id"))
    if update_id is None or update_id < 0:
        raise ValueError("telegram_update_id_invalid")
    existing = db.scalar(
        select(FieldDispatchUpdate).where(
            FieldDispatchUpdate.bot_identity == BOT_IDENTITY,
            FieldDispatchUpdate.update_id == update_id,
        )
    )
    if existing is not None:
        return InboundResult(
            update_id, "DUPLICATE", existing.kind, existing.handoff_id, duplicate=True
        )

    callback = update.get("callback_query")
    message = update.get("message")
    if isinstance(callback, dict):
        kind = "COMPLETION_CALLBACK"
        source_message = callback.get("message")
        sender_value = callback.get("from")
        callback_id = callback.get("id")
        if (
            not isinstance(callback_id, str)
            or not callback_id
            or not isinstance(source_message, dict)
        ):
            return _ignored(db, update_id, kind, "IGNORED", "telegram_callback_invalid")
    elif isinstance(message, dict):
        kind = "TEXT_REPLY"
        source_message = message
        sender_value = message.get("from")
    else:
        return _ignored(db, update_id, "UNSUPPORTED", "IGNORED", "telegram_update_unsupported")

    chat = source_message.get("chat")
    chat_id_value = chat.get("id") if isinstance(chat, dict) else None
    chat_type = chat.get("type") if isinstance(chat, dict) else None
    incoming_chat_id = str(chat_id_value)
    if chat_type not in {"group", "supergroup"}:
        return _ignored(db, update_id, kind, "DENIED", "telegram_chat_not_bound")
    binding = current_binding(db)
    current_ids = chat_id_aliases(db, binding.chat_id) if binding.chat_id is not None else set()
    accepted_chat_ids = chat_id_aliases(db, incoming_chat_id)
    if not accepted_chat_ids.intersection(current_ids):
        known_handoff = db.scalar(
            select(FieldWorkHandoff.id)
            .where(
                FieldWorkHandoff.chat_id.in_(accepted_chat_ids),
                FieldWorkHandoff.delivery_status == "SENT",
            )
            .limit(1)
        )
        known_prompt = db.scalar(
            select(FieldWorkCompletionPrompt.id)
            .where(FieldWorkCompletionPrompt.chat_id.in_(accepted_chat_ids))
            .limit(1)
        )
        if known_handoff is None and known_prompt is None:
            return _ignored(db, update_id, kind, "DENIED", "telegram_chat_not_bound")
    sender = _sender(sender_value)
    if sender is None:
        return _ignored(db, update_id, kind, "DENIED", "telegram_sender_invalid")
    sender_id, sender_name = sender
    source_message_id = _integer(source_message.get("message_id"))
    if source_message_id is None:
        return _ignored(db, update_id, kind, "IGNORED", "telegram_message_invalid")

    if isinstance(callback, dict):
        return _callback(
            db,
            update_id,
            callback,
            source_message,
            source_message_id,
            sender_id,
            sender_name,
            accepted_chat_ids,
        )
    return _text_reply(
        db,
        update_id,
        message,
        source_message_id,
        sender_id,
        sender_name,
        accepted_chat_ids,
    )
