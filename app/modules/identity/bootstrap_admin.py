"""One-time interactive administrator bootstrap for an empty user table."""

import getpass
import hmac
import sys

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import ensure_non_root_runtime
from app.db.engine import get_engine
from app.modules.identity.crypto import hash_password, normalize_login, validate_password
from app.modules.identity.models import User
from app.modules.identity.roles import Role
from app.modules.identity.service import ADMIN_LOCK, audit


def main() -> int:
    ensure_non_root_runtime()
    get_settings()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("An interactive terminal is required", file=sys.stderr)
        return 2
    try:
        login = normalize_login(input("Administrator login: "))
        display_name = input("Display name: ").strip()
        if (
            not display_name
            or len(display_name) > 150
            or any(c in display_name for c in "\r\n\x00")
        ):
            raise ValueError("display name is invalid")
        password = validate_password(getpass.getpass("Password: "))
        confirmation = getpass.getpass("Repeat password: ")
        if not hmac.compare_digest(password, confirmation):
            raise ValueError("passwords do not match")
    except (EOFError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with Session(get_engine(), expire_on_commit=False) as db:
        db.execute(text("SELECT pg_advisory_xact_lock(:namespace, :key)"), ADMIN_LOCK)
        if db.scalar(select(func.count()).select_from(User)):
            print("Bootstrap refused: user table is not empty", file=sys.stderr)
            return 1
        user = User(
            login=login,
            display_name=display_name,
            role=Role.ADMIN,
            password_hash=hash_password(password),
        )
        db.add(user)
        try:
            db.flush()
            audit(db, "admin_bootstrap", "success", actor=user.id, target=user.id)
            db.commit()
        except IntegrityError:
            db.rollback()
            print("Bootstrap failed: identity conflict", file=sys.stderr)
            return 1
    print("Initial administrator created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
