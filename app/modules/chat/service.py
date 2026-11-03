"""Transactional operations for the shared staff room."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.chat.models import ChatMessage, ChatRead
from app.modules.identity.models import User
from app.modules.identity.service import utc_now

STAFF_ROOM_ID = UUID("00000000-0000-4000-8000-000000000001")
MESSAGE_LIMIT = 2000
SEND_LIMIT_PER_MINUTE = 12


def latest_id(db: Session) -> int:
    return (
        db.scalar(select(func.max(ChatMessage.id)).where(ChatMessage.room_id == STAFF_ROOM_ID)) or 0
    )


def list_messages(
    db: Session, *, before: int | None, limit: int
) -> tuple[list[ChatMessage], int | None]:
    statement = select(ChatMessage).where(ChatMessage.room_id == STAFF_ROOM_ID)
    if before is not None:
        statement = statement.where(ChatMessage.id < before)
    rows = list(db.scalars(statement.order_by(ChatMessage.id.desc()).limit(limit + 1)))
    older = len(rows) > limit
    items = list(reversed(rows[:limit]))
    return items, items[0].id if older and items else None


def messages_after(db: Session, after: int, limit: int = 100) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.room_id == STAFF_ROOM_ID, ChatMessage.id > after)
            .order_by(ChatMessage.id)
            .limit(limit)
        )
    )


def send_message(
    db: Session, *, sender_id: UUID, client_id: UUID, body: str
) -> tuple[ChatMessage, bool]:
    body = body.strip()
    if not body or len(body) > MESSAGE_LIMIT:
        raise ApiError(422, "invalid_chat_message", "Message must contain 1–2000 characters")

    # Serializes commits so event IDs are ordered by visibility for SSE cursors.
    db.execute(text("SELECT pg_advisory_xact_lock(2929, 1)"))
    # Also recheck the account after the lock, including duplicate client IDs.
    sender = db.scalar(select(User).where(User.id == sender_id).with_for_update())
    if sender is None or not sender.is_active or sender.must_change_password:
        raise ApiError(401, "unauthenticated", "Authentication required")
    existing = db.scalar(
        select(ChatMessage).where(
            ChatMessage.room_id == STAFF_ROOM_ID,
            ChatMessage.sender_id == sender_id,
            ChatMessage.client_id == client_id,
        )
    )
    if existing:
        db.commit()
        if existing.body != body:
            raise ApiError(409, "chat_client_id_conflict", "Client message ID was already used")
        return existing, False

    recent = (
        db.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(
                ChatMessage.sender_id == sender_id,
                ChatMessage.created_at >= utc_now() - timedelta(minutes=1),
            )
        )
        or 0
    )
    if recent >= SEND_LIMIT_PER_MINUTE:
        db.rollback()
        raise ApiError(429, "chat_rate_limited", "Too many chat messages")

    message = ChatMessage(
        room_id=STAFF_ROOM_ID,
        sender_id=sender_id,
        sender_name=sender.display_name,
        client_id=client_id,
        body=body,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message, True


def read_summary(db: Session, user_id: UUID) -> dict[str, int]:
    cursor = (
        db.scalar(
            select(ChatRead.last_message_id).where(
                ChatRead.room_id == STAFF_ROOM_ID, ChatRead.user_id == user_id
            )
        )
        or 0
    )
    unread = (
        db.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(
                ChatMessage.room_id == STAFF_ROOM_ID,
                ChatMessage.id > cursor,
                ChatMessage.sender_id != user_id,
            )
        )
        or 0
    )
    return {"latest_id": latest_id(db), "last_read_id": cursor, "unread_count": unread}


def mark_read(db: Session, user_id: UUID, through_id: int) -> dict[str, int]:
    if through_id > latest_id(db):
        raise ApiError(422, "invalid_chat_cursor", "Message cursor is ahead of the room")
    statement = (
        insert(ChatRead)
        .values(room_id=STAFF_ROOM_ID, user_id=user_id, last_message_id=through_id)
        .on_conflict_do_update(
            index_elements=[ChatRead.room_id, ChatRead.user_id],
            set_={
                "last_message_id": func.greatest(ChatRead.last_message_id, through_id),
                "updated_at": func.now(),
            },
        )
    )
    db.execute(statement)
    db.commit()
    return read_summary(db, user_id)
