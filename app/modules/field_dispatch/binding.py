"""Database-backed Telegram group destination and migration aliases."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.models import (
    FieldDispatchChatMigration,
    FieldDispatchGroupBinding,
    FieldDispatchGroupBindingAudit,
)


def current_binding(db: Session, *, lock: bool = False) -> FieldDispatchGroupBinding:
    statement = select(FieldDispatchGroupBinding).where(FieldDispatchGroupBinding.id == 1)
    if lock:
        statement = statement.with_for_update()
    binding = db.scalar(statement)
    if binding is None:
        raise RuntimeError("telegram_group_binding_not_initialized")
    return binding


def destination_chat_id(db: Session) -> str:
    binding = current_binding(db)
    if binding.status == "UNBOUND" or binding.chat_id is None:
        raise ApiError(409, "telegram_group_not_bound", "No Telegram destination group is bound")
    if binding.status == "BOT_REMOVED":
        raise ApiError(409, "telegram_bot_not_in_bound_group", "The bot is not in the bound group")
    return binding.chat_id


def mark_bot_unavailable(db: Session, failed_chat_id: str) -> None:
    """Block future delivery after Telegram definitively rejects the group."""
    binding = current_binding(db, lock=True)
    if binding.chat_id is None or binding.chat_id != failed_chat_id or binding.status != "ACTIVE":
        db.rollback()
        return
    binding.status = "BOT_REMOVED"
    binding.updated_at = datetime.now(UTC)
    db.add(
        FieldDispatchGroupBindingAudit(
            action="UNAVAILABLE",
            old_chat_id=binding.chat_id,
            new_chat_id=binding.chat_id,
            chat_title=binding.chat_title,
            chat_type=binding.chat_type,
        )
    )
    db.commit()


def chat_id_aliases(db: Session, chat_id: str) -> set[str]:
    """Return this chat id and its known pre-migration ids for message matching."""

    result = {chat_id}
    frontier = {chat_id}
    while frontier:
        older = (
            set(
                db.scalars(
                    select(FieldDispatchChatMigration.old_chat_id).where(
                        FieldDispatchChatMigration.new_chat_id.in_(frontier)
                    )
                ).all()
            )
            - result
        )
        result.update(older)
        frontier = older
    return result


def current_chat_id(db: Session, chat_id: str) -> str:
    """Resolve an historical id to the latest migrated Telegram chat id."""

    result = chat_id
    seen = {result}
    while True:
        migrated = db.scalar(
            select(FieldDispatchChatMigration.new_chat_id).where(
                FieldDispatchChatMigration.old_chat_id == result
            )
        )
        if migrated is None or migrated in seen:
            return result
        result = migrated
        seen.add(result)


def title_from_chat(value: object) -> str:
    if not isinstance(value, str):
        return "Группа Telegram"
    cleaned = " ".join("".join(char for char in value if ord(char) >= 32).split())
    return cleaned[:200] or "Группа Telegram"
