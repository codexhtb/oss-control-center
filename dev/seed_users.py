"""One-time synthetic identities for the isolated development database only."""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
from app.modules.identity.crypto import hash_password
from app.modules.identity.models import User
from app.modules.identity.roles import Role
from app.modules.identity.service import audit

USERS_PATH = Path("/run/dev-users.json")
EXPECTED_USERS = {
    "dev-admin": Role.ADMIN,
    "dev-operator": Role.OPERATOR,
    "dev-engineer": Role.ENGINEER,
}


def main() -> None:
    settings = get_settings()
    if (
        settings.app_env != "dev"
        or settings.postgres_db != "oss_control_dev"
        or settings.postgres_host != "postgres"
        or settings.auth_public_origin != "https://172.17.7.2:8443"
    ):
        raise SystemExit("Refusing to seed outside the isolated dev database")

    credentials = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    if set(credentials) != set(EXPECTED_USERS):
        raise SystemExit("Dev identity manifest does not match expected roles")
    with Session(get_engine()) as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Dev users already exist; no changes made")
        for login, role in EXPECTED_USERS.items():
            password = credentials[login]
            if not isinstance(password, str):
                raise SystemExit("Invalid dev identity manifest")
            user = User(
                login=login,
                display_name=f"Synthetic {role.value.title()}",
                role=role,
                password_hash=hash_password(password),
            )
            db.add(user)
            db.flush()
            audit(db, "dev_user_seeded", "success", actor=user.id, target=user.id)
        db.commit()
    print("Created three synthetic dev identities")


if __name__ == "__main__":
    main()
