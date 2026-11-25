"""HTTPS regression using temporary staff identities and owner-held smoke secrets.

No subscriber lookup or password is read. Existing staff passwords are untouched.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role
from app.modules.identity.schemas import CreateUserRequest
from app.modules.identity.service import create_user, delete_user


def main():
    settings = get_settings()
    if (os.environ.get("OSS_LIVE_SMOKE") != "1"
            or settings.auth_public_origin != "https://sd.oss-ab.net"
            or settings.subscriber_cabinet_mode != "disabled"):
        raise SystemExit("Live disabled-mode smoke guard rejected configuration")
    manifest = json.loads(Path("/run/staff-smoke.json").read_text())
    engine = get_engine()
    created = []
    with Session(engine) as db:
        admin = next((user for user in db.scalars(select(User).where(
            User.role == Role.ADMIN, User.is_active.is_(True), User.deleted_at.is_(None)))
            if has_permission(db, user, Permission.IDENTITY_MANAGE)), None)
        if admin is None:
            raise RuntimeError("Administrative release identity unavailable")
        admin_id = admin.id
    try:
        for role in Role:
            password = manifest[next(key for key in manifest if role.value in key)]
            login = f"cabinet-release-smoke-{role.value}"
            with Session(engine) as db:
                if db.scalar(select(User.id).where(User.login == login, User.deleted_at.is_(None))):
                    raise RuntimeError("Live smoke identity already exists; refusing overwrite")
                user, _ = create_user(db, db.get(User, admin_id), CreateUserRequest(
                    login=login, display_name=f"Cabinet release smoke {role.value}", role=role,
                    password=SecretStr(password), permissions=[
                        Permission.SUBSCRIBER_READ, Permission.TICKET_READ],
                    must_change_password=False,
                ))
                created.append(user.id)
            with httpx.Client(base_url=settings.auth_public_origin, verify=True,
                              trust_env=False, timeout=15, headers={
                                  "Origin": settings.auth_public_origin}) as client:
                assert client.post("/api/v1/auth/login", json={
                    "login": login, "password": password}).status_code == 200
                me = client.get("/api/v1/auth/me")
                assert me.status_code == 200
                assert Permission.SUBSCRIBER_PASSWORD_READ not in me.json()["permissions"]
                key = uuid4()
                path = f"/api/v1/subscriber-lookups/{key}/cabinet"
                assert client.get(path).status_code == 404
                result = client.post(path + "/password", headers={
                    "X-CSRF-Token": client.cookies["oss_csrf"]})
                assert result.status_code == 403
                assert result.headers["cache-control"] == "no-store"
                assert client.get("/api/v1/tickets?size=1").status_code == 200
                print(f"PASS live {role.value}: HTTPS login, cabinet RBAC, no-store, tickets")
    finally:
        for key in created:
            with Session(engine) as db:
                delete_user(db, db.get(User, admin_id), key)
        print("Live smoke staff identities removed; sessions revoked; no subscriber secrets read")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Live cabinet smoke failed (source and credentials suppressed)") from None
