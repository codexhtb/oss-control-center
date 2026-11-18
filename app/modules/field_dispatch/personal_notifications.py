"""Durable privacy-safe personal Telegram notifications for OSS Field Work."""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.field_dispatch.gateway import (
    DispatchRejected,
    DispatchUncertain,
    TelegramBotGateway,
)
from app.modules.field_dispatch.models import (
    FieldInstaller,
    FieldInstallerDirection,
    FieldNotificationDelivery,
    FieldNotificationEvent,
    FieldNotificationPreference,
    FieldWorkHandoff,
    FieldWorkStaffAccess,
    FieldWorkStaffDirection,
)
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role

KIND_SETTING = {
    "NEW_WORK": "notify_new_work",
    "RESCHEDULED": "notify_reschedules",
    "REPORT_PENDING": "notify_verification",
}
MESSAGE_TEXT = {
    "NEW_WORK": "Появилась новая выездная работа. Подробности доступны в OSS Field Work.",
    "RESCHEDULED": (
        "Изменены дата или условия выездной работы. "
        "Проверьте актуальные данные в OSS Field Work."
    ),
    "REPORT_PENDING": "Получен отчёт о выездной работе, ожидающий проверки инженером.",
}


def _preference(db: Session, telegram_user_id: int) -> FieldNotificationPreference | None:
    return db.get(FieldNotificationPreference, telegram_user_id)


def preference_view(db: Session, telegram_user_id: int, *, can_verify: bool) -> dict:
    item = _preference(db, telegram_user_id)
    return {
        "notify_new_work": item.notify_new_work if item else True,
        "notify_reschedules": item.notify_reschedules if item else True,
        "notify_verification": item.notify_verification if item else True,
        "can_verify": can_verify,
        "subscription_status": item.subscription_status if item else "UNKNOWN",
        "last_error_code": item.last_error_code if item else None,
        "last_delivery_at": item.last_delivery_at if item else None,
    }


def update_preferences(
    db: Session,
    telegram_user_id: int,
    *,
    notify_new_work: bool,
    notify_reschedules: bool,
    notify_verification: bool,
    can_verify: bool,
) -> dict:
    item = _preference(db, telegram_user_id)
    if item is None:
        item = FieldNotificationPreference(telegram_user_id=telegram_user_id)
        db.add(item)
    item.notify_new_work = notify_new_work
    item.notify_reschedules = notify_reschedules
    item.notify_verification = notify_verification if can_verify else False
    item.updated_at = datetime.now(UTC)
    db.commit()
    return preference_view(db, telegram_user_id, can_verify=can_verify)


def mark_private_chat_seen(db: Session, update_payload: dict) -> None:
    """A private update proves that the user has an open chat with this bot."""
    message = update_payload.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict) or chat.get("type") != "private":
        return
    chat_id = chat.get("id")
    sender_id = sender.get("id")
    if type(chat_id) is not int or chat_id != sender_id or chat_id <= 0:
        return
    item = _preference(db, chat_id)
    if item is None:
        item = FieldNotificationPreference(telegram_user_id=chat_id)
        db.add(item)
    item.subscription_status = "ACTIVE"
    item.last_error_code = None
    item.updated_at = datetime.now(UTC)
    db.commit()


def _allows(db: Session, telegram_user_id: int, kind: str) -> bool:
    item = _preference(db, telegram_user_id)
    return item is None or bool(getattr(item, KIND_SETTING[kind]))


def _recipients(db: Session, handoff: FieldWorkHandoff, kind: str) -> set[int]:
    if handoff.direction is None:
        return set()
    recipients: set[int] = set()
    if kind != "REPORT_PENDING":
        recipients.update(
            db.scalars(
                select(FieldInstaller.telegram_user_id)
                .join(FieldInstallerDirection)
                .where(
                    FieldInstaller.is_active.is_(True),
                    FieldInstallerDirection.direction == handoff.direction,
                )
            ).all()
        )
    staff = db.execute(
        select(User, FieldWorkStaffAccess.telegram_user_id)
        .join(FieldWorkStaffAccess, FieldWorkStaffAccess.user_id == User.id)
        .join(FieldWorkStaffDirection, FieldWorkStaffDirection.user_id == User.id)
        .where(
            FieldWorkStaffAccess.is_enabled.is_(True),
            FieldWorkStaffAccess.telegram_user_id.is_not(None),
            FieldWorkStaffDirection.direction == handoff.direction,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.must_change_password.is_(False),
            User.role.in_((Role.ENGINEER, Role.ADMIN)),
        )
    ).all()
    for user, telegram_user_id in staff:
        if kind == "REPORT_PENDING" and not (
            has_permission(db, user, Permission.TICKET_FIELD_WORK)
            and has_permission(db, user, Permission.TICKET_READ)
        ):
            continue
        recipients.add(int(telegram_user_id))
    return {item for item in recipients if _allows(db, item, kind)}


def enqueue_notification(
    db: Session, handoff: FieldWorkHandoff, kind: str, *, dedupe_suffix: str
) -> FieldNotificationEvent | None:
    """Create one event and its frozen recipient set inside the caller transaction."""
    if kind not in KIND_SETTING:
        raise ValueError("field_notification_event_invalid")
    if handoff.direction is None:
        return None
    dedupe_key = f"{kind}:{handoff.id}:{dedupe_suffix}"
    existing = db.scalar(
        select(FieldNotificationEvent).where(FieldNotificationEvent.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return existing
    event = FieldNotificationEvent(
        handoff_id=handoff.id,
        kind=kind,
        direction=handoff.direction,
        dedupe_key=dedupe_key,
    )
    db.add(event)
    db.flush()
    for telegram_user_id in sorted(_recipients(db, handoff, kind)):
        db.add(
            FieldNotificationDelivery(
                event_id=event.id,
                telegram_user_id=telegram_user_id,
            )
        )
    return event


def _miniapp_url(handoff_id: UUID) -> str:
    configured = os.environ.get("FIELD_WORK_MINIAPP_URL", "")
    parsed = urlsplit(configured)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("field_work_miniapp_url_invalid")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["handoff"] = str(handoff_id)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _set_preference_delivery_state(
    db: Session,
    telegram_user_id: int,
    *,
    active: bool,
    error_code: str | None,
    delivered_at: datetime | None = None,
) -> None:
    item = _preference(db, telegram_user_id)
    if item is None:
        item = FieldNotificationPreference(telegram_user_id=telegram_user_id)
        db.add(item)
    item.subscription_status = "ACTIVE" if active else "UNAVAILABLE"
    item.last_error_code = error_code
    if delivered_at is not None:
        item.last_delivery_at = delivered_at
    item.updated_at = datetime.now(UTC)


def _retry_delay(attempts: int, retry_after: int | None = None) -> timedelta:
    seconds = retry_after if retry_after is not None else min(30 * 2 ** max(attempts - 1, 0), 3600)
    return timedelta(seconds=max(1, min(seconds, 86400)))


def _claim_delivery(db: Session) -> tuple[FieldNotificationDelivery, FieldNotificationEvent] | None:
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=2)
    db.execute(
        update(FieldNotificationDelivery)
        .where(
            FieldNotificationDelivery.status == "SENDING",
            FieldNotificationDelivery.last_attempt_at < stale_before,
        )
        .values(status="UNCERTAIN", last_error_code="telegram_transport_uncertain")
    )
    delivery = db.scalar(
        select(FieldNotificationDelivery)
        .where(
            FieldNotificationDelivery.status.in_(("PENDING", "RETRY")),
            FieldNotificationDelivery.available_at <= now,
            FieldNotificationDelivery.attempts < 8,
        )
        .order_by(FieldNotificationDelivery.available_at, FieldNotificationDelivery.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if delivery is None:
        db.commit()
        return None
    event = db.get(FieldNotificationEvent, delivery.event_id)
    if event is None:
        delivery.status = "FAILED"
        delivery.last_error_code = "notification_event_missing"
        db.commit()
        return None
    delivery.status = "SENDING"
    delivery.attempts += 1
    delivery.last_attempt_at = now
    delivery.last_error_code = None
    db.commit()
    return delivery, event


def drain_notification_outbox(
    db: Session, gateway: TelegramBotGateway, *, limit: int = 20
) -> int:
    """Deliver committed outbox rows; uncertain sends are never retried automatically."""
    processed = 0
    while processed < limit:
        claimed = _claim_delivery(db)
        if claimed is None:
            break
        delivery, event = claimed
        handoff = db.get(FieldWorkHandoff, event.handoff_id)
        if handoff is None or delivery.telegram_user_id not in _recipients(
            db, handoff, event.kind
        ):
            current = db.scalar(
                select(FieldNotificationDelivery)
                .where(FieldNotificationDelivery.id == delivery.id)
                .with_for_update()
            )
            if current is not None and current.status == "SENDING":
                current.status = "SKIPPED"
                current.last_error_code = "notification_recipient_inactive"
            db.commit()
            processed += 1
            continue
        try:
            receipt = gateway.send_personal_notification(
                str(delivery.telegram_user_id),
                MESSAGE_TEXT[event.kind],
                miniapp_url=_miniapp_url(event.handoff_id),
            )
        except DispatchRejected as exc:
            retryable = (
                exc.code == "telegram_connect_failed"
                or exc.code == "telegram_http_429"
                or exc.code.startswith("telegram_http_5")
            )
            blocked = exc.code in {"telegram_http_400", "telegram_http_403"}
            current = db.scalar(
                select(FieldNotificationDelivery)
                .where(FieldNotificationDelivery.id == delivery.id)
                .with_for_update()
            )
            if current is not None and current.status == "SENDING":
                current.last_error_code = exc.code
                if retryable:
                    current.status = "RETRY"
                    current.available_at = datetime.now(UTC) + _retry_delay(
                        current.attempts, exc.retry_after
                    )
                elif blocked:
                    current.status = "BLOCKED"
                    _set_preference_delivery_state(
                        db, current.telegram_user_id, active=False, error_code=exc.code
                    )
                else:
                    current.status = "FAILED"
            db.commit()
        except DispatchUncertain:
            current = db.scalar(
                select(FieldNotificationDelivery)
                .where(FieldNotificationDelivery.id == delivery.id)
                .with_for_update()
            )
            if current is not None and current.status == "SENDING":
                current.status = "UNCERTAIN"
                current.last_error_code = "telegram_transport_uncertain"
            db.commit()
        except ValueError as exc:
            current = db.scalar(
                select(FieldNotificationDelivery)
                .where(FieldNotificationDelivery.id == delivery.id)
                .with_for_update()
            )
            if current is not None and current.status == "SENDING":
                current.status = "FAILED"
                current.last_error_code = str(exc)[:80]
            db.commit()
        else:
            current = db.scalar(
                select(FieldNotificationDelivery)
                .where(FieldNotificationDelivery.id == delivery.id)
                .with_for_update()
            )
            if current is not None and current.status == "SENDING":
                now = datetime.now(UTC)
                current.status = "SENT"
                current.telegram_message_id = int(receipt.message_id)
                current.sent_at = now
                current.last_error_code = None
                _set_preference_delivery_state(
                    db, current.telegram_user_id, active=True, error_code=None, delivered_at=now
                )
            db.commit()
        processed += 1
    return processed
