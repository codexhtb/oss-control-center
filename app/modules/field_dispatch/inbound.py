"""Verified, idempotent import of Telegram replies and completion reports."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.binding import chat_id_aliases, current_binding
from app.modules.field_dispatch.gateway import completion_prompt_text, reschedule_prompt_text
from app.modules.field_dispatch.models import (
    FieldDispatchUpdate,
    FieldExternalMessage,
    FieldReportNotification,
    FieldRescheduleDialog,
    FieldWorkCompletionPrompt,
    FieldWorkDeliveryAttempt,
    FieldWorkHandoff,
)
from app.modules.field_dispatch.reschedule_dialog import (
    TEXT_STAGES,
    accept_text,
    active_dialog,
    advance,
    scheduled_instant,
    touch,
)
from app.modules.field_dispatch.rescheduling import parse_telegram_reschedule, record_reschedule
from app.modules.identity import models as identity_models  # noqa: F401 - register audit FK metadata
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent
from app.modules.tickets.status import TicketStatus

BOT_IDENTITY = "oss_field_service_bot"
COMPLETION_PREFIX = "field_done:"
RESCHEDULE_PREFIX = "field_reschedule:"
RESCHEDULE_STEP_PREFIX = "rs:"
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
    dialog_id: UUID | None = None


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


def _recover_prompt_receipt(
    db: Session, prompt: FieldWorkCompletionPrompt, quoted: dict[str, Any],
    chat_id: str, message_id: int,
) -> None:
    other_active = db.scalar(
        select(FieldWorkCompletionPrompt.id).where(
            FieldWorkCompletionPrompt.handoff_id == prompt.handoff_id,
            FieldWorkCompletionPrompt.id != prompt.id,
            FieldWorkCompletionPrompt.status.in_(ACTIVE_PROMPT_STATUSES),
        ).with_for_update().limit(1)
    )
    prompt.chat_id = chat_id
    prompt.telegram_message_id = message_id
    prompt.error_code = None
    prompt.sent_at = _event_time(quoted.get("date"))
    if other_active is not None:
        prompt.status = "CANCELED"
        prompt.completed_at = datetime.now(UTC)
    else:
        prompt.status = "WAITING"
    record = db.get(FieldDispatchUpdate, prompt.callback_update_id)
    if record is not None:
        record.sync_status = "SYNCED"
        record.sync_error_code = None


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


def _inline_reschedule(
    db: Session,
    update_id: int,
    callback: dict[str, Any],
    source_message_id: int,
    sender_id: int,
    sender_name: str,
    accepted_chat_ids: set[str],
) -> InboundResult:
    callback_id = callback["id"]
    data = callback.get("data")
    if not isinstance(data, str):
        return _ignored(
            db, update_id, "RESCHEDULE_CALLBACK", "IGNORED", "telegram_callback_invalid"
        )
    starting = data.startswith(RESCHEDULE_PREFIX)
    if starting:
        try:
            handoff_id = UUID(data[len(RESCHEDULE_PREFIX) :])
        except ValueError:
            return _ignored(
                db, update_id, "RESCHEDULE_CALLBACK", "IGNORED", "telegram_callback_invalid"
            )
        dialog_id = None
    else:
        parts = data.split(":", 2)
        if len(parts) != 3:
            return _ignored(
                db, update_id, "RESCHEDULE_CALLBACK", "IGNORED", "telegram_callback_invalid"
            )
        try:
            dialog_id = UUID(parts[1])
        except ValueError:
            return _ignored(
                db, update_id, "RESCHEDULE_CALLBACK", "IGNORED", "telegram_callback_invalid"
            )
        handoff_id = db.scalar(
            select(FieldRescheduleDialog.handoff_id).where(FieldRescheduleDialog.id == dialog_id)
        )
        if handoff_id is None:
            return _ignored(
                db, update_id, "RESCHEDULE_CALLBACK", "DENIED", "field_reschedule_dialog_missing"
            )
    handoff = db.scalar(
        select(FieldWorkHandoff).where(FieldWorkHandoff.id == handoff_id).with_for_update()
    )
    source_chat = callback.get("message", {}).get("chat", {})
    if (
        handoff is None
        or handoff.delivery_status != "SENT"
        or handoff.work_status != "AWAITING_REPORT"
        or handoff.chat_id not in accepted_chat_ids
        or handoff.telegram_message_id != source_message_id
        or str(source_chat.get("id")) not in accepted_chat_ids
    ):
        return _ignored(db, update_id, "RESCHEDULE_CALLBACK", "DENIED", "telegram_handoff_mismatch")
    ticket = db.get(Ticket, handoff.ticket_id)
    if ticket is None or ticket.deleted_at is not None or ticket.status != TicketStatus.IN_PROGRESS:
        return _ignored(
            db, update_id, "RESCHEDULE_CALLBACK", "DENIED", "field_reschedule_not_permitted"
        )
    if starting:
        completion = db.scalar(
            select(FieldWorkCompletionPrompt.id).where(
                FieldWorkCompletionPrompt.handoff_id == handoff.id,
                FieldWorkCompletionPrompt.status.in_(ACTIVE_PROMPT_STATUSES),
            )
        )
        if completion is not None:
            return _ignored(
                db, update_id, "RESCHEDULE_CALLBACK", "DENIED", "field_reschedule_dialog_busy"
            )
        dialog = active_dialog(db, handoff.id, lock=True)
        if dialog is not None and dialog.sender_telegram_id != sender_id:
            return _ignored(
                db, update_id, "RESCHEDULE_CALLBACK", "DENIED", "field_reschedule_dialog_busy"
            )
        receipt, created = _receipt(
            db,
            update_id,
            "RESCHEDULE_CALLBACK",
            "PROCESSED",
            handoff_id=handoff.id,
        )
        if not created:
            db.rollback()
            return InboundResult(
                update_id, "DUPLICATE", "RESCHEDULE_CALLBACK", handoff.id, duplicate=True
            )
        if dialog is None:
            dialog = FieldRescheduleDialog(
                handoff_id=handoff.id,
                chat_id=handoff.chat_id,
                sender_telegram_id=sender_id,
                sender_name=sender_name,
                stage="REASON",
            )
            db.add(dialog)
        else:
            touch(dialog)
        db.commit()
        return InboundResult(
            update_id,
            "PROCESSED",
            "RESCHEDULE_CALLBACK",
            handoff.id,
            callback_id,
            sender_name,
            prompt_id=None,
            dialog_id=dialog.id,
        )
    dialog = db.scalar(
        select(FieldRescheduleDialog).where(FieldRescheduleDialog.id == dialog_id).with_for_update()
    )
    if (
        dialog is None
        or dialog.handoff_id != handoff.id
        or dialog.chat_id not in accepted_chat_ids
        or dialog.sender_telegram_id != sender_id
        or dialog.stage in {"FINISHED", "CANCELED"}
    ):
        return _ignored(
            db, update_id, "RESCHEDULE_CALLBACK", "DENIED", "field_reschedule_dialog_mismatch"
        )
    try:
        outcome = advance(dialog, parts[2])
    except ValueError as exc:
        db.rollback()
        return _ignored(db, update_id, "RESCHEDULE_CALLBACK", "DENIED", str(exc))
    kind = (
        "RESCHEDULE_REPORT"
        if outcome == "CONFIRM"
        else ("RESCHEDULE_CANCEL" if outcome == "CANCEL" else "RESCHEDULE_CALLBACK")
    )
    receipt, created = _receipt(
        db,
        update_id,
        kind,
        "PROCESSED",
        handoff_id=handoff.id,
    )
    if not created:
        db.rollback()
        return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
    if outcome == "CONFIRM":
        try:
            instant = scheduled_instant(dialog)
            record_reschedule(
                db,
                handoff,
                reason=dialog.reason or "",
                scheduled_for=instant,
                scheduled_date=dialog.local_date,
                source="TELEGRAM",
                author_name=dialog.sender_name,
                telegram_user_id=sender_id,
                telegram_chat_id=handoff.chat_id,
                update_record_id=receipt.id,
            )
        except (ApiError, ValueError):
            db.rollback()
            return _ignored(db, update_id, kind, "DENIED", "field_reschedule_not_permitted")
        dialog.stage = "FINISHED"
        dialog.updated_at = datetime.now(UTC)
    db.commit()
    return InboundResult(
        update_id, "PROCESSED", kind, handoff.id, callback_id, sender_name, dialog_id=dialog.id
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
    callback_id = callback.get("id")
    callback_data = callback.get("data")
    if isinstance(callback_data, str) and callback_data.startswith(COMPLETION_PREFIX):
        kind, prompt_type, prefix = "COMPLETION_CALLBACK", "COMPLETION", COMPLETION_PREFIX
    elif isinstance(callback_data, str) and callback_data.startswith(RESCHEDULE_PREFIX):
        kind, prompt_type, prefix = "RESCHEDULE_CALLBACK", "RESCHEDULE", RESCHEDULE_PREFIX
    else:
        return _ignored(
            db, update_id, "COMPLETION_CALLBACK", "IGNORED", "telegram_callback_unknown"
        )
    try:
        requested_handoff = UUID(callback_data[len(prefix) :])
    except (ValueError, TypeError):
        return _ignored(db, update_id, kind, "IGNORED", "telegram_callback_invalid")
    handoff = db.scalar(
        select(FieldWorkHandoff).where(FieldWorkHandoff.id == requested_handoff).with_for_update()
    )
    source_chat_id = str(source_message.get("chat", {}).get("id"))
    if (
        handoff is not None
        and handoff.delivery_status == "UNCERTAIN"
        and handoff.chat_id == source_chat_id
        and source_chat_id in accepted_chat_ids
        and source_message_id > 0
    ):
        # Telegram supplied a callback from the original bot card. Its unique
        # handoff callback data, chat and message ID confirm delivery.
        attempt = db.scalar(
            select(FieldWorkDeliveryAttempt)
            .where(FieldWorkDeliveryAttempt.handoff_id == handoff.id)
            .order_by(
                FieldWorkDeliveryAttempt.started_at.desc(), FieldWorkDeliveryAttempt.id.desc()
            )
            .limit(1).with_for_update()
        )
        if attempt is not None and attempt.status == "UNCERTAIN":
            now = datetime.now(UTC)
            handoff.delivery_status = attempt.status = "SENT"
            handoff.telegram_message_id = attempt.telegram_message_id = source_message_id
            handoff.sent_at = now
            handoff.last_error_code = attempt.error_code = None
            handoff.updated_at = now
            ticket = db.get(Ticket, handoff.ticket_id)
            db.add(TicketEvent(
                ticket_id=handoff.ticket_id, event_type="field_work_delivery_reconciled",
                actor_id=handoff.sent_by,
                event_data={"handoff_id": str(handoff.id), "message_id": source_message_id},
                ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
            ))
            db.add(TicketAudit(
                ticket_id=handoff.ticket_id, actor_id=handoff.sent_by,
                action="field_work_delivery_reconciled", outcome="success", origin="SYSTEM",
            ))
            db.flush()
    if (
        handoff is None
        or handoff.delivery_status != "SENT"
        or handoff.chat_id not in accepted_chat_ids
        or handoff.telegram_message_id != source_message_id
        or handoff.chat_id != str(source_message.get("chat", {}).get("id"))
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
    if prompt_type == "COMPLETION" and active_dialog(db, handoff.id) is not None:
        return _ignored(db, update_id, kind, "DENIED", "field_reschedule_dialog_busy")
    if prompt_type == "RESCHEDULE":
        ticket = db.get(Ticket, handoff.ticket_id)
        if (
            ticket is None or ticket.deleted_at is not None
            or ticket.status != TicketStatus.IN_PROGRESS
        ):
            receipt, created = _receipt(
                db, update_id, kind, "DENIED", handoff_id=handoff.id,
                error_code="field_reschedule_not_permitted",
            )
            db.commit()
            return InboundResult(
                update_id, "DENIED" if created else "DUPLICATE", kind,
                handoff.id, callback_id, sender_name, not created,
            )
    active = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(
            FieldWorkCompletionPrompt.handoff_id == handoff.id,
            FieldWorkCompletionPrompt.status.in_(ACTIVE_PROMPT_STATUSES),
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if active is not None:
        retry_ready = (
            prompt_type == "COMPLETION"
            and active.prompt_type == "COMPLETION"
            and active.status != "REQUESTING"
            and now - active.created_at >= timedelta(seconds=45)
        )
        recent_prompts = db.scalar(
            select(func.count()).select_from(FieldWorkCompletionPrompt).where(
                FieldWorkCompletionPrompt.handoff_id == handoff.id,
                FieldWorkCompletionPrompt.created_at >= now - timedelta(minutes=10),
                or_(
                    FieldWorkCompletionPrompt.status.in_(ACTIVE_PROMPT_STATUSES + ("CANCELED",)),
                    FieldWorkCompletionPrompt.error_code == "superseded_uncertain",
                ),
            )
        ) or 0
        if not retry_ready or recent_prompts >= 3:
            _receipt(
                db, update_id, kind, "DUPLICATE", handoff_id=handoff.id,
                error_code=(
                    "completion_prompt_retry_limit" if recent_prompts >= 3
                    else "completion_description_already_requested"
                ),
            )
            db.commit()
            return InboundResult(
                update_id, "DUPLICATE", kind, handoff.id, callback_id,
                sender_name, True, active.id,
            )
    else:
        latest_failed = db.scalar(
            select(FieldWorkCompletionPrompt).where(
                FieldWorkCompletionPrompt.handoff_id == handoff.id,
                FieldWorkCompletionPrompt.status == "FAILED",
            ).order_by(FieldWorkCompletionPrompt.created_at.desc()).limit(1)
        )
        if (
            prompt_type == "COMPLETION"
            and latest_failed is not None
            and latest_failed.prompt_type == "COMPLETION"
            and now - latest_failed.created_at < timedelta(seconds=15)
        ):
            _receipt(
                db, update_id, kind, "DUPLICATE", handoff_id=handoff.id,
                error_code="completion_prompt_retry_wait",
            )
            db.commit()
            return InboundResult(
                update_id, "DUPLICATE", kind, handoff.id, callback_id,
                sender_name, True, latest_failed.id,
            )
    receipt, created = _receipt(
        db, update_id, kind, "PROCESSED", handoff_id=handoff.id, sync_status="PENDING"
    )
    if not created:
        db.rollback()
        return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
    if active is not None:
        if active.status == "WAITING":
            active.status = "CANCELED"
            active.completed_at = now
            # Keep the old question until the replacement was delivered.
            active.deletion_status = "NOT_REQUIRED"
        else:
            active.status = "FAILED"
            active.error_code = "superseded_uncertain"
            old_update = db.get(FieldDispatchUpdate, active.callback_update_id)
            if old_update is not None:
                old_update.sync_status = "FAILED"
                old_update.sync_error_code = "superseded_uncertain"
    prompt = FieldWorkCompletionPrompt(
        handoff_id=handoff.id,
        callback_update_id=receipt.id,
        chat_id=str(source_message.get("chat", {}).get("id")),
        sender_telegram_id=sender_id,
        sender_name=sender_name,
        status="REQUESTING",
        prompt_type=prompt_type,
        recipient_username=(
            callback["from"]["username"]
            if isinstance(callback.get("from"), dict)
            and isinstance(callback["from"].get("username"), str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", callback["from"]["username"])
            else None
        ),
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
    incoming_chat_id = str(message.get("chat", {}).get("id"))

    card = db.scalar(select(FieldWorkHandoff).where(
        FieldWorkHandoff.chat_id == incoming_chat_id,
        FieldWorkHandoff.telegram_message_id == reply_message_id,
    ).with_for_update())
    dialog = active_dialog(db, card.id, lock=True) if card is not None else None
    if dialog is not None and dialog.stage in TEXT_STAGES:
        if dialog.sender_telegram_id != sender_id:
            return _ignored(
                db, update_id, "TEXT_REPLY", "DENIED", "field_reschedule_sender_mismatch"
            )
        if card.work_status != "AWAITING_REPORT" or card.delivery_status != "SENT":
            return _ignored(db, update_id, "TEXT_REPLY", "DENIED", "field_reschedule_not_active")
        accept_text(dialog, body)
        kind = "RESCHEDULE_INVALID" if dialog.input_error else "TEXT_REPLY"
        receipt, created = _receipt(
            db, update_id, kind, "DENIED" if dialog.input_error else "PROCESSED",
            handoff_id=card.id, error_code=dialog.input_error,
        )
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", kind, card.id, duplicate=True)
        db.commit()
        return InboundResult(update_id, receipt.outcome, kind, card.id,
                             sender_name=sender_name, dialog_id=dialog.id)

    imported = db.scalar(
        select(FieldExternalMessage).where(
            FieldExternalMessage.chat_id == incoming_chat_id,
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
            FieldWorkCompletionPrompt.chat_id == incoming_chat_id,
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
            isinstance(prompt_text, str)
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
                    or_(
                        FieldWorkCompletionPrompt.status == "UNCERTAIN",
                        and_(
                            FieldWorkCompletionPrompt.status == "FAILED",
                            FieldWorkCompletionPrompt.error_code == "superseded_uncertain",
                        ),
                    ),
                    FieldWorkHandoff.delivery_status == "SENT",
                    FieldWorkHandoff.work_status == "AWAITING_REPORT",
                    FieldWorkHandoff.telegram_message_id == original_id,
                )
                .with_for_update()
            ).all()
            matches = []
            for candidate in candidates:
                candidate_handoff = db.get(FieldWorkHandoff, candidate.handoff_id)
                if candidate_handoff is None or candidate_handoff.chat_id != str(original_chat_id):
                    continue
                prior = db.scalar(select(FieldWorkCompletionPrompt.id).where(
                    FieldWorkCompletionPrompt.handoff_id == candidate.handoff_id,
                    FieldWorkCompletionPrompt.created_at < candidate.created_at,
                ).limit(1))
                ticket = db.get(Ticket, candidate_handoff.ticket_id)
                context = f"Заявка #{ticket.number} · {candidate.id}" if prior and ticket else None
                expected = (
                    reschedule_prompt_text(candidate.recipient_username, context)
                    if candidate.prompt_type == "RESCHEDULE"
                    else completion_prompt_text(candidate.recipient_username, context)
                )
                if prompt_text == expected:
                    matches.append(candidate)
            if len(matches) == 1:
                prompt = matches[0]
                _recover_prompt_receipt(
                    db, prompt, reply, str(quoted_chat_id), reply_message_id
                )
        if (
            prompt is None
            and original_id is None
            and isinstance(prompt_text, str)
            and isinstance(from_value, dict)
            and from_value.get("is_bot") is True
            and bot_sender_id is not None
            and bot_sender_id > 0
            and quoted_chat_id is not None
            and str(quoted_chat_id) in accepted_chat_ids
        ):
            # A migrated group's old card cannot be quoted in the new group.
            # The fallback prompt carries an exact prompt ID for recovery if
            # Telegram delivered it but its sendMessage receipt was lost.
            match = re.search(r"(?m)^Заявка #(\d+) · ([0-9a-f-]{36})$", prompt_text)
            if match is not None:
                try:
                    prompt_id = UUID(match.group(2))
                except ValueError:
                    prompt_id = None
                if prompt_id is not None:
                    candidate = db.scalar(
                        select(FieldWorkCompletionPrompt)
                        .join(FieldWorkHandoff)
                        .where(
                            FieldWorkCompletionPrompt.id == prompt_id,
                            FieldWorkCompletionPrompt.chat_id.in_(accepted_chat_ids),
                            or_(
                                FieldWorkCompletionPrompt.status == "UNCERTAIN",
                                and_(
                                    FieldWorkCompletionPrompt.status == "FAILED",
                                    FieldWorkCompletionPrompt.error_code == "superseded_uncertain",
                                ),
                            ),
                            FieldWorkHandoff.delivery_status == "SENT",
                            FieldWorkHandoff.work_status == "AWAITING_REPORT",
                        )
                        .with_for_update()
                    )
                    handoff = db.get(FieldWorkHandoff, candidate.handoff_id) if candidate else None
                    ticket = db.get(Ticket, handoff.ticket_id) if handoff else None
                    context = f"Заявка #{ticket.number} · {prompt_id}" if ticket else None
                    expected = None
                    if candidate is not None:
                        expected = (
                            reschedule_prompt_text(candidate.recipient_username, context)
                            if candidate.prompt_type == "RESCHEDULE"
                            else completion_prompt_text(candidate.recipient_username, context)
                        )
                    if ticket and str(ticket.number) == match.group(1) and prompt_text == expected:
                        prompt = candidate
                        _recover_prompt_receipt(
                            db, prompt, reply, str(quoted_chat_id), reply_message_id
                        )
    if prompt is not None:
        if prompt.prompt_type == "RESCHEDULE":
            kind = "RESCHEDULE_REPORT"
            handoff = db.scalar(select(FieldWorkHandoff).where(
                FieldWorkHandoff.id == prompt.handoff_id
            ).with_for_update())
            if (
                handoff is None or prompt.status != "WAITING"
                or handoff.work_status != "AWAITING_REPORT"
                or handoff.delivery_status != "SENT"
                or handoff.chat_id not in accepted_chat_ids
            ):
                return _ignored(db, update_id, kind, "DENIED", "field_reschedule_not_active")
            if sender_id != prompt.sender_telegram_id:
                return _ignored(
                    db, update_id, "TEXT_REPLY", "DENIED", "field_reschedule_sender_mismatch"
                )
            if body == "/cancel":
                receipt, created = _receipt(
                    db, update_id, "RESCHEDULE_CANCEL", "PROCESSED", handoff_id=handoff.id,
                    confirmation_status="PENDING",
                )
                if not created:
                    db.rollback()
                    return InboundResult(
                        update_id, "DUPLICATE", "RESCHEDULE_CANCEL", handoff.id,
                        duplicate=True,
                    )
                prompt.status = "CANCELED"
                prompt.completed_at = _event_time(message.get("date"))
                prompt.deletion_status = "PENDING"
                db.commit()
                return InboundResult(update_id, "PROCESSED", "RESCHEDULE_CANCEL", handoff.id)
            try:
                reason, scheduled_for = parse_telegram_reschedule(body)
            except ValueError as exc:
                code = str(exc)
                receipt, created = _receipt(
                    db, update_id, "RESCHEDULE_INVALID", "DENIED", handoff_id=handoff.id,
                    error_code=code,
                )
                db.commit()
                return InboundResult(
                    update_id, "DENIED" if created else "DUPLICATE", "RESCHEDULE_INVALID",
                    handoff.id, duplicate=not created,
                )
            receipt, created = _receipt(
                db, update_id, kind, "PROCESSED", handoff_id=handoff.id,
                confirmation_status="PENDING",
            )
            if not created:
                db.rollback()
                return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
            try:
                record_reschedule(
                    db, handoff, reason=reason, scheduled_for=scheduled_for,
                    source="TELEGRAM", author_name=sender_name,
                    telegram_user_id=sender_id, telegram_chat_id=str(message["chat"]["id"]),
                    telegram_message_id=source_message_id, update_record_id=receipt.id,
                )
            except ApiError:
                db.rollback()
                return _ignored(
                    db, update_id, kind, "DENIED", "field_reschedule_not_permitted"
                )
            prompt.status = "COMPLETED"
            prompt.completed_at = _event_time(message.get("date"))
            prompt.deletion_status = "PENDING"
            db.commit()
            return InboundResult(update_id, "PROCESSED", kind, handoff.id, sender_name=sender_name)
        kind = "COMPLETION_REPORT"
        handoff = db.scalar(
            select(FieldWorkHandoff)
            .where(FieldWorkHandoff.id == prompt.handoff_id)
            .with_for_update()
        )
        if handoff is None:
            return _ignored(db, update_id, kind, "IGNORED", "telegram_handoff_missing")
        if (
            prompt.status not in {"WAITING", "CANCELED"}
            or handoff.work_status != "AWAITING_REPORT"
        ):
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
            sync_status="NOT_REQUIRED",
            confirmation_status="NOT_REQUIRED",
        )
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", kind, handoff.id, duplicate=True)
        other_prompts = db.scalars(
            select(FieldWorkCompletionPrompt).where(
                FieldWorkCompletionPrompt.handoff_id == handoff.id,
                FieldWorkCompletionPrompt.id != prompt.id,
                FieldWorkCompletionPrompt.status.in_(ACTIVE_PROMPT_STATUSES),
            ).with_for_update()
        ).all()
        for other in other_prompts:
            if other.status == "WAITING":
                other.status = "CANCELED"
                other.completed_at = event_time
                other.deletion_status = "PENDING"
            else:
                other.status = "FAILED"
                other.error_code = "superseded_by_report"
                other_update = db.get(FieldDispatchUpdate, other.callback_update_id)
                if other_update is not None:
                    other_update.sync_status = "FAILED"
                    other_update.sync_error_code = "superseded_by_report"
        receipt.report_delete_status = "PENDING"
        receipt.card_edit_status = "PENDING"
        report_message_id = uuid4()
        db.add(
            FieldExternalMessage(
                id=report_message_id,
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
        db.add(FieldReportNotification(handoff_id=handoff.id, message_id=report_message_id))
        prompt.status = "COMPLETED"
        prompt.error_code = None
        prompt.completed_at = event_time
        prompt.deletion_status = "PENDING"
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
        data = callback.get("data")
        if isinstance(data, str) and (
            data.startswith(RESCHEDULE_PREFIX) or data.startswith(RESCHEDULE_STEP_PREFIX)
        ):
            return _inline_reschedule(
                db, update_id, callback, source_message_id, sender_id, sender_name,
                accepted_chat_ids,
            )
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
