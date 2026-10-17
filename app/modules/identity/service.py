"""Transactional identity operations and persistent login throttling."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.modules.identity.access import effective_permissions, has_permission
from app.modules.identity.crypto import (
    fingerprint,
    hash_password,
    hash_token,
    new_token,
    normalize_login,
    verify_password,
)
from app.modules.identity.models import (
    AuthSession,
    IdentityAuditEvent,
    LoginBucket,
    User,
    UserPermission,
)
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.identity.schemas import CreateUserRequest, UpdateUserRequest

SESSION_LIFETIME = timedelta(hours=8)
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_THRESHOLDS = {"login": 5, "source": 20}
ADMIN_LOCK = {"namespace": 18384, "key": 1202}


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


@dataclass(frozen=True)
class IssuedSession:
    user: User
    token: str
    csrf_token: str
    expires_at: datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def audit(
    db: Session,
    action: str,
    outcome: str,
    *,
    actor: UUID | None = None,
    target: UUID | None = None,
    login_digest: str | None = None,
    source_digest: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        IdentityAuditEvent(
            action=action,
            outcome=outcome,
            actor_user_id=actor,
            target_user_id=target,
            login_digest=login_digest,
            source_digest=source_digest,
            details=details,
        )
    )


def source_address(request: Request) -> str:
    # Uvicorn runs with --no-proxy-headers; X-Forwarded-For is never a rate key.
    return request.client.host if request.client else "unknown"


def _lock_login_buckets(db: Session, login_key: str, source_key: str) -> dict[str, LoginBucket]:
    now = utc_now()
    keys = {"login": login_key, "source": source_key}
    buckets: dict[str, LoginBucket] = {}
    for kind in ("login", "source"):
        db.execute(
            pg_insert(LoginBucket)
            .values(
                kind=kind,
                key_digest=keys[kind],
                failures=0,
                window_started_at=now,
            )
            .on_conflict_do_nothing(index_elements=["kind", "key_digest"])
        )
        buckets[kind] = db.execute(
            select(LoginBucket)
            .where(LoginBucket.kind == kind, LoginBucket.key_digest == keys[kind])
            .with_for_update()
        ).scalar_one()
    return buckets


def _record_failure(buckets: dict[str, LoginBucket], now: datetime) -> None:
    for kind, bucket in buckets.items():
        if now - bucket.window_started_at >= LOGIN_WINDOW:
            bucket.window_started_at = now
            bucket.failures = 0
            bucket.blocked_until = None
        bucket.failures += 1
        if bucket.failures >= LOGIN_THRESHOLDS[kind]:
            bucket.blocked_until = now + LOGIN_WINDOW


def login_user(
    db: Session, settings: Settings, request: Request, login: str, password: str
) -> IssuedSession:
    try:
        canonical_login = normalize_login(login)
        valid_login = True
    except ValueError:
        canonical_login = login.strip().casefold()[:256]
        valid_login = False
    secret = settings.auth_hash_key.get_secret_value()
    login_key = fingerprint("login", canonical_login, secret)
    source_key = fingerprint("source", source_address(request), secret)
    buckets = _lock_login_buckets(db, login_key, source_key)
    now = utc_now()
    if any(bucket.blocked_until and bucket.blocked_until > now for bucket in buckets.values()):
        audit(db, "login", "rate_limited", login_digest=login_key, source_digest=source_key)
        db.commit()
        raise ApiError(429, "login_rate_limited", "Try again later")

    user = (
        db.execute(
            select(User).where(User.login == canonical_login).with_for_update()
        ).scalar_one_or_none()
        if valid_login
        else None
    )
    if not verify_password(user.password_hash if user else None, password) or not (
        user and user.is_active
    ):
        _record_failure(buckets, now)
        audit(
            db,
            "login",
            "denied",
            target=user.id if user else None,
            login_digest=login_key,
            source_digest=source_key,
        )
        db.commit()
        raise ApiError(401, "invalid_credentials", "Invalid credentials")

    buckets["login"].failures = 0
    buckets["login"].window_started_at = now
    buckets["login"].blocked_until = None
    token = new_token()
    csrf_token = new_token()
    expires_at = now + SESSION_LIFETIME
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_hash=hash_token(csrf_token),
            expires_at=expires_at,
        )
    )
    audit(
        db,
        "login",
        "success",
        actor=user.id,
        target=user.id,
        login_digest=login_key,
        source_digest=source_key,
    )
    db.commit()
    return IssuedSession(user=user, token=token, csrf_token=csrf_token, expires_at=expires_at)


def current_identity(db: Session, token: str | None) -> AuthContext:
    if not token or len(token) > 256:
        raise ApiError(401, "unauthenticated", "Authentication required")
    auth_session = db.execute(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if not auth_session or auth_session.revoked_at or auth_session.expires_at <= utc_now():
        raise ApiError(401, "unauthenticated", "Authentication required")
    user = db.get(User, auth_session.user_id)
    if not user or not user.is_active:
        raise ApiError(401, "unauthenticated", "Authentication required")
    return AuthContext(user=user, session=auth_session)


def revoke_session(db: Session, identity: AuthContext) -> None:
    identity.session.revoked_at = utc_now()
    audit(db, "session_revoked", "success", actor=identity.user.id, target=identity.user.id)
    db.commit()


def _lock_admin_changes(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:namespace, :key)"), ADMIN_LOCK)


def _ensure_actor_can_manage(db: Session, actor: User) -> None:
    current = db.execute(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        current is None
        or not current.is_active
        or not has_permission(db, current, Permission.IDENTITY_MANAGE)
    ):
        raise ApiError(403, "forbidden", "Permission denied")


def _get_target_for_update(db: Session, user_id: UUID) -> User:
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if user is None:
        raise ApiError(404, "user_not_found", "User not found")
    return user


def _ensure_admin_remains(db: Session, target: User) -> None:
    if target.role == Role.ADMIN and target.is_active:
        other_admins = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.role == Role.ADMIN,
                User.is_active.is_(True),
                User.id != target.id,
                (User.permissions_customized.is_(False))
                | (
                    select(UserPermission.user_id)
                    .where(
                        UserPermission.user_id == User.id,
                        UserPermission.permission == Permission.IDENTITY_MANAGE,
                    )
                    .exists()
                ),
            )
        )
        if not other_admins:
            raise ApiError(409, "last_admin", "Last active administrator cannot be changed")


def _revoke_user_sessions(db: Session, target: User, actor: User) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == target.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    audit(db, "sessions_revoked", "success", actor=actor.id, target=target.id)


def create_user(db: Session, actor: User, request: CreateUserRequest) -> tuple[User, str | None]:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    supplied_password = (
        request.password.get_secret_value() if request.password is not None else None
    )
    password = supplied_password or secrets.token_urlsafe(24)
    temporary_password = None if supplied_password is not None else password
    grants = (
        set(request.permissions)
        if request.permissions is not None
        else set(permissions_for(request.role))
    )
    user = User(
        login=request.login,
        display_name=request.display_name,
        role=request.role,
        password_hash=hash_password(password),
        must_change_password=(
            request.must_change_password if supplied_password is not None else True
        ),
        permissions_customized=True,
    )
    db.add(user)
    try:
        db.flush()
        db.add_all(UserPermission(user_id=user.id, permission=value) for value in grants)
        audit(
            db,
            "user_created",
            "success",
            actor=actor.id,
            target=user.id,
            details={
                "login": user.login,
                "display_name": user.display_name,
                "role": user.role,
                "permissions": sorted(grants),
                "must_change_password": user.must_change_password,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "user_conflict", "User could not be created") from None
    return user, temporary_password


def update_user(db: Session, actor: User, user_id: UUID, payload: UpdateUserRequest) -> User:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    target = _get_target_for_update(db, user_id)
    changed = {}
    for field in ("login", "display_name"):
        value = getattr(payload, field)
        if value is not None and value != getattr(target, field):
            changed[field] = {"before": getattr(target, field), "after": value}
            setattr(target, field, value)
    if not changed:
        raise ApiError(409, "no_change", "No fields changed")
    target.updated_at = utc_now()
    audit(db, "user_updated", "success", actor=actor.id, target=target.id, details=changed)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "user_conflict", "User could not be updated") from None
    return target


def set_user_permissions(db: Session, actor: User, user_id: UUID, grants: set[Permission]) -> User:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    target = _get_target_for_update(db, user_id)
    previous = effective_permissions(db, target)
    if target.role == Role.ADMIN and target.is_active and Permission.IDENTITY_MANAGE not in grants:
        _ensure_admin_remains(db, target)
    if previous == grants:
        return target
    db.query(UserPermission).filter(UserPermission.user_id == target.id).delete()
    db.add_all(UserPermission(user_id=target.id, permission=value) for value in grants)
    target.permissions_customized = True
    target.updated_at = utc_now()
    audit(
        db,
        "permissions_changed",
        "success",
        actor=actor.id,
        target=target.id,
        details={"before": sorted(previous), "after": sorted(grants)},
    )
    db.commit()
    return target


def reset_password(db: Session, actor: User, user_id: UUID) -> str:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    target = _get_target_for_update(db, user_id)
    temporary_password = secrets.token_urlsafe(24)
    target.password_hash = hash_password(temporary_password)
    target.must_change_password = True
    target.updated_at = utc_now()
    _revoke_user_sessions(db, target, actor)
    audit(db, "password_reset", "success", actor=actor.id, target=target.id)
    db.commit()
    return temporary_password


def set_user_password(
    db: Session,
    actor: User,
    user_id: UUID,
    new_password: str,
    must_change_password: bool,
) -> None:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    target = _get_target_for_update(db, user_id)
    target.password_hash = hash_password(new_password)
    target.must_change_password = must_change_password
    target.updated_at = utc_now()
    _revoke_user_sessions(db, target, actor)
    audit(
        db,
        "password_set",
        "success",
        actor=actor.id,
        target=target.id,
        details={"must_change_password": must_change_password},
    )
    db.commit()


def change_own_password(db: Session, identity: AuthContext, current: str, new: str) -> None:
    user = _get_target_for_update(db, identity.user.id)
    if not verify_password(user.password_hash, current):
        audit(db, "password_change", "denied", actor=user.id, target=user.id)
        db.commit()
        raise ApiError(403, "invalid_password", "Current password is incorrect")
    if verify_password(user.password_hash, new):
        raise ApiError(422, "same_password", "New password must differ")
    user.password_hash = hash_password(new)
    user.must_change_password = False
    user.updated_at = utc_now()
    _revoke_user_sessions(db, user, user)
    audit(db, "password_change", "success", actor=user.id, target=user.id)
    db.commit()


def reactivate_user(db: Session, actor: User, user_id: UUID) -> User:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    target = _get_target_for_update(db, user_id)
    if target.is_active:
        raise ApiError(409, "already_active", "User is already active")
    target.is_active = True
    target.updated_at = utc_now()
    audit(db, "user_reactivated", "success", actor=actor.id, target=target.id)
    db.commit()
    return target


def deactivate_user(db: Session, actor: User, user_id: UUID) -> User:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    target = _get_target_for_update(db, user_id)
    if not target.is_active:
        raise ApiError(409, "already_inactive", "User is already inactive")
    _ensure_admin_remains(db, target)
    target.is_active = False
    target.updated_at = utc_now()
    _revoke_user_sessions(db, target, actor)
    audit(db, "user_deactivated", "success", actor=actor.id, target=target.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "last_admin", "Last active administrator cannot be changed") from exc
    return target


def change_user_role(db: Session, actor: User, user_id: UUID, new_role: Role) -> User:
    _lock_admin_changes(db)
    _ensure_actor_can_manage(db, actor)
    target = _get_target_for_update(db, user_id)
    if not target.is_active:
        raise ApiError(409, "inactive_user", "Inactive user cannot change role")
    if target.role == new_role:
        raise ApiError(409, "same_role", "User already has this role")
    if target.role == Role.ADMIN:
        _ensure_admin_remains(db, target)
    target.role = new_role
    target.updated_at = utc_now()
    _revoke_user_sessions(db, target, actor)
    audit(db, "role_changed", "success", actor=actor.id, target=target.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "last_admin", "Last active administrator cannot be changed") from exc
    return target
