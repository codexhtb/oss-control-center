"""Current grants are read from PostgreSQL, not inferred from a session's role."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.models import User, UserPermission
from app.modules.identity.roles import Permission, Role, permissions_for


def effective_permissions(db: Session, user: User) -> frozenset[Permission]:
    if not user.permissions_customized:
        # Compatibility for code-created users predating the explicit grant API.
        return permissions_for(Role(user.role))
    return frozenset(
        Permission(value)
        for value in db.scalars(
            select(UserPermission.permission).where(UserPermission.user_id == user.id)
        )
    )


def has_permission(db: Session, user: User, permission: Permission) -> bool:
    return permission in effective_permissions(db, user)
