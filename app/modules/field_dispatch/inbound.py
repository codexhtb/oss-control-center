"""Verified, idempotent import of Telegram replies and completion reports."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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


class SenderRegistryError(RuntimeError):
    """Protected sender registry cannot be used safely."""


@dataclass(frozen=True)
class TelegramSenderRegistry:
    path: Path

    def allowed_ids(self) -> frozenset[int]:
        try:
            stat = self.path.stat()
            if not self.path.is_file() or stat.st_mode & 0o077:
                raise SenderRegistryError("telegram_sender_registry_permissions")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except SenderRegistryError:
            raise
        except (OSError, json.JSONDecodeError):
            raise SenderRegistryError("telegram_sender_registry_unavailable") from None
        values = raw.get("allowed_user_ids") if isinstance(raw, dict) else None
        if not isinstance(values, list) or not values:
            raise SenderRegistryError("telegram_sender_registry_invalid")
        result: set[int] = set()
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SenderRegistryError("telegram_sender_registry_invalid")
            result.add(value)
        return frozenset(result)


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
    allowed_chat_id: str,
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
        or handoff.chat_id != allowed_chat_id
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
        chat_id=allowed_chat_id,
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
    allowed_chat_id: str,
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
            FieldExternalMessage.chat_id == allowed_chat_id,
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
            FieldWorkCompletionPrompt.chat_id == allowed_chat_id,
            FieldWorkCompletionPrompt.telegram_message_id == reply_message_id,
        )
        .with_for_update()
    )
    if prompt is not None:
        kind = "COMPLETION_REPORT"
        if sender_id != prompt.sender_telegram_id:
            return _ignored(db, update_id, kind, "DENIED", "completion_sender_mismatch")
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
            db, update_id, kind, "PROCESSED", handoff_id=handoff.id, sync_status="PENDING"
        )
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
        db.add(
            FieldExternalMessage(
                handoff_id=handoff.id,
                update_record_id=receipt.id,
                chat_id=allowed_chat_id,
                telegram_message_id=source_message_id,
                sender_telegram_id=sender_id,
                sender_name=sender_name,
                kind=kind,
                body=body,
                telegram_created_at=event_time,
            )
        )
        prompt.status = "COMPLETED"
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

    kind = "TEXT_REPLY"
    handoff = db.scalar(
        select(FieldWorkHandoff)
        .where(
            FieldWorkHandoff.chat_id == allowed_chat_id,
            FieldWorkHandoff.telegram_message_id == reply_message_id,
            FieldWorkHandoff.delivery_status == "SENT",
        )
        .with_for_update()
    )
    if handoff is None:
        return _ignored(db, update_id, kind, "IGNORED", "telegram_reply_unmatched")
    receipt, created = _receipt(db, update_id, kind, "PROCESSED", handoff_id=handoff.id)
    if not created:
        db.rollback()
        return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
    event_time = _event_time(message.get("date"))
    db.add(
        FieldExternalMessage(
            handoff_id=handoff.id,
            update_record_id=receipt.id,
            chat_id=allowed_chat_id,
            telegram_message_id=source_message_id,
            sender_telegram_id=sender_id,
            sender_name=sender_name,
            kind=kind,
            body=body,
            telegram_created_at=event_time,
        )
    )
    ticket = db.get(Ticket, handoff.ticket_id)
    db.add(
        TicketEvent(
            ticket_id=handoff.ticket_id,
            event_type="field_external_message_received",
            actor_id=None,
            event_data={"handoff_id": str(handoff.id), "message_kind": kind},
            ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
        )
    )
    db.add(
        TicketAudit(
            ticket_id=handoff.ticket_id,
            actor_id=None,
            action="field_external_message_received",
            outcome="success",
            origin="SYSTEM",
        )
    )
    db.commit()
    return InboundResult(update_id, "PROCESSED", kind, handoff.id, sender_name=sender_name)


def process_update(
    db: Session,
    update: dict[str, Any],
    allowed_sender_ids: frozenset[int],
    *,
    allowed_chat_id: str,
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
    if str(chat_id_value) != allowed_chat_id or chat_type not in {"group", "supergroup"}:
        return _ignored(db, update_id, kind, "DENIED", "telegram_chat_not_allowed")
    sender = _sender(sender_value)
    if sender is None or sender[0] not in allowed_sender_ids:
        return _ignored(db, update_id, kind, "DENIED", "telegram_sender_not_allowed")
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
            allowed_chat_id,
        )
    return _text_reply(
        db,
        update_id,
        message,
        source_message_id,
        sender_id,
        sender_name,
        allowed_chat_id,
    )
