"""Audit operator grants and reverse only additions made by this release."""

import json
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_engine
from app.modules.identity.access import has_permission
from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.identity.service import (
    _lock_admin_changes,
    audit,
    grant_operator_cabinet_access,
)


def main():
    with Session(get_engine()) as db:
        _lock_admin_changes(db)
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
        if sys.argv[1] == "apply":
            targets = db.scalars(
                select(User)
                .where(User.role == Role.OPERATOR, User.deleted_at.is_(None))
                .order_by(User.id)
                .with_for_update()
            ).all()
            required = {Permission.SUBSCRIBER_READ, Permission.SUBSCRIBER_PASSWORD_READ}
            receipt = []
            for target in targets:
                stored = set(
                    db.scalars(
                        select(UserPermission.permission).where(UserPermission.user_id == target.id)
                    )
                )
                desired = (
                    stored | required
                    if target.permissions_customized
                    else (set(permissions_for(Role.OPERATOR)) | required)
                )
                receipt.append(
                    {
                        "id": str(target.id),
                        "added": sorted(desired - stored),
                        "customized": target.permissions_customized,
                    }
                )
            # The caller writes this metadata into a protected file before commit.
            print(json.dumps(receipt), flush=True)
            grant_operator_cabinet_access(db, admin)
        elif sys.argv[1] == "rollback":
            for item in json.load(sys.stdin):
                target = db.scalar(
                    select(User).where(User.id == UUID(item["id"])).with_for_update()
                )
                if target is None:
                    continue
                db.query(UserPermission).filter(
                    UserPermission.user_id == target.id,
                    UserPermission.permission.in_(item["added"]),
                ).delete(synchronize_session=False)
                remaining = set(
                    db.scalars(
                        select(UserPermission.permission).where(UserPermission.user_id == target.id)
                    )
                )
                if not item["customized"] and not remaining:
                    target.permissions_customized = False
                if item["added"]:
                    audit(
                        db,
                        "permissions_changed",
                        "success",
                        actor=admin.id,
                        target=target.id,
                        details={"removed": item["added"], "reason": "cabinet_release_rollback"},
                    )
            db.commit()
            print("PASS: release-added grants reverted; other grants preserved")
        elif sys.argv[1] == "check":
            users = db.scalars(
                select(User).where(User.role == Role.OPERATOR, User.deleted_at.is_(None))
            ).all()
            assert users
            assert all(
                has_permission(db, u, Permission.SUBSCRIBER_READ)
                and has_permission(db, u, Permission.SUBSCRIBER_PASSWORD_READ)
                for u in users
            )
            print("PASS: all existing operators have cabinet login/password grants:", len(users))
        else:
            raise ValueError("Unsupported release action")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Cabinet permission release failed; details suppressed") from None
