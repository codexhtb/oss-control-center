"""Opt-in one-shot Telegram update poller. It is intentionally not started by the API."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import get_engine
from app.modules.field_dispatch.gateway import (
    DispatchRejected,
    DispatchUncertain,
    TelegramBotGateway,
    TelegramGatewayConfig,
)
from app.modules.field_dispatch.inbound import TelegramSenderRegistry, process_update
from app.modules.field_dispatch.models import (
    FieldDispatchUpdate,
    FieldWorkCompletionPrompt,
    FieldWorkHandoff,
)
from app.modules.field_dispatch.service import LAB_CHAT_ID
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent


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
        receipt = gateway.send_completion_prompt(handoff.chat_id, handoff.telegram_message_id)
    except DispatchRejected as exc:
        _set_prompt_failure(db, prompt_id, exc.code)
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
                event_type="field_work_description_requested",
                actor_id=None,
                event_data={"handoff_id": str(handoff.id)},
                ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
            )
        )
        db.add(
            TicketAudit(
                ticket_id=handoff.ticket_id,
                actor_id=None,
                action="field_work_description_requested",
                outcome="success",
                origin="SYSTEM",
            )
        )
    db.commit()


def _sync_callback(
    db: Session,
    gateway: TelegramBotGateway,
    callback_id: str,
    prompt_id: UUID,
) -> None:
    try:
        gateway.acknowledge_callback(callback_id, "Опишите выполненные работы в ответе боту")
    except (DispatchRejected, DispatchUncertain):
        # The durable prompt is still sent even if Telegram could not display the toast.
        pass
    _send_prompt(db, gateway, prompt_id)


def _sync_completion(db: Session, gateway: TelegramBotGateway, update_id: int) -> None:
    record = db.scalar(
        select(FieldDispatchUpdate).where(
            FieldDispatchUpdate.bot_identity == "oss_field_service_bot",
            FieldDispatchUpdate.update_id == update_id,
        )
    )
    handoff = db.get(FieldWorkHandoff, record.handoff_id) if record is not None else None
    if record is None or handoff is None or handoff.telegram_message_id is None:
        return
    try:
        gateway.mark_message_completed(handoff.chat_id, handoff.telegram_message_id)
    except DispatchRejected as exc:
        record.sync_status = "FAILED"
        record.sync_error_code = exc.code
    except DispatchUncertain:
        record.sync_status = "FAILED"
        record.sync_error_code = "telegram_transport_uncertain"
    else:
        record.sync_status = "SYNCED"
        record.sync_error_code = None
    db.commit()


def _retry_sync(db: Session, gateway: TelegramBotGateway) -> None:
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
            FieldDispatchUpdate.sync_status.in_(("PENDING", "FAILED")),
            FieldDispatchUpdate.handoff_id.is_not(None),
        )
        .order_by(FieldDispatchUpdate.received_at.asc())
        .limit(20)
    ).all()
    for record in records:
        _sync_completion(db, gateway, record.update_id)


def poll_once() -> int:
    token_file = os.environ.get("TELEGRAM_BOT_TOKEN_FILE")
    registry_file = os.environ.get("TELEGRAM_ALLOWED_SENDERS_FILE")
    chat_id = os.environ.get("TELEGRAM_FIELD_CHAT_ID", LAB_CHAT_ID)
    if not token_file or not registry_file or chat_id != LAB_CHAT_ID:
        raise RuntimeError("telegram_inbound_not_configured")
    registry = TelegramSenderRegistry(Path(registry_file))
    allowed = registry.allowed_ids()
    gateway = TelegramBotGateway(
        TelegramGatewayConfig(Path(token_file), allowed_chat_id=LAB_CHAT_ID, timeout_seconds=25)
    )
    factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    with factory() as db:
        _retry_sync(db, gateway)
        updates = gateway.get_updates(_offset(db), timeout_seconds=20)
        for update in updates:
            result = process_update(db, update, allowed, allowed_chat_id=LAB_CHAT_ID)
            if (
                result.kind == "COMPLETION_CALLBACK"
                and result.outcome == "PROCESSED"
                and result.callback_query_id
                and result.prompt_id
            ):
                _sync_callback(db, gateway, result.callback_query_id, result.prompt_id)
            elif result.kind == "COMPLETION_CALLBACK" and result.callback_query_id:
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
                    elif result.handoff_id is not None:
                        text = "Отчёт уже получен"
                    else:
                        text = "Действие не принято"
                    gateway.acknowledge_callback(result.callback_query_id, text)
                except (DispatchRejected, DispatchUncertain):
                    pass
            elif result.kind == "COMPLETION_REPORT" and result.outcome == "PROCESSED":
                _sync_completion(db, gateway, result.update_id)
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
