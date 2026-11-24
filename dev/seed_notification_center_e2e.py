"""Seed only an empty disposable acceptance database using existing DEV identities."""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.engine import get_engine
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role, permissions_for


def main():
    settings = Settings.from_env()
    if settings.app_env != "dev" or settings.postgres_db != "oss_test_notification_center":
        raise SystemExit("Disposable acceptance database required")
    credentials = json.loads(Path("/run/dev-users.json").read_text())
    with Session(get_engine()) as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Acceptance database must be empty")
        for login, role in (
            ("dev-admin", Role.ADMIN), ("dev-engineer", Role.ENGINEER),
            ("dev-operator", Role.OPERATOR),
        ):
            user = User(login=login, display_name=f"Synthetic {role.value}", role=role,
                        password_hash=hash_password(credentials[login]),
                        permissions_customized=True)
            db.add(user)
            db.flush()
            grants = set(permissions_for(role))
            if role == Role.OPERATOR:
                grants.add(Permission.TASK_EXECUTE)
            db.add_all(UserPermission(user_id=user.id, permission=item) for item in grants)
        db.commit()
    print("Disposable acceptance identities ready; existing passwords reused")


if __name__ == "__main__":
    main()
