"""Opt-in one-shot Telegram update poller. It is intentionally not started by the API."""

import os
import sys
from pathlib import Path
from time import sleep

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
from app.modules.field_dispatch.models import FieldDispatchUpdate, FieldWorkHandoff
from app.modules.field_dispatch.service import LAB_CHAT_ID


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


def _sync_callback(
    db: Session, gateway: TelegramBotGateway, update_id: int, callback_id: str, handoff_id
) -> None:
    record = db.scalar(
        select(FieldDispatchUpdate).where(
            FieldDispatchUpdate.bot_identity == "oss_field_service_bot",
            FieldDispatchUpdate.update_id == update_id,
        )
    )
    handoff = db.get(FieldWorkHandoff, handoff_id)
    if record is None or handoff is None or handoff.telegram_message_id is None:
        return
    try:
        gateway.acknowledge_callback(callback_id, "Результат сохранён в OSS")
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


def _retry_status_sync(db: Session, gateway: TelegramBotGateway) -> None:
    records = db.scalars(
        select(FieldDispatchUpdate)
        .where(
            FieldDispatchUpdate.kind == "COMPLETION_CALLBACK",
            FieldDispatchUpdate.outcome == "PROCESSED",
            FieldDispatchUpdate.sync_status.in_(("PENDING", "FAILED")),
            FieldDispatchUpdate.handoff_id.is_not(None),
        )
        .order_by(FieldDispatchUpdate.received_at.asc())
        .limit(20)
    ).all()
    for record in records:
        handoff = db.get(FieldWorkHandoff, record.handoff_id)
        if handoff is None or handoff.telegram_message_id is None:
            continue
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
        _retry_status_sync(db, gateway)
        updates = gateway.get_updates(_offset(db), timeout_seconds=20)
        for update in updates:
            result = process_update(db, update, allowed, allowed_chat_id=LAB_CHAT_ID)
            if (
                result.kind == "COMPLETION_CALLBACK"
                and result.outcome == "PROCESSED"
                and result.callback_query_id
                and result.handoff_id
            ):
                _sync_callback(
                    db, gateway, result.update_id, result.callback_query_id, result.handoff_id
                )
            elif result.kind == "COMPLETION_CALLBACK" and result.callback_query_id:
                try:
                    gateway.acknowledge_callback(result.callback_query_id, "Уже сохранено")
                except (DispatchRejected, DispatchUncertain):
                    pass
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
