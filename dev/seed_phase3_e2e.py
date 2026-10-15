"""Seed only an isolated, disposable Phase 3 browser test database."""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Role, permissions_for


def main() -> None:
    settings = get_settings()
    if (
        settings.app_env != "dev"
        or not settings.postgres_db.startswith("oss_test_phase3_browser_")
        or settings.postgres_host != "postgres"
        or settings.auth_public_origin != "https://172.17.7.2:8443"
    ):
        raise SystemExit("Refusing to seed outside the isolated Phase 3 browser database")
    credentials = json.loads(Path("/run/dev-users.json").read_text(encoding="utf-8"))
    password = credentials.get("dev-admin")
    if not isinstance(password, str):
        raise SystemExit("Dev administrator credential is unavailable")
    with Session(get_engine()) as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Browser test database is not empty")
        user = User(
            login="dev-admin",
            display_name="Администратор теста",
            role=Role.ADMIN,
            password_hash=hash_password(password),
            permissions_customized=True,
        )
        db.add(user)
        db.flush()
        db.add_all(
            UserPermission(user_id=user.id, permission=permission)
            for permission in permissions_for(Role.ADMIN)
        )
        db.commit()
    print("Isolated browser administrator seeded")


if __name__ == "__main__":
    main()
