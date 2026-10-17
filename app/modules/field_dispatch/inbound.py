"""Verified, idempotent import of Telegram replies and completion callbacks."""

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
    FieldWorkHandoff,
)
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent

BOT_IDENTITY = "oss_field_service_bot"
COMPLETION_PREFIX = "field_done:"


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
    return db.get(FieldDispatchUpdate, receipt_id), True


def _ignored(db: Session, update_id: int, kind: str, outcome: str, code: str) -> InboundResult:
    receipt, created = _receipt(db, update_id, kind, outcome, error_code=code)
    db.commit()
    return InboundResult(
        update_id, "DUPLICATE" if not created else receipt.outcome, kind, duplicate=not created
    )


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
        callback_data = callback.get("data")
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
        callback_id = None
        callback_data = None
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

    if kind == "COMPLETION_CALLBACK":
        if not isinstance(callback_data, str) or not callback_data.startswith(COMPLETION_PREFIX):
            return _ignored(db, update_id, kind, "IGNORED", "telegram_callback_unknown")
        try:
            requested_handoff = UUID(callback_data[len(COMPLETION_PREFIX) :])
        except (ValueError, TypeError):
            return _ignored(db, update_id, kind, "IGNORED", "telegram_callback_invalid")
        handoff = db.scalar(
            select(FieldWorkHandoff)
            .where(FieldWorkHandoff.id == requested_handoff)
            .with_for_update()
        )
        if (
            handoff is None
            or handoff.delivery_status != "SENT"
            or handoff.chat_id != allowed_chat_id
            or handoff.telegram_message_id != source_message_id
        ):
            return _ignored(db, update_id, kind, "DENIED", "telegram_handoff_mismatch")
        if handoff.work_status != "AWAITING_REPORT":
            receipt, created = _receipt(
                db,
                update_id,
                kind,
                "DUPLICATE",
                handoff_id=handoff.id,
                error_code="field_work_already_reported",
            )
            db.commit()
            return InboundResult(
                update_id,
                "DUPLICATE",
                kind,
                handoff.id,
                callback_id,
                sender_name,
                duplicate=True,
            )
        telegram_message_id = None
        body = None
        # CallbackQuery has no event timestamp. Message.date belongs to the
        # original bot message, so the only truthful time is OSS receipt time.
        event_time = datetime.now(UTC)
    else:
        reply = source_message.get("reply_to_message")
        reply_message_id = _integer(reply.get("message_id")) if isinstance(reply, dict) else None
        text = message.get("text")
        if reply_message_id is None or not isinstance(text, str) or not text.strip():
            return _ignored(db, update_id, kind, "IGNORED", "telegram_reply_invalid")
        body = text.strip()
        if len(body) > 4000 or any(char == "\x00" for char in body):
            return _ignored(db, update_id, kind, "IGNORED", "telegram_reply_invalid")
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
        telegram_message_id = source_message_id
        event_time = _event_time(source_message.get("date"))

    receipt, created = _receipt(
        db,
        update_id,
        kind,
        "PROCESSED",
        handoff_id=handoff.id,
        sync_status="PENDING" if kind == "COMPLETION_CALLBACK" else "NOT_REQUIRED",
    )
    if not created:
        db.rollback()
        return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
    external = FieldExternalMessage(
        handoff_id=handoff.id,
        update_record_id=receipt.id,
        chat_id=allowed_chat_id,
        telegram_message_id=telegram_message_id,
        sender_telegram_id=sender_id,
        sender_name=sender_name,
        kind=kind,
        body=body,
        telegram_created_at=event_time,
    )
    db.add(external)
    ticket = db.get(Ticket, handoff.ticket_id)
    if kind == "COMPLETION_CALLBACK":
        handoff.work_status = "REPORTED"
        handoff.reported_at = event_time
        handoff.updated_at = datetime.now(UTC)
        event_type = "field_work_reported"
    else:
        event_type = "field_external_message_received"
    db.add(
        TicketEvent(
            ticket_id=handoff.ticket_id,
            event_type=event_type,
            actor_id=None,
            event_data={"handoff_id": str(handoff.id), "message_kind": kind},
            ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
        )
    )
    db.add(
        TicketAudit(
            ticket_id=handoff.ticket_id,
            actor_id=None,
            action=event_type,
            outcome="success",
            origin="SYSTEM",
        )
    )
    db.commit()
    return InboundResult(update_id, "PROCESSED", kind, handoff.id, callback_id, sender_name)
