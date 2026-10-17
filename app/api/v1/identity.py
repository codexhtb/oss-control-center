"""Cookie session endpoints and administrator-only identity operations."""

import hmac
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.session import get_session
from app.modules.identity.access import effective_permissions
from app.modules.identity.crypto import hash_token
from app.modules.identity.models import IdentityAuditEvent, User
from app.modules.identity.roles import Permission
from app.modules.identity.schemas import (
    ChangePasswordRequest,
    ChangeRoleRequest,
    CreatedUserResponse,
    CreateUserRequest,
    IdentityAuditView,
    LoginRequest,
    LoginResponse,
    ManagedUserResponse,
    MeResponse,
    PermissionsResponse,
    ResetPasswordResponse,
    SetPermissionsRequest,
    SetUserPasswordRequest,
    UpdateUserRequest,
    UserResponse,
)
from app.modules.identity.service import (
    AuthContext,
    audit,
    change_own_password,
    change_user_role,
    create_user,
    current_identity,
    deactivate_user,
    login_user,
    reactivate_user,
    reset_password,
    revoke_session,
    set_user_password,
    set_user_permissions,
    update_user,
)
from app.modules.tickets.schemas import Page

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


def get_prechange_identity(request: Request, db: SessionDep) -> AuthContext:
    return current_identity(db, request.cookies.get(SESSION_COOKIE))


PrechangeIdentityDep = Annotated[AuthContext, Depends(get_prechange_identity)]


def get_current_identity(identity: PrechangeIdentityDep) -> AuthContext:
    if identity.user.must_change_password:
        raise ApiError(403, "password_change_required", "Change password before continuing")
    return identity


CurrentIdentityDep = Annotated[AuthContext, Depends(get_current_identity)]


def _check_csrf(
    request: Request,
    identity: AuthContext,
    db: SessionDep,
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


def require_csrf(
    request: Request, identity: CurrentIdentityDep, db: SessionDep, _: OriginDep
) -> AuthContext:
    return _check_csrf(request, identity, db)


def require_prechange_csrf(
    request: Request, identity: PrechangeIdentityDep, db: SessionDep, _: OriginDep
) -> AuthContext:
    return _check_csrf(request, identity, db)


CsrfIdentityDep = Annotated[AuthContext, Depends(require_csrf)]
PrechangeCsrfDep = Annotated[AuthContext, Depends(require_prechange_csrf)]


def require_permission(permission: Permission):
    def dependency(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
        if permission not in effective_permissions(db, identity.user):
            audit(db, "permission_denied", "denied", actor=identity.user.id)
            db.commit()
            raise ApiError(403, "forbidden", "Permission denied")
        return identity

    return dependency


def require_read_permission(permission: Permission):
    def dependency(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
        if permission not in effective_permissions(db, identity.user):
            raise ApiError(403, "forbidden", "Permission denied")
        return identity

    return dependency


require_identity_manage = require_permission(Permission.IDENTITY_MANAGE)
ManagedIdentityDep = Annotated[AuthContext, Depends(require_identity_manage)]
ReadUsersDep = Annotated[AuthContext, Depends(require_read_permission(Permission.USERS_READ))]


def public_user(user) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)


def public_me(db: Session, user: User) -> MeResponse:
    return MeResponse(
        user=public_user(user),
        permissions=sorted(effective_permissions(db, user)),
    )


def managed_user(db: Session, user: User) -> ManagedUserResponse:
    return ManagedUserResponse(
        **public_user(user).model_dump(), permissions=sorted(effective_permissions(db, user))
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
    me = public_me(db, issued.user)
    return LoginResponse(**me.model_dump(), expires_at=issued.expires_at)


@router.post("/auth/logout")
def logout(
    response: Response,
    identity: PrechangeCsrfDep,
    db: SessionDep,
) -> dict[str, str]:
    revoke_session(db, identity)
    response.delete_cookie(SESSION_COOKIE, path="/api/v1")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "logged_out"}


@router.get("/auth/me", response_model=MeResponse)
def me(identity: PrechangeIdentityDep, db: SessionDep) -> MeResponse:
    return public_me(db, identity.user)


@router.get("/auth/permissions", response_model=PermissionsResponse)
def my_permissions(identity: CurrentIdentityDep, db: SessionDep) -> PermissionsResponse:
    return PermissionsResponse(permissions=sorted(effective_permissions(db, identity.user)))


@router.post("/auth/password")
def change_password(
    payload: ChangePasswordRequest, response: Response, identity: PrechangeCsrfDep, db: SessionDep
) -> dict[str, str]:
    change_own_password(
        db,
        identity,
        payload.current_password.get_secret_value(),
        payload.new_password.get_secret_value(),
    )
    response.delete_cookie(SESSION_COOKIE, path="/api/v1")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "password_changed"}


@router.get("/users", response_model=Page[ManagedUserResponse])
def users_list(
    identity: ReadUsersDep,
    db: SessionDep,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    total = db.scalar(select(func.count()).select_from(User)) or 0
    users = db.scalars(
        select(User).order_by(User.display_name, User.id).offset((page - 1) * size).limit(size)
    ).all()
    return {
        "items": [managed_user(db, user) for user in users],
        "page": page,
        "size": size,
        "total": total,
    }


@router.get("/users/audit", response_model=Page[IdentityAuditView])
def users_audit(
    identity: CurrentIdentityDep,
    db: SessionDep,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    if not {Permission.IDENTITY_MANAGE, Permission.TICKET_AUDIT_READ} <= effective_permissions(
        db, identity.user
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    total = db.scalar(select(func.count()).select_from(IdentityAuditEvent)) or 0
    items = db.scalars(
        select(IdentityAuditEvent)
        .order_by(IdentityAuditEvent.occurred_at.desc(), IdentityAuditEvent.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    ids = {
        value
        for item in items
        for value in (item.actor_user_id, item.target_user_id)
        if value is not None
    }
    names = dict(db.execute(select(User.id, User.display_name).where(User.id.in_(ids))).all())
    return {
        "items": [
            IdentityAuditView.model_validate(item, from_attributes=True).model_copy(
                update={
                    "actor_name": names.get(item.actor_user_id),
                    "target_name": names.get(item.target_user_id),
                }
            )
            for item in items
        ],
        "page": page,
        "size": size,
        "total": total,
    }


@router.get("/users/{user_id}", response_model=ManagedUserResponse)
def users_get(user_id: UUID, identity: ReadUsersDep, db: SessionDep):
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user_not_found", "User not found")
    return managed_user(db, user)


@router.post("/users", status_code=201, response_model=CreatedUserResponse)
def admin_create_user(
    payload: CreateUserRequest,
    response: Response,
    identity: ManagedIdentityDep,
    db: SessionDep,
) -> CreatedUserResponse:
    user, temporary_password = create_user(db, identity.user, payload)
    response.headers["Cache-Control"] = "no-store"
    return CreatedUserResponse(
        **managed_user(db, user).model_dump(), temporary_password=temporary_password
    )


@router.patch("/users/{user_id}", response_model=ManagedUserResponse)
def admin_update_user(
    user_id: UUID, payload: UpdateUserRequest, identity: ManagedIdentityDep, db: SessionDep
):
    return managed_user(db, update_user(db, identity.user, user_id, payload))


@router.put("/users/{user_id}/permissions", response_model=ManagedUserResponse)
def admin_set_permissions(
    user_id: UUID, payload: SetPermissionsRequest, identity: ManagedIdentityDep, db: SessionDep
):
    return managed_user(
        db, set_user_permissions(db, identity.user, user_id, set(payload.permissions))
    )


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def admin_reset_password(
    user_id: UUID, response: Response, identity: ManagedIdentityDep, db: SessionDep
):
    password = reset_password(db, identity.user, user_id)
    response.headers["Cache-Control"] = "no-store"
    return ResetPasswordResponse(temporary_password=password)


@router.put("/users/{user_id}/password")
def admin_set_password(
    user_id: UUID,
    payload: SetUserPasswordRequest,
    response: Response,
    identity: ManagedIdentityDep,
    db: SessionDep,
) -> dict[str, str]:
    set_user_password(
        db,
        identity.user,
        user_id,
        payload.new_password.get_secret_value(),
        payload.must_change_password,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"status": "password_changed"}


@router.post("/users/{user_id}/reactivate", response_model=ManagedUserResponse)
def admin_reactivate_user(user_id: UUID, identity: ManagedIdentityDep, db: SessionDep):
    return managed_user(db, reactivate_user(db, identity.user, user_id))


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
