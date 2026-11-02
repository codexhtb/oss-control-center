"""Telegram group binding commands and membership/migration events."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.modules.field_dispatch.binding import current_binding, title_from_chat
from app.modules.field_dispatch.gateway import (
    DispatchRejected,
    DispatchUncertain,
    TelegramBotGateway,
)
from app.modules.field_dispatch.inbound import (
    InboundResult,
    _integer,
    _receipt,
    _sender,
)
from app.modules.field_dispatch.models import (
    FieldDispatchChatMigration,
    FieldDispatchGroupBindingAudit,
    FieldDispatchUpdate,
)


def _chat(value: object) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    ident, kind = value.get("id"), value.get("type")
    if isinstance(ident, bool) or not isinstance(ident, int) or ident >= 0:
        return None
    if kind not in {"group", "supergroup"}:
        return None
    return str(ident), title_from_chat(value.get("title")), kind


def _admin(gateway: TelegramBotGateway, chat_id: str, user_id: int) -> bool:
    try:
        return gateway.get_chat_member(chat_id, user_id).get("status") in {
            "creator",
            "administrator",
        }
    except (DispatchRejected, DispatchUncertain, OSError):
        return False


def _audit(
    db: Session,
    receipt: FieldDispatchUpdate,
    action: str,
    actor: int | None,
    old: str | None,
    new: str | None,
    title: str | None,
    kind: str | None,
) -> None:
    db.add(
        FieldDispatchGroupBindingAudit(
            update_record_id=receipt.id,
            action=action,
            actor_telegram_id=actor,
            old_chat_id=old,
            new_chat_id=new,
            chat_title=title,
            chat_type=kind,
        )
    )


def process_group_control(
    db: Session, gateway: TelegramBotGateway, update: dict[str, Any]
) -> InboundResult | None:
    """Handle only binding commands and bot membership/migration service events."""
    update_id = _integer(update.get("update_id"))
    if update_id is None or update_id < 0:
        return None

    member_event = update.get("my_chat_member")
    message = update.get("message")
    if isinstance(member_event, dict):
        chat = _chat(member_event.get("chat"))
        if chat is None:
            return None
        binding = current_binding(db, lock=True)
        if binding.chat_id is None or chat[0] != binding.chat_id:
            db.rollback()
            return None
        new_member = member_event.get("new_chat_member")
        status = new_member.get("status") if isinstance(new_member, dict) else None
        action = (
            "BOT_REMOVED"
            if status in {"left", "kicked"}
            else ("BOT_RESTORED" if status == "administrator" else "UNAVAILABLE")
        )
        if action is None:
            return None
        receipt, created = _receipt(db, update_id, "GROUP_EVENT", "PROCESSED")
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", "GROUP_EVENT", duplicate=True)
        binding.status = "ACTIVE" if action == "BOT_RESTORED" else "BOT_REMOVED"
        binding.updated_at = datetime.now(UTC)
        _audit(db, receipt, action, None, chat[0], chat[0], binding.chat_title, binding.chat_type)
        db.commit()
        return InboundResult(update_id, "PROCESSED", "GROUP_EVENT")

    if not isinstance(message, dict):
        return None
    chat_value = message.get("chat")
    chat = _chat(chat_value)
    if chat is None:
        return None

    # Telegram converts groups to supergroups using these service fields.
    moved_to = message.get("migrate_to_chat_id")
    moved_from = message.get("migrate_from_chat_id")
    binding = current_binding(db)
    if (
        isinstance(moved_to, int)
        and not isinstance(moved_to, bool)
        and moved_to < 0
        and binding.chat_id == chat[0]
    ):
        binding = current_binding(db, lock=True)
        new_id = str(moved_to)
        receipt, created = _receipt(db, update_id, "GROUP_EVENT", "PROCESSED")
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", "GROUP_EVENT", duplicate=True)
        db.add(
            FieldDispatchChatMigration(
                old_chat_id=chat[0], new_chat_id=new_id, update_record_id=receipt.id
            )
        )
        binding.chat_id, binding.chat_type, binding.status = new_id, "supergroup", "ACTIVE"
        binding.updated_at = datetime.now(UTC)
        _audit(db, receipt, "MIGRATED", None, chat[0], new_id, binding.chat_title, "supergroup")
        db.commit()
        return InboundResult(update_id, "PROCESSED", "GROUP_EVENT")
    if (
        isinstance(moved_from, int)
        and not isinstance(moved_from, bool)
        and moved_from < 0
        and binding.chat_id == str(moved_from)
    ):
        binding = current_binding(db, lock=True)
        receipt, created = _receipt(db, update_id, "GROUP_EVENT", "PROCESSED")
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", "GROUP_EVENT", duplicate=True)
        db.add(
            FieldDispatchChatMigration(
                old_chat_id=str(moved_from), new_chat_id=chat[0], update_record_id=receipt.id
            )
        )
        binding.chat_id = chat[0]
        binding.chat_title = chat[1]
        binding.chat_type = chat[2]
        binding.status = "ACTIVE"
        binding.updated_at = datetime.now(UTC)
        _audit(db, receipt, "MIGRATED", None, str(moved_from), chat[0], chat[1], chat[2])
        db.commit()
        return InboundResult(update_id, "PROCESSED", "GROUP_EVENT")

    text = message.get("text")
    if not isinstance(text, str):
        return None
    command = text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower()
    if command not in {"/oss_bind", "/oss_status"}:
        return None
    kind = "GROUP_BIND" if command == "/oss_bind" else "GROUP_STATUS"
    sender = _sender(message.get("from"))
    if sender is None:
        receipt, created = _receipt(
            db, update_id, kind, "DENIED", error_code="telegram_sender_invalid"
        )
        db.commit()
        return InboundResult(
            update_id, "DENIED" if created else "DUPLICATE", kind, duplicate=not created
        )
    sender_id, _ = sender
    if command == "/oss_status" and not _admin(gateway, chat[0], sender_id):
        receipt, created = _receipt(
            db, update_id, kind, "DENIED", error_code="telegram_status_admin_required"
        )
        db.commit()
        if created:
            gateway.send_message(chat[0], "Статус группы доступен только её администратору.")
        return InboundResult(
            update_id, "DENIED" if created else "DUPLICATE", kind, duplicate=not created
        )
    if command == "/oss_bind":
        if not _admin(gateway, chat[0], sender_id):
            receipt, created = _receipt(
                db, update_id, kind, "DENIED", error_code="telegram_bind_admin_required"
            )
            db.commit()
            if created:
                gateway.send_message(chat[0], "Привязать группу может только её администратор.")
            return InboundResult(
                update_id, "DENIED" if created else "DUPLICATE", kind, duplicate=not created
            )
        try:
            bot = gateway.get_me()
            if not _admin(gateway, chat[0], bot["id"]):
                raise DispatchRejected("telegram_bot_admin_required")
        except (DispatchRejected, DispatchUncertain, OSError):
            receipt, created = _receipt(
                db, update_id, kind, "DENIED", error_code="telegram_bot_admin_required"
            )
            db.commit()
            if created:
                gateway.send_message(
                    chat[0],
                    "Сначала добавьте бота в группу с правами администратора, "
                    "затем повторите /oss_bind.",
                )
            return InboundResult(
                update_id, "DENIED" if created else "DUPLICATE", kind, duplicate=not created
            )
        binding = current_binding(db, lock=True)
        old = binding.chat_id
        receipt, created = _receipt(db, update_id, kind, "PROCESSED")
        if not created:
            db.rollback()
            return InboundResult(update_id, "DUPLICATE", kind, duplicate=True)
        binding.chat_id, binding.chat_title, binding.chat_type = chat
        binding.status, binding.bound_by_telegram_id = "ACTIVE", sender_id
        binding.updated_at = datetime.now(UTC)
        _audit(
            db,
            receipt,
            "BOUND" if old is None else "MOVED",
            sender_id,
            old,
            chat[0],
            chat[1],
            chat[2],
        )
        db.commit()
        gateway.send_message(chat[0], f"Группа «{chat[1]}» привязана для выездных заявок.")
        return InboundResult(update_id, "PROCESSED", kind)

    receipt, created = _receipt(db, update_id, kind, "PROCESSED")
    if not created:
        db.rollback()
        return InboundResult(update_id, "DUPLICATE", kind, duplicate=True)
    binding = current_binding(db)
    state = (
        "не привязана"
        if binding.status == "UNBOUND"
        else (f"«{binding.chat_title}» ({binding.status})")
    )
    db.commit()
    gateway.send_message(chat[0], f"Активная группа OSS: {state}.")
    return InboundResult(update_id, "PROCESSED", kind)
