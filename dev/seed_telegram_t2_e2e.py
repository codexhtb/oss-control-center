"""Seed one reported Telegram handoff in a disposable browser database."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
from app.modules.field_dispatch.models import (
    FieldDispatchUpdate,
    FieldExternalMessage,
    FieldWorkCompletionPrompt,
    FieldWorkHandoff,
)
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User
from app.modules.identity.roles import Role
from app.modules.tickets.models import ConnectionSnapshot, SubscriberSnapshot, Ticket
from app.modules.tickets.status import TicketPriority, TicketStatus, TicketType


def main() -> None:
    settings = get_settings()
    if (
        settings.app_env != "dev"
        or not settings.postgres_db.startswith("oss_test_phase55_browser_")
        or settings.postgres_host != "postgres"
    ):
        raise SystemExit("Refusing to seed outside the disposable browser database")
    credentials = json.loads(Path("/run/dev-users.json").read_text(encoding="utf-8"))
    password = credentials.get("dev-engineer")
    if not isinstance(password, str) or not password:
        raise SystemExit("Required dev credential is unavailable")
    now = datetime.now(UTC)
    with Session(get_engine()) as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Disposable browser database is not empty")
        engineer = User(
            login="dev-engineer",
            display_name="Инженер Telegram T2",
            role=Role.ENGINEER,
            password_hash=hash_password(password),
            is_active=True,
        )
        db.add(engineer)
        db.flush()
        snapshot = SubscriberSnapshot(
            account_no="SYNTHETIC-T2",
            source="MOCK",
            fetched_at=now,
            expires_at=now + timedelta(hours=1),
            requested_by=engineer.id,
            full_name="Синтетический абонент Telegram T2",
            phone="+000000002",
            actual_address="г. Тест, ул. Монтажная",
            house_number="9",
            contracts=[],
            services=[{"id": "synthetic-field-service"}],
        )
        db.add(snapshot)
        db.flush()
        connection = ConnectionSnapshot(
            subscriber_snapshot_id=snapshot.id,
            access_type="ETHERNET",
            device="synthetic-switch",
            port="2",
            vlan="200",
            ip_groups=[],
        )
        db.add(connection)
        db.flush()
        ticket = Ticket(
            ticket_type=TicketType.SUBSCRIBER_LINKED,
            account_no=snapshot.account_no,
            subscriber_snapshot_id=snapshot.id,
            connection_snapshot_id=connection.id,
            selected_service_id="synthetic-field-service",
            subject="Синтетический ответ монтажника Telegram T2",
            description="Одноразовая браузерная проверка в изолированной базе.",
            status=TicketStatus.IN_PROGRESS,
            priority=TicketPriority.NORMAL,
            created_by=engineer.id,
            assigned_engineer_id=engineer.id,
            client_request_id=uuid4(),
            version=1,
        )
        db.add(ticket)
        db.flush()
        handoff = FieldWorkHandoff(
            ticket_id=ticket.id,
            ticket_version=1,
            reason="Синтетическая выездная проверка",
            delivery_status="SENT",
            work_status="REPORTED",
            chat_id="-5319791765",
            telegram_message_id=7001,
            message_text="Синтетическое сообщение Telegram T2",
            created_by=engineer.id,
            sent_by=engineer.id,
            created_at=now,
            send_started_at=now,
            sent_at=now,
            reported_at=now,
            updated_at=now,
        )
        db.add(handoff)
        db.flush()
        callback = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot",
            update_id=91001,
            kind="COMPLETION_CALLBACK",
            outcome="PROCESSED",
            handoff_id=handoff.id,
            sync_status="SYNCED",
        )
        reply = FieldDispatchUpdate(
            bot_identity="oss_field_service_bot",
            update_id=91002,
            kind="COMPLETION_REPORT",
            outcome="PROCESSED",
            handoff_id=handoff.id,
            sync_status="SYNCED",
        )
        db.add_all([callback, reply])
        db.flush()
        db.add(
            FieldWorkCompletionPrompt(
                handoff_id=handoff.id,
                callback_update_id=callback.id,
                chat_id=handoff.chat_id,
                sender_telegram_id=501,
                sender_name="Монтажник Тест",
                status="COMPLETED",
                telegram_message_id=7003,
                sent_at=now,
                completed_at=now + timedelta(seconds=1),
            )
        )
        db.add(
            FieldExternalMessage(
                handoff_id=handoff.id,
                update_record_id=reply.id,
                chat_id=handoff.chat_id,
                telegram_message_id=7002,
                sender_telegram_id=501,
                sender_name="Монтажник Тест",
                kind="COMPLETION_REPORT",
                body="Синтетический ответ: работы выполнены.",
                telegram_created_at=now + timedelta(seconds=1),
            )
        )
        db.commit()
    print("Disposable Telegram T2 browser data seeded")


if __name__ == "__main__":
    main()
