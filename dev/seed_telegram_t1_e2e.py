"""Seed one synthetic linked ticket in a disposable browser database."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
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
    with Session(get_engine()) as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Disposable browser database is not empty")
        engineer = User(
            login="dev-engineer",
            display_name="Инженер Telegram T1",
            role=Role.ENGINEER,
            password_hash=hash_password(password),
            is_active=True,
        )
        db.add(engineer)
        db.flush()
        snapshot = SubscriberSnapshot(
            account_no="SYNTHETIC-T1",
            source="MOCK",
            fetched_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            requested_by=engineer.id,
            full_name="Синтетический абонент Telegram T1",
            phone="+000000000",
            actual_address="г. Тест, ул. Безопасная",
            house_number="20",
            contracts=[],
            services=[{"id": "synthetic-field-service"}],
        )
        db.add(snapshot)
        db.flush()
        connection = ConnectionSnapshot(
            subscriber_snapshot_id=snapshot.id,
            access_type="ETHERNET",
            device="synthetic-switch",
            port="1",
            vlan="100",
            ip_groups=[],
        )
        db.add(connection)
        db.flush()
        db.add(
            Ticket(
                ticket_type=TicketType.SUBSCRIBER_LINKED,
                account_no=snapshot.account_no,
                subscriber_snapshot_id=snapshot.id,
                connection_snapshot_id=connection.id,
                selected_service_id="synthetic-field-service",
                subject="Синтетическая выездная проверка Telegram T1",
                description="Одноразовая браузерная проверка в изолированной базе.",
                status=TicketStatus.IN_PROGRESS,
                priority=TicketPriority.NORMAL,
                created_by=engineer.id,
                assigned_engineer_id=engineer.id,
                client_request_id=uuid4(),
                version=1,
            )
        )
        db.commit()
    print("Disposable Telegram T1 browser data seeded")


if __name__ == "__main__":
    main()
