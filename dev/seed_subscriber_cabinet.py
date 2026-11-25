"""Reuse protected DEV staff credentials only in the dedicated empty DEV database."""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.identity.service import audit


def main():
    settings = get_settings()
    if (settings.app_env != "dev" or settings.postgres_db != "oss_test_subscriber_cabinet"
            or settings.auth_public_origin != "https://172.17.7.2:9443"):
        raise SystemExit("Dedicated cabinet DEV database/origin required")
    credentials = json.loads(Path("/run/dev-users.json").read_text())
    with Session(get_engine()) as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Refusing to overwrite existing DEV identities")
        for role in Role:
            login = f"dev-{role.value}"
            user = User(login=login, display_name=f"DEV {role.value}", role=role,
                        password_hash=hash_password(credentials[login]),
                        permissions_customized=True)
            db.add(user)
            db.flush()
            grants = set(permissions_for(role))
            if role in {Role.ADMIN, Role.ENGINEER}:
                grants.add(Permission.SUBSCRIBER_PASSWORD_READ)
            db.add_all(UserPermission(user_id=user.id, permission=p) for p in grants)
            audit(db, "dev_user_seeded", "success", actor=user.id, target=user.id)
        db.commit()
    print("Dedicated DEV identities ready; no subscriber credentials created or stored")


if __name__ == "__main__":
    main()
