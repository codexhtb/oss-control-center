"""Cookie session endpoints and administrator-only identity operations."""

import hmac
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.session import get_session
from app.modules.identity.crypto import hash_token
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.identity.schemas import (
    ChangeRoleRequest,
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    PermissionsResponse,
    UserResponse,
)
from app.modules.identity.service import (
    AuthContext,
    audit,
    change_user_role,
    create_user,
    current_identity,
    deactivate_user,
    login_user,
    revoke_session,
)

router = APIRouter(tags=["identity"])
SESSION_COOKIE = "oss_session"
CSRF_COOKIE = "oss_csrf"
COOKIE_AGE_SECONDS = 8 * 60 * 60
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_origin(request: Request, settings: SettingsDep) -> None:
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if not hmac.compare_digest(origin, settings.auth_public_origin) or (
        host != urlsplit(settings.auth_public_origin).netloc
    ):
        raise ApiError(403, "invalid_origin", "Request origin is not allowed")


OriginDep = Annotated[None, Depends(require_origin)]


def get_current_identity(request: Request, db: SessionDep) -> AuthContext:
    return current_identity(db, request.cookies.get(SESSION_COOKIE))


CurrentIdentityDep = Annotated[AuthContext, Depends(get_current_identity)]


def require_csrf(
    request: Request,
    identity: CurrentIdentityDep,
    db: SessionDep,
    _: OriginDep,
) -> AuthContext:
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if (
        not header
        or not cookie
        or len(header) > 256
        or not hmac.compare_digest(header, cookie)
        or not hmac.compare_digest(hash_token(header), identity.session.csrf_hash)
    ):
        audit(db, "csrf_rejected", "denied", actor=identity.user.id, target=identity.user.id)
        db.commit()
        raise ApiError(403, "invalid_csrf", "CSRF check failed")
    return identity


CsrfIdentityDep = Annotated[AuthContext, Depends(require_csrf)]


def require_permission(permission: Permission):
    def dependency(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
        if permission not in permissions_for(Role(identity.user.role)):
            audit(db, "permission_denied", "denied", actor=identity.user.id)
            db.commit()
            raise ApiError(403, "forbidden", "Permission denied")
        return identity

    return dependency


require_identity_manage = require_permission(Permission.IDENTITY_MANAGE)
ManagedIdentityDep = Annotated[AuthContext, Depends(require_identity_manage)]


def public_user(user) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)


def public_me(user) -> MeResponse:
    return MeResponse(
        user=public_user(user),
        permissions=sorted(permissions_for(Role(user.role))),
    )


@router.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    _: OriginDep,
) -> LoginResponse:
    issued = login_user(db, settings, request, payload.login, payload.password.get_secret_value())
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        max_age=COOKIE_AGE_SECONDS,
        path="/api/v1",
        httponly=True,
        secure=settings.secure_cookie,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=COOKIE_AGE_SECONDS,
        path="/",
        httponly=False,
        secure=settings.secure_cookie,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    me = public_me(issued.user)
    return LoginResponse(**me.model_dump(), expires_at=issued.expires_at)


@router.post("/auth/logout")
def logout(
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
) -> dict[str, str]:
    revoke_session(db, identity)
    response.delete_cookie(SESSION_COOKIE, path="/api/v1")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "logged_out"}


@router.get("/auth/me", response_model=MeResponse)
def me(identity: CurrentIdentityDep) -> MeResponse:
    return public_me(identity.user)


@router.get("/auth/permissions", response_model=PermissionsResponse)
def my_permissions(identity: CurrentIdentityDep) -> PermissionsResponse:
    return PermissionsResponse(permissions=sorted(permissions_for(Role(identity.user.role))))


@router.post("/users", status_code=201, response_model=UserResponse)
def admin_create_user(
    payload: CreateUserRequest,
    identity: ManagedIdentityDep,
    db: SessionDep,
) -> UserResponse:
    return public_user(create_user(db, identity.user, payload))


@router.post("/users/{user_id}/deactivate", response_model=UserResponse)
def admin_deactivate_user(
    user_id: UUID,
    identity: ManagedIdentityDep,
    db: SessionDep,
) -> UserResponse:
    return public_user(deactivate_user(db, identity.user, user_id))


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def admin_change_role(
    user_id: UUID,
    payload: ChangeRoleRequest,
    identity: ManagedIdentityDep,
    db: SessionDep,
) -> UserResponse:
    return public_user(change_user_role(db, identity.user, user_id, payload.role))
