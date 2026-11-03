"""Authenticated shared staff chat and resumable message stream."""

import asyncio
import json

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api.v1.identity import SESSION_COOKIE, CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.chat.schemas import (
    ChatHistory,
    ChatMessageView,
    ChatSummary,
    MarkChatRead,
    SendChatMessage,
)
from app.modules.chat.service import (
    latest_id,
    list_messages,
    mark_read,
    messages_after,
    read_summary,
    send_message,
)
from app.modules.identity.service import current_identity

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/summary", response_model=ChatSummary)
def summary(identity: CurrentIdentityDep, db: SessionDep) -> dict[str, int]:
    return read_summary(db, identity.user.id)


@router.get("/messages", response_model=ChatHistory)
def history(
    identity: CurrentIdentityDep,
    db: SessionDep,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=40, ge=1, le=100),
) -> ChatHistory:
    items, next_before = list_messages(db, before=before, limit=limit)
    return ChatHistory(
        items=[ChatMessageView.model_validate(item) for item in items],
        next_before=next_before,
    )


@router.post("/messages", response_model=ChatMessageView)
def send(
    payload: SendChatMessage,
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
) -> ChatMessageView:
    message, created = send_message(
        db, sender_id=identity.user.id, client_id=payload.client_id, body=payload.body
    )
    response.status_code = 201 if created else 200
    return ChatMessageView.model_validate(message)


@router.post("/read", response_model=ChatSummary)
def read(payload: MarkChatRead, identity: CsrfIdentityDep, db: SessionDep) -> dict[str, int]:
    return mark_read(db, identity.user.id, payload.through_id)


def _poll(engine: Engine, token: str | None, cursor: int) -> list[dict] | None:
    with Session(engine) as db:
        try:
            identity = current_identity(db, token)
            if identity.user.must_change_password:
                return None
        except ApiError:
            return None
        return [
            ChatMessageView.model_validate(item).model_dump(mode="json")
            for item in messages_after(db, cursor)
        ]


@router.get("/events")
async def events(
    request: Request,
    identity: CurrentIdentityDep,
    db: SessionDep,
    after: int | None = Query(default=None, ge=0),
) -> StreamingResponse:
    del identity  # The dependency authenticates before the stream starts.
    last_event = request.headers.get("last-event-id")
    if last_event is not None:
        if not last_event.isdecimal() or len(last_event) > 20:
            raise ApiError(422, "invalid_chat_cursor", "Invalid event cursor")
        cursor = int(last_event)
    else:
        cursor = after if after is not None else latest_id(db)
    if cursor > latest_id(db):
        raise ApiError(422, "invalid_chat_cursor", "Event cursor is ahead of the room")

    engine = db.get_bind()
    token = request.cookies.get(SESSION_COOKIE)

    async def stream():
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            batch = await asyncio.to_thread(_poll, engine, token, cursor)
            if batch is None:
                return
            if batch:
                idle = 0
                for item in batch:
                    cursor = item["id"]
                    payload = json.dumps(item, ensure_ascii=False)
                    yield f"id: {cursor}\nevent: message\ndata: {payload}\n\n"
            else:
                idle += 1
                if idle >= 20:
                    idle = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
