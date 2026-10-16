"""Seed four synthetic staff only in a disposable Phase 5.5 browser database."""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.tickets.models import TicketCategory


def main() -> None:
    settings = get_settings()
    if (
        settings.app_env != "dev"
        or not settings.postgres_db.startswith("oss_test_phase55_browser_")
        or settings.postgres_host != "postgres"
        or settings.auth_public_origin != "https://172.17.7.2:8443"
    ):
        raise SystemExit("Refusing to seed outside the disposable browser database")
    stored = json.loads(Path("/run/dev-users.json").read_text(encoding="utf-8"))
    credentials = (
        ("dev-operator", "Тестовый оператор", Role.OPERATOR, stored.get("dev-operator")),
        ("dev-engineer", "Инженер теста А", Role.ENGINEER, stored.get("dev-engineer")),
        ("queue-engineer-2", "Инженер теста Б", Role.ENGINEER, stored.get("dev-admin")),
        ("dev-admin", "Администратор теста", Role.ADMIN, stored.get("dev-admin")),
    )
    if any(not isinstance(password, str) or not password for _, _, _, password in credentials):
        raise SystemExit("A required dev credential is unavailable")
    with Session(get_engine()) as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Disposable browser database is not empty")
        users = [
            User(
                login=login,
                display_name=display_name,
                role=role,
                password_hash=hash_password(password),
                is_active=True,
            )
            for login, display_name, role, password in credentials
        ]
        db.add_all(users)
        db.flush()
        callback_staff = users[0]
        callback_staff.permissions_customized = True
        db.add_all(
            UserPermission(user_id=callback_staff.id, permission=permission)
            for permission in permissions_for(Role.OPERATOR) | {Permission.TICKET_CALLBACK}
        )
        db.add(
            TicketCategory(
                name="Синтетическая проблема подключения",
                sort_order=10,
                is_active=True,
                is_other=False,
                version=1,
            )
        )
        db.commit()
    print("Disposable browser staff seeded")


if __name__ == "__main__":
    main()
