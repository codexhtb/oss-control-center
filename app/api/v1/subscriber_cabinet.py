"""On-demand cabinet credentials, outside the persisted Subscriber360 contract."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.config import get_settings
from app.core.errors import ApiError
from app.modules.billing.netup.cabinet import load_cabinet, password_enabled
from app.modules.billing.netup.config import NetupConfig
from app.modules.billing.netup.repository import parse_account_no
from app.modules.billing.provider import BillingInvalidAccount
from app.modules.identity.access import has_permission
from app.modules.identity.models import AuthSession, User
from app.modules.identity.roles import Permission
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.models import SubscriberSnapshot

router = APIRouter(prefix="/subscriber-lookups", tags=["billing"])
NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}


def _audit(db, actor_id, lookup_id, outcome):
    audit(db, "subscriber_password_view", outcome, actor=actor_id,
          details={"lookup_id": str(lookup_id)})
    db.commit()  # If audit cannot be persisted, never deliver a secret.


def _allowed(db, identity, *, password=False):
    return has_permission(db, identity.user, Permission.SUBSCRIBER_READ) and (
        not password or has_permission(db, identity.user, Permission.SUBSCRIBER_PASSWORD_READ)
    )


def _source(db, identity, lookup_id, *, password=False):
    if not _allowed(db, identity, password=password):
        if password:
            _audit(db, identity.user.id, lookup_id, "denied")
        raise ApiError(403, "forbidden", "Permission denied")
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != identity.user.id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    if password and snapshot.expires_at <= datetime.now(UTC):
        raise ApiError(409, "lookup_expired", "Refresh subscriber lookup")
    if snapshot.source != "NETUP" or get_settings().billing_provider != "NETUP":
        raise ApiError(503, "cabinet_unavailable", "Cabinet data is unavailable")
    try:
        result = (parse_account_no(snapshot.account_no), parse_account_no(snapshot.subscriber_id))
    except (BillingInvalidAccount, TypeError):
        raise ApiError(503, "cabinet_unavailable", "Cabinet data is unavailable") from None
    return result


@router.get("/{lookup_id}/cabinet")
def cabinet_login(lookup_id: UUID, identity: CurrentIdentityDep, db: SessionDep):
    account_id, user_id = _source(db, identity, lookup_id)
    permitted = _allowed(db, identity, password=True)
    db.rollback()
    try:
        config = NetupConfig.from_env(app_env=get_settings().app_env)
        result = load_cabinet(config, account_id, user_id)
        available = permitted and password_enabled(get_settings(), config)
    except Exception:
        raise ApiError(503, "cabinet_unavailable", "Cabinet data is unavailable") from None
    return JSONResponse({"login": result.login, "password_available": available}, headers=NO_STORE)


@router.post("/{lookup_id}/cabinet/password")
def cabinet_password(lookup_id: UUID, identity: CsrfIdentityDep, db: SessionDep):
    actor_id, session_id = identity.user.id, identity.session.id
    try:
        account_id, user_id = _source(db, identity, lookup_id, password=True)
    except ApiError as exc:
        if exc.status_code != 403:
            _audit(db, actor_id, lookup_id, "denied")
        raise
    db.rollback()  # Release PostgreSQL before the external SELECT.
    settings = get_settings()
    try:
        config = NetupConfig.from_env(app_env=settings.app_env)
        if not password_enabled(settings, config):
            raise ValueError("disabled")
        result = load_cabinet(
            config, account_id, user_id, password=True,
            require_tls=settings.subscriber_cabinet_mode not in {"dev_preview", "management"},
        )
        if result.password is None:
            raise ValueError("unavailable")
    except Exception:
        _audit(db, actor_id, lookup_id, "unavailable")
        raise ApiError(
            503, "cabinet_password_unavailable", "Cabinet password is unavailable"
        ) from None
    # Recheck saved permissions and session after I/O. Serialize with grant changes.
    actor = db.scalar(select(User).where(User.id == actor_id).with_for_update()
                      .execution_options(populate_existing=True))
    session = db.scalar(select(AuthSession).where(AuthSession.id == session_id).with_for_update()
                        .execution_options(populate_existing=True))
    if (
        actor is None or not actor.is_active or actor.deleted_at is not None
        or actor.must_change_password or session is None or session.revoked_at is not None
        or session.expires_at <= datetime.now(UTC)
        or not _allowed(db, AuthContext(actor, session), password=True)
    ):
        _audit(db, actor_id, lookup_id, "denied")
        raise ApiError(403, "forbidden", "Permission denied")
    _audit(db, actor_id, lookup_id, "success")
    return JSONResponse({"password": result.password.get_secret_value()}, headers=NO_STORE)
