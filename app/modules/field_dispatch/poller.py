"""Opt-in one-shot Telegram update poller. It is intentionally not started by the API."""

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import get_engine
from app.modules.field_dispatch.binding import current_chat_id, mark_bot_unavailable
from app.modules.field_dispatch.gateway import (
    DispatchRejected,
    DispatchUncertain,
    TelegramBotGateway,
    TelegramGatewayConfig,
)
from app.modules.field_dispatch.group_control import process_group_control
from app.modules.field_dispatch.inbound import process_update
from app.modules.field_dispatch.models import (
    FieldDispatchUpdate,
    FieldExternalMessage,
    FieldWorkCompletionPrompt,
    FieldWorkHandoff,
    FieldWorkReschedule,
)
from app.modules.field_dispatch.rescheduling import ABKHAZIA
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent

logger = logging.getLogger(__name__)


def _offset(db: Session) -> int:
    highest = db.scalar(
        select(func.max(FieldDispatchUpdate.update_id)).where(
            FieldDispatchUpdate.bot_identity == "oss_field_service_bot"
        )
    )
    if highest is not None:
        return int(highest) + 1
    configured = os.environ.get("TELEGRAM_INITIAL_UPDATE_OFFSET")
    if not configured or not configured.isdecimal():
        raise RuntimeError("telegram_initial_update_offset_required")
    return int(configured)


def _set_prompt_failure(
    db: Session, prompt_id: UUID, error_code: str, *, uncertain: bool = False
) -> None:
    prompt = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(FieldWorkCompletionPrompt.id == prompt_id)
        .with_for_update()
    )
    if prompt is None or prompt.status not in {"REQUESTING", "UNCERTAIN"}:
        db.rollback()
        return
    record = db.get(FieldDispatchUpdate, prompt.callback_update_id)
    prompt.status = "UNCERTAIN" if uncertain else "FAILED"
    prompt.error_code = error_code
    if record is not None:
        record.sync_status = "FAILED"
        record.sync_error_code = error_code
    db.commit()


def _send_prompt(db: Session, gateway: TelegramBotGateway, prompt_id: UUID) -> None:
    prompt = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(FieldWorkCompletionPrompt.id == prompt_id)
        .with_for_update()
    )
    handoff = db.get(FieldWorkHandoff, prompt.handoff_id) if prompt is not None else None
    if (
        prompt is None
        or prompt.status != "REQUESTING"
        or handoff is None
        or handoff.telegram_message_id is None
    ):
        db.rollback()
        return
    # Once the network call starts, a crash leaves the result unknown. Persist
    # that state first so restart never sends a second ForceReply automatically.
    prompt.status = "UNCERTAIN"
    prompt.error_code = "telegram_transport_uncertain"
    db.commit()
    try:
        destination = current_chat_id(db, prompt.chat_id)
        if prompt.prompt_type == "RESCHEDULE":
            receipt = gateway.send_reschedule_prompt(
                destination, handoff.telegram_message_id,
                username=prompt.recipient_username,
            )
        elif prompt.recipient_username:
            receipt = gateway.send_completion_prompt(
                destination, handoff.telegram_message_id, username=prompt.recipient_username
            )
        else:
            receipt = gateway.send_completion_prompt(destination, handoff.telegram_message_id)
    except DispatchRejected as exc:
        _set_prompt_failure(db, prompt_id, exc.code)
        if exc.code.endswith("403"):
            mark_bot_unavailable(db, destination)
        return
    except DispatchUncertain:
        _set_prompt_failure(db, prompt_id, "telegram_transport_uncertain", uncertain=True)
        return

    prompt = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(FieldWorkCompletionPrompt.id == prompt_id)
        .with_for_update()
    )
    if prompt is None or prompt.status != "UNCERTAIN":
        db.rollback()
        return
    record = db.get(FieldDispatchUpdate, prompt.callback_update_id)
    handoff = db.get(FieldWorkHandoff, prompt.handoff_id)
    now = datetime.now(UTC)
    prompt.status = "WAITING"
    prompt.chat_id = receipt.chat_id
    prompt.telegram_message_id = int(receipt.message_id)
    prompt.error_code = None
    prompt.sent_at = now
    if record is not None:
        record.sync_status = "SYNCED"
        record.sync_error_code = None
    if handoff is not None:
        ticket = db.get(Ticket, handoff.ticket_id)
        db.add(
            TicketEvent(
                ticket_id=handoff.ticket_id,
                event_type=("field_work_reschedule_requested" if prompt.prompt_type == "RESCHEDULE"
                            else "field_work_description_requested"),
                actor_id=None,
                event_data={"handoff_id": str(handoff.id)},
                ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
            )
        )
        db.add(
            TicketAudit(
                ticket_id=handoff.ticket_id,
                actor_id=None,
                action=("field_work_reschedule_requested" if prompt.prompt_type == "RESCHEDULE"
                        else "field_work_description_requested"),
                outcome="success",
                origin="SYSTEM",
            )
        )
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("telegram_prompt_receipt_persist_failed error_type=%s", type(exc).__name__)
        raise


def _sync_callback(
    db: Session,
    gateway: TelegramBotGateway,
    callback_id: str,
    prompt_id: UUID,
) -> None:
    try:
        prompt = db.get(FieldWorkCompletionPrompt, prompt_id)
        gateway.acknowledge_callback(
            callback_id,
            "Укажите причину и дату в ответе боту" if prompt and prompt.prompt_type == "RESCHEDULE"
            else "Опишите выполненные работы в ответе боту",
        )
    except (DispatchRejected, DispatchUncertain):
        # The durable prompt is still sent even if Telegram could not display the toast.
        pass
    _send_prompt(db, gateway, prompt_id)


def _delete_prompt(db: Session, gateway: TelegramBotGateway, prompt_id: UUID) -> None:
    prompt = db.scalar(
        select(FieldWorkCompletionPrompt)
        .where(FieldWorkCompletionPrompt.id == prompt_id)
        .with_for_update()
    )
    if (
        prompt is None
        or prompt.status not in {"COMPLETED", "CANCELED"}
        or prompt.telegram_message_id is None
        or prompt.deletion_status not in {"PENDING", "SENDING", "FAILED"}
        or prompt.deletion_attempts >= 5
    ):
        db.rollback()
        return
    now = datetime.now(UTC)
    if prompt.deletion_attempted_at is not None and prompt.deletion_status != "PENDING":
        retry_after = timedelta(seconds=30 * 2 ** min(prompt.deletion_attempts, 5))
        if now - prompt.deletion_attempted_at < retry_after:
            db.rollback()
            return
    prompt.deletion_status = "SENDING"
    prompt.deletion_attempts += 1
    prompt.deletion_attempted_at = now
    db.commit()
    try:
        gateway.delete_message(current_chat_id(db, prompt.chat_id), prompt.telegram_message_id)
    except DispatchRejected as exc:
        prompt.deletion_status = "FAILED"
        prompt.deletion_error_code = exc.code
    except DispatchUncertain:
        prompt.deletion_status = "FAILED"
        prompt.deletion_error_code = "telegram_transport_uncertain"
    else:
        prompt.deletion_status = "DELETED"
        prompt.deletion_error_code = None
    db.commit()


def _card_text(handoff: FieldWorkHandoff, report: FieldExternalMessage) -> str:
    suffix = f"\n\n✅ Выполнено\nМонтажник: {report.sender_name}"
    original = handoff.message_text or ""
    if len(original) + len(suffix) > 4096:
        original = original[: 4095 - len(suffix)].rstrip() + "…"
    return original + suffix


def _sync_report_action(
    db: Session, gateway: TelegramBotGateway, update_id: int, action: str
) -> None:
    if action not in {"report_delete", "card_edit"}:
        raise ValueError("telegram_report_action_invalid")
    record = db.scalar(
        select(FieldDispatchUpdate)
        .where(
            FieldDispatchUpdate.bot_identity == "oss_field_service_bot",
            FieldDispatchUpdate.update_id == update_id,
            FieldDispatchUpdate.kind == "COMPLETION_REPORT",
            FieldDispatchUpdate.outcome == "PROCESSED",
        )
        .with_for_update()
    )
    if record is None:
        db.rollback()
        return
    status = getattr(record, f"{action}_status")
    attempts = getattr(record, f"{action}_attempts")
    attempted_at = getattr(record, f"{action}_attempted_at")
    if status not in {"PENDING", "SENDING", "FAILED"} or attempts >= 5:
        db.rollback()
        return
    now = datetime.now(UTC)
    if attempted_at is not None and status != "PENDING":
        if now - attempted_at < timedelta(seconds=30 * 2 ** min(attempts, 5)):
            db.rollback()
            return
    report = db.scalar(
        select(FieldExternalMessage).where(
            FieldExternalMessage.update_record_id == record.id,
            FieldExternalMessage.kind == "COMPLETION_REPORT",
        )
    )
    handoff = db.get(FieldWorkHandoff, record.handoff_id) if record.handoff_id else None
    if (
        report is None
        or report.telegram_message_id is None
        or handoff is None
        or (
            action == "card_edit"
            and (handoff.telegram_message_id is None or not handoff.message_text)
        )
    ):
        setattr(record, f"{action}_status", "FAILED")
        setattr(record, f"{action}_attempts", 5)
        setattr(record, f"{action}_error_code", "telegram_cleanup_target_missing")
        db.commit()
        return
    setattr(record, f"{action}_status", "SENDING")
    setattr(record, f"{action}_attempts", attempts + 1)
    setattr(record, f"{action}_attempted_at", now)
    db.commit()
    try:
        if action == "report_delete":
            gateway.delete_message(current_chat_id(db, report.chat_id), report.telegram_message_id)
        else:
            gateway.edit_handoff_card(
                current_chat_id(db, handoff.chat_id),
                handoff.telegram_message_id,
                _card_text(handoff, report),
            )
    except DispatchRejected as exc:
        already_done = (action == "report_delete" and exc.code == "telegram_message_not_found") or (
            action == "card_edit" and exc.code == "telegram_message_not_modified"
        )
        status = "DONE" if already_done else "FAILED"
        error_code = None if already_done else exc.code
    except DispatchUncertain:
        status = "FAILED"
        error_code = "telegram_transport_uncertain"
    else:
        status = "DONE"
        error_code = None
    setattr(record, f"{action}_status", status)
    setattr(record, f"{action}_error_code", error_code)
    db.commit()


def _sync_completion(db: Session, gateway: TelegramBotGateway, update_id: int) -> None:
    record = db.scalar(
        select(FieldDispatchUpdate).where(
            FieldDispatchUpdate.bot_identity == "oss_field_service_bot",
            FieldDispatchUpdate.update_id == update_id,
            FieldDispatchUpdate.kind == "COMPLETION_REPORT",
            FieldDispatchUpdate.outcome == "PROCESSED",
        )
    )
    if record is None or record.handoff_id is None:
        return
    prompt_id = db.scalar(
        select(FieldWorkCompletionPrompt.id).where(
            FieldWorkCompletionPrompt.handoff_id == record.handoff_id,
            FieldWorkCompletionPrompt.prompt_type == "COMPLETION",
            FieldWorkCompletionPrompt.status == "COMPLETED",
            FieldWorkCompletionPrompt.deletion_status != "NOT_REQUIRED",
        )
    )
    if prompt_id is not None:
        try:
            _delete_prompt(db, gateway, prompt_id)
        except Exception as exc:
            db.rollback()
            logger.error("telegram_prompt_deletion_failed error_type=%s", type(exc).__name__)
    for action in ("report_delete", "card_edit"):
        try:
            _sync_report_action(db, gateway, update_id, action)
        except Exception as exc:
            db.rollback()
            logger.error(
                "telegram_report_cleanup_failed action=%s error_type=%s", action, type(exc).__name__
            )


def _sync_reschedule(db: Session, gateway: TelegramBotGateway, update_id: int) -> None:
    record = db.scalar(select(FieldDispatchUpdate).where(
        FieldDispatchUpdate.bot_identity == "oss_field_service_bot",
        FieldDispatchUpdate.update_id == update_id,
        FieldDispatchUpdate.kind.in_(("RESCHEDULE_REPORT", "RESCHEDULE_CANCEL")),
        FieldDispatchUpdate.outcome == "PROCESSED",
    ).with_for_update())
    if record is None or record.handoff_id is None or record.confirmation_status != "PENDING":
        db.rollback()
        return
    handoff = db.get(FieldWorkHandoff, record.handoff_id)
    if handoff is None:
        db.rollback()
        return
    if record.kind == "RESCHEDULE_CANCEL":
        text = "📅 Диалог переноса отменён. Выезд не изменён."
    else:
        item = db.scalar(select(FieldWorkReschedule).where(
            FieldWorkReschedule.update_record_id == record.id
        ))
        if item is None:
            db.rollback()
            return
        date_text = (
            item.scheduled_for.astimezone(ABKHAZIA).strftime("%d.%m.%Y %H:%M")
            if item.scheduled_for else "дата не согласована"
        )
        text = f"📅 Перенос выезда сохранён. {date_text} (время Абхазии)."
    record.confirmation_status = "SENDING"
    db.commit()
    try:
        gateway.send_message(current_chat_id(db, handoff.chat_id), text)
    except DispatchRejected as exc:
        record.confirmation_status = "FAILED"
        record.confirmation_error_code = exc.code
    except DispatchUncertain:
        record.confirmation_status = "UNCERTAIN"
        record.confirmation_error_code = "telegram_transport_uncertain"
    else:
        record.confirmation_status = "SENT"
        record.confirmation_error_code = None
    db.commit()


def _sync_invalid_reschedule(db: Session, gateway: TelegramBotGateway, update_id: int) -> None:
    record = db.scalar(select(FieldDispatchUpdate).where(
        FieldDispatchUpdate.bot_identity == "oss_field_service_bot",
        FieldDispatchUpdate.update_id == update_id,
        FieldDispatchUpdate.kind == "RESCHEDULE_INVALID",
    ))
    handoff = db.get(FieldWorkHandoff, record.handoff_id) if record and record.handoff_id else None
    if handoff is None:
        return
    try:
        gateway.send_message(
            current_chat_id(db, handoff.chat_id),
            "Не удалось сохранить перенос. Ответьте на вопрос бота строго в формате:\n"
            "Причина: ...\nДата: ДД.ММ.ГГГГ ЧЧ:ММ или Дата: не согласована "
            "(время Абхазии; дата должна быть в будущем).",
        )
    except (DispatchRejected, DispatchUncertain):
        pass


def _retry_sync(db: Session, gateway: TelegramBotGateway) -> None:
    cleanup_ids = db.scalars(
        select(FieldWorkCompletionPrompt.id)
        .where(
            FieldWorkCompletionPrompt.status.in_(("COMPLETED", "CANCELED")),
            FieldWorkCompletionPrompt.deletion_status.in_(("PENDING", "SENDING", "FAILED")),
            FieldWorkCompletionPrompt.deletion_attempts < 5,
        )
        .order_by(
            FieldWorkCompletionPrompt.deletion_attempted_at.asc().nullsfirst(),
            FieldWorkCompletionPrompt.completed_at.asc(),
        )
        .limit(100)
    ).all()
    for prompt_id in cleanup_ids:
        try:
            _delete_prompt(db, gateway, prompt_id)
        except Exception as exc:
            db.rollback()
            logger.error("telegram_prompt_deletion_failed error_type=%s", type(exc).__name__)

    prompt_ids = db.scalars(
        select(FieldWorkCompletionPrompt.id)
        .join(
            FieldDispatchUpdate,
            FieldDispatchUpdate.id == FieldWorkCompletionPrompt.callback_update_id,
        )
        .where(
            FieldWorkCompletionPrompt.status == "REQUESTING",
            FieldDispatchUpdate.sync_status == "PENDING",
        )
        .order_by(FieldWorkCompletionPrompt.created_at.asc())
        .limit(20)
    ).all()
    for prompt_id in prompt_ids:
        _send_prompt(db, gateway, prompt_id)

    records = db.scalars(
        select(FieldDispatchUpdate)
        .where(
            FieldDispatchUpdate.kind == "COMPLETION_REPORT",
            FieldDispatchUpdate.outcome == "PROCESSED",
            or_(
                and_(
                    FieldDispatchUpdate.report_delete_status.in_(("PENDING", "SENDING", "FAILED")),
                    FieldDispatchUpdate.report_delete_attempts < 5,
                ),
                and_(
                    FieldDispatchUpdate.card_edit_status.in_(("PENDING", "SENDING", "FAILED")),
                    FieldDispatchUpdate.card_edit_attempts < 5,
                ),
            ),
            FieldDispatchUpdate.handoff_id.is_not(None),
        )
        .order_by(FieldDispatchUpdate.received_at.desc())
        .limit(100)
    ).all()
    for record in records:
        _sync_completion(db, gateway, record.update_id)

    confirmations = db.scalars(select(FieldDispatchUpdate).where(
        FieldDispatchUpdate.kind.in_(("RESCHEDULE_REPORT", "RESCHEDULE_CANCEL")),
        FieldDispatchUpdate.outcome == "PROCESSED",
        FieldDispatchUpdate.confirmation_status == "PENDING",
    ).order_by(FieldDispatchUpdate.received_at.asc()).limit(50)).all()
    for record in confirmations:
        _sync_reschedule(db, gateway, record.update_id)


def poll_once() -> int:
    token_file = os.environ.get("TELEGRAM_BOT_TOKEN_FILE")
    if not token_file:
        raise RuntimeError("telegram_inbound_not_configured")
    gateway = TelegramBotGateway(TelegramGatewayConfig(Path(token_file), timeout_seconds=25))
    factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    with factory() as db:
        _retry_sync(db, gateway)
        updates = gateway.get_updates(_offset(db), timeout_seconds=20)
        for update in updates:
            result = process_group_control(db, gateway, update)
            if result is None:
                result = process_update(db, update)
            if (
                result.kind in {"COMPLETION_CALLBACK", "RESCHEDULE_CALLBACK"}
                and result.outcome == "PROCESSED"
                and result.callback_query_id
                and result.prompt_id
            ):
                _sync_callback(db, gateway, result.callback_query_id, result.prompt_id)
            elif (
                result.kind in {"COMPLETION_CALLBACK", "RESCHEDULE_CALLBACK"}
                and result.callback_query_id
            ):
                try:
                    active_prompt = (
                        db.get(FieldWorkCompletionPrompt, result.prompt_id)
                        if result.prompt_id
                        else None
                    )
                    if active_prompt is not None and active_prompt.status == "UNCERTAIN":
                        text = "Доставка вопроса не подтверждена, сообщите инженеру"
                    elif active_prompt is not None:
                        text = "Ответьте на вопрос бота"
                    elif result.kind == "RESCHEDULE_CALLBACK":
                        text = "Перенос сейчас недоступен"
                    elif result.handoff_id is not None:
                        text = "Отчёт уже получен"
                    else:
                        text = "Действие не принято"
                    gateway.acknowledge_callback(result.callback_query_id, text)
                except (DispatchRejected, DispatchUncertain):
                    pass
            elif result.kind == "COMPLETION_REPORT" and result.outcome == "PROCESSED":
                _sync_completion(db, gateway, result.update_id)
            elif (
                result.kind in {"RESCHEDULE_REPORT", "RESCHEDULE_CANCEL"}
                and result.outcome == "PROCESSED"
            ):
                _sync_reschedule(db, gateway, result.update_id)
            elif (
                result.kind == "RESCHEDULE_INVALID" and result.outcome == "DENIED"
                and not result.duplicate
            ):
                _sync_invalid_reschedule(db, gateway, result.update_id)
        return len(updates)


if __name__ == "__main__":
    if "--forever" not in sys.argv:
        poll_once()
    else:
        while True:
            try:
                poll_once()
            except (DispatchRejected, DispatchUncertain, OSError, RuntimeError):
                sleep(5)
