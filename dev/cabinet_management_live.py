"""Live operator acceptance; secret values stay in process memory only."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_engine
from app.modules.identity.access import has_permission
from app.modules.identity.models import IdentityAuditEvent, User
from app.modules.identity.roles import Permission, Role
from app.modules.identity.schemas import CreateUserRequest
from app.modules.identity.service import create_user, delete_user
from app.modules.tickets.models import SubscriberSnapshot

LOGIN = "cabinet-live-smoke-operator"


def main():
    settings = get_settings()
    assert os.environ.get("OSS_CABINET_LIVE_SMOKE") == "1"
    assert settings.auth_public_origin in {"https://sd.oss-ab.net", "https://172.17.7.2:9443"}
    assert settings.subscriber_cabinet_mode == "management"
    engine = get_engine()
    with Session(engine) as db:
        admin = next(
            (
                u
                for u in db.scalars(
                    select(User).where(
                        User.role == Role.ADMIN, User.is_active.is_(True), User.deleted_at.is_(None)
                    )
                )
                if has_permission(db, u, Permission.IDENTITY_MANAGE)
            ),
            None,
        )
        assert admin is not None
        admin_id = admin.id
        if sys.argv[1] == "cleanup":
            target = db.get(User, UUID(sys.argv[2]))
            assert target is not None and target.login == LOGIN
            if target.deleted_at is None:
                delete_user(db, admin, target.id)
            print("PASS: temporary operator removed and sessions revoked")
            return
        assert sys.argv[1] == "prepare"
        assert (
            db.scalar(select(User.id).where(User.login == LOGIN, User.deleted_at.is_(None))) is None
        )
    dev_engine = create_engine(settings.sqlalchemy_url.set(database="oss_test_subscriber_cabinet"))
    try:
        with Session(dev_engine) as db:
            # Reuse the account exercised manually by the owner on isolated DEV.
            account_no = db.scalar(
                select(SubscriberSnapshot.account_no)
                .join(User, User.id == SubscriberSnapshot.requested_by)
                .where(
                    SubscriberSnapshot.source == "NETUP",
                    User.login.in_(
                        [
                            "dev-admin",
                            "dev-engineer",
                            "dev-operator",
                        ]
                    ),
                )
                .order_by(SubscriberSnapshot.fetched_at.desc())
                .limit(1)
            )
            assert account_no
    finally:
        dev_engine.dispose()
    manifest = json.loads(Path("/run/staff-smoke.json").read_text())
    password = manifest[next(key for key in manifest if "operator" in key)]
    created_id = None
    try:
        with Session(engine) as db:
            user, _ = create_user(
                db,
                db.get(User, admin_id),
                CreateUserRequest(
                    login=LOGIN,
                    display_name="Cabinet live acceptance",
                    role=Role.OPERATOR,
                    password=SecretStr(password),
                    must_change_password=False,
                ),
            )
            created_id = user.id
        verify = "/run/dev-ca.crt" if settings.auth_public_origin.endswith(":9443") else True
        with httpx.Client(
            base_url=settings.auth_public_origin,
            verify=verify,
            trust_env=False,
            timeout=30,
            headers={"Origin": settings.auth_public_origin},
        ) as client:
            assert (
                client.post(
                    "/api/v1/auth/login",
                    json={
                        "login": LOGIN,
                        "password": password,
                    },
                ).status_code
                == 200
            )
            grants = client.get("/api/v1/auth/me").json()["permissions"]
            assert Permission.SUBSCRIBER_PASSWORD_READ in grants
            headers = {"X-CSRF-Token": client.cookies["oss_csrf"]}
            looked = client.post(
                "/api/v1/subscriber-lookups", headers=headers, json={"account_no": account_no}
            )
            assert looked.status_code == 200
            lookup_id = UUID(looked.json()["lookup_id"])
            path = f"/api/v1/subscriber-lookups/{lookup_id}/cabinet"
            response = client.get(path)
            assert response.status_code == 200 and response.json()["login"]
            assert response.json()["password_available"] is True
            assert client.post(path + "/password").status_code == 403
            revealed = client.post(path + "/password", headers=headers)
            assert revealed.status_code == 200 and revealed.json()["password"]
            assert "no-store" in revealed.headers["cache-control"]
            assert revealed.headers["pragma"] == "no-cache"
            with Session(engine) as db:
                snapshot = db.get(SubscriberSnapshot, lookup_id)
                assert all("password" not in col.name for col in snapshot.__table__.columns)
                events = db.scalars(
                    select(IdentityAuditEvent).where(
                        IdentityAuditEvent.actor_user_id == created_id,
                        IdentityAuditEvent.action == "subscriber_password_view",
                        IdentityAuditEvent.outcome == "success",
                    )
                ).all()
                assert any(e.details == {"lookup_id": str(lookup_id)} for e in events)
            # No login, subscriber data or secret is emitted to the caller.
            print(json.dumps({"user_id": str(created_id), "lookup_id": str(lookup_id)}))
    except Exception:
        if created_id:
            with Session(engine) as db:
                delete_user(db, db.get(User, admin_id), created_id)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Live operator acceptance failed; details suppressed") from None
