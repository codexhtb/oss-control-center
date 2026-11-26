"""OCC staff access to the existing Field Work queue after verified Telegram linking."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.api.v1.field_work_miniapp import (
    CompleteWork,
    MiniappReschedule,
    _mutable_handoff,
    complete_handoff_as_staff,
    handoff_detail,
    miniapp_changes,
    queue_handoffs,
    reschedule_history,
)
from app.api.v1.identity import (
    CsrfIdentityDep,
    CurrentIdentityDep,
    SessionDep,
    require_permission,
    require_read_permission,
)
from app.core.errors import ApiError
from app.modules.field_dispatch.miniapp_auth import configured_bot_token, verify_init_data
from app.modules.field_dispatch.models import (
    FieldExternalMessage,
    FieldInstaller,
    FieldInstallerDirection,
    FieldWorkHandoff,
    FieldWorkStaffAccess,
    FieldWorkStaffDirection,
    FieldWorkStaffLinkCode,
)
from app.modules.field_dispatch.reschedule_dialog import active_dialog
from app.modules.field_dispatch.rescheduling import active_prompt, record_reschedule
from app.modules.field_dispatch.service import verify_handoff
from app.modules.identity.access import has_permission
from app.modules.identity.crypto import hash_token
from app.modules.identity.models import IdentityAuditEvent, User
from app.modules.identity.roles import Permission, Role
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.models import Ticket

router = APIRouter(prefix="/field-work", tags=["field-work-staff"])
Direction = Literal["SUKHUM", "OCHAMCHIRA"]
ManageDep = Annotated[AuthContext, Depends(require_permission(Permission.IDENTITY_MANAGE))]
ReadManageDep = Annotated[AuthContext, Depends(require_read_permission(Permission.IDENTITY_MANAGE))]


class StaffAccessWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_enabled: bool
    directions: set[Direction] = Field(max_length=2)
    expected_updated_at: datetime | None = None


class LinkCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=16, max_length=64)


def _directions(db: SessionDep, user_id: UUID) -> list[str]:
    return sorted(
        db.scalars(
            select(FieldWorkStaffDirection.direction).where(
                FieldWorkStaffDirection.user_id == user_id
            )
        ).all()
    )


def _view(db: SessionDep, access: FieldWorkStaffAccess | None, user_id: UUID) -> dict:
    events = db.scalars(
        select(IdentityAuditEvent)
        .where(
            IdentityAuditEvent.target_user_id == user_id,
            IdentityAuditEvent.action.in_(
                ("field_staff_linked", "field_staff_unlinked", "field_staff_access_changed")
            ),
        )
        .order_by(IdentityAuditEvent.occurred_at.desc())
        .limit(10)
    ).all()
    return {
        "user_id": user_id,
        "telegram_user_id": access.telegram_user_id if access else None,
        "is_enabled": access.is_enabled if access else False,
        "directions": _directions(db, user_id) if access else [],
        "updated_at": access.updated_at if access else None,
        "linked_at": access.linked_at if access else None,
        "link_code_active": bool(
            db.scalar(
                select(func.count()).select_from(FieldWorkStaffLinkCode).where(
                    FieldWorkStaffLinkCode.user_id == user_id,
                    FieldWorkStaffLinkCode.expires_at > datetime.now(UTC),
                )
            )
        ),
        "events": [
            {"action": event.action, "occurred_at": event.occurred_at}
            for event in events
        ],
    }


@router.get("/staff-access/me")
def get_own_staff_access(identity: CurrentIdentityDep, db: SessionDep):
    return _view(db, db.get(FieldWorkStaffAccess, identity.user.id), identity.user.id)


@router.get("/staff-access/{user_id}")
def get_staff_access(user_id: UUID, _: ReadManageDep, db: SessionDep):
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError(404, "user_not_found", "User not found")
    return _view(db, db.get(FieldWorkStaffAccess, user_id), user_id)


@router.put("/staff-access/{user_id}")
def set_staff_access(user_id: UUID, payload: StaffAccessWrite, identity: ManageDep, db: SessionDep):
    if user_id == identity.user.id:
        raise ApiError(403, "cannot_manage_self", "Cannot change own Field Work access")
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None or user.role not in (Role.ENGINEER, Role.ADMIN):
        raise ApiError(404, "user_not_found", "Eligible user not found")
    if payload.is_enabled and not payload.directions:
        raise ApiError(422, "directions_required", "Choose a direction")
    access = db.scalar(
        select(FieldWorkStaffAccess)
        .where(FieldWorkStaffAccess.user_id == user_id)
        .with_for_update()
    )
    if access is None:
        if payload.expected_updated_at is not None:
            raise ApiError(409, "stale_access", "Field Work access changed")
        access = FieldWorkStaffAccess(user_id=user_id)
        db.add(access)
        db.flush()
    elif access.updated_at != payload.expected_updated_at:
        raise ApiError(409, "stale_access", "Field Work access changed")
    if payload.is_enabled and access.telegram_user_id is not None:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:telegram_id)"),
            {"telegram_id": access.telegram_user_id},
        )
    access.is_enabled = payload.is_enabled
    access.updated_at = datetime.now(UTC)
    for item in db.scalars(
        select(FieldWorkStaffDirection).where(FieldWorkStaffDirection.user_id == user_id)
    ).all():
        db.delete(item)
    db.flush()
    for direction in payload.directions:
        db.add(FieldWorkStaffDirection(user_id=user_id, direction=direction))
    db.query(FieldWorkStaffLinkCode).filter(FieldWorkStaffLinkCode.user_id == user_id).delete()
    audit(
        db,
        "field_staff_access_changed",
        "success",
        actor=identity.user.id,
        target=user_id,
        details={"enabled": payload.is_enabled, "directions": sorted(payload.directions)},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409, "telegram_identity_conflict", "Telegram identity is active as installer"
        ) from exc
    db.refresh(access)
    return _view(db, access, user_id)


@router.post("/staff-access/{user_id}/unlink")
def unlink_staff(user_id: UUID, identity: ManageDep, db: SessionDep):
    if user_id == identity.user.id:
        raise ApiError(403, "cannot_manage_self", "Cannot unlink own Telegram account")
    access = db.scalar(
        select(FieldWorkStaffAccess)
        .where(FieldWorkStaffAccess.user_id == user_id)
        .with_for_update()
    )
    if access is None:
        raise ApiError(404, "not_found", "Field Work access not found")
    access.telegram_user_id = None
    access.linked_at = None
    access.updated_at = datetime.now(UTC)
    db.query(FieldWorkStaffLinkCode).filter(FieldWorkStaffLinkCode.user_id == user_id).delete()
    audit(db, "field_staff_unlinked", "success", actor=identity.user.id, target=user_id)
    db.commit()
    db.refresh(access)
    return _view(db, access, user_id)


@router.post("/staff-access/{user_id}/revoke-codes")
def revoke_staff_codes(user_id: UUID, identity: ManageDep, db: SessionDep):
    if user_id == identity.user.id:
        raise ApiError(403, "cannot_manage_self", "Cannot revoke own link codes")
    deleted = db.query(FieldWorkStaffLinkCode).filter(
        FieldWorkStaffLinkCode.user_id == user_id
    ).delete()
    audit(
        db,
        "field_staff_codes_revoked",
        "success",
        actor=identity.user.id,
        target=user_id,
        details={"count": deleted},
    )
    db.commit()
    return {"revoked": deleted}


@router.post("/staff-link-code")
def issue_link_code(identity: CsrfIdentityDep, db: SessionDep, response: Response):
    user = identity.user
    if user.role not in (Role.ENGINEER, Role.ADMIN):
        raise ApiError(403, "forbidden", "Only OSS staff may link Telegram")
    access = db.get(FieldWorkStaffAccess, user.id)
    if access is None or not access.is_enabled or not _directions(db, user.id):
        raise ApiError(403, "field_work_not_enabled", "Field Work access is not enabled")
    if access.telegram_user_id is not None:
        raise ApiError(409, "already_linked", "Telegram account is already linked")
    code = secrets.token_urlsafe(18)
    challenge = db.get(FieldWorkStaffLinkCode, user.id)
    if challenge is None:
        challenge = FieldWorkStaffLinkCode(
            user_id=user.id,
            code_hash=hash_token(code),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add(challenge)
    else:
        challenge.code_hash = hash_token(code)
        challenge.expires_at = datetime.now(UTC) + timedelta(minutes=10)
        challenge.created_at = datetime.now(UTC)
    audit(db, "field_staff_link_requested", "success", actor=user.id, target=user.id)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"code": code, "expires_at": challenge.expires_at}


@router.post("/miniapp/link-staff")
def claim_staff_link(
    payload: LinkCode,
    db: SessionDep,
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
):
    telegram_id = verify_init_data(init_data or "", configured_bot_token())
    db.execute(text("SELECT pg_advisory_xact_lock(:telegram_id)"), {"telegram_id": telegram_id})
    if db.scalar(
        select(FieldInstaller.id).where(
            FieldInstaller.telegram_user_id == telegram_id,
            FieldInstaller.is_active.is_(True),
        )
    ):
        raise ApiError(409, "telegram_identity_conflict", "Telegram account is an installer")
    challenge = db.scalar(
        select(FieldWorkStaffLinkCode)
        .where(FieldWorkStaffLinkCode.code_hash == hash_token(payload.code))
        .with_for_update()
    )
    if challenge is None or challenge.expires_at <= datetime.now(UTC):
        raise ApiError(403, "invalid_link_code", "Link code is invalid or expired")
    access = db.scalar(
        select(FieldWorkStaffAccess)
        .where(FieldWorkStaffAccess.user_id == challenge.user_id)
        .with_for_update()
    )
    user = db.get(User, challenge.user_id)
    if (
        access is None
        or not access.is_enabled
        or access.telegram_user_id is not None
        or user is None
        or not user.is_active
        or user.deleted_at is not None
        or user.must_change_password
        or user.role not in (Role.ENGINEER, Role.ADMIN)
        or not _directions(db, challenge.user_id)
    ):
        raise ApiError(403, "field_work_not_enabled", "Field Work access is not available")
    access.telegram_user_id = telegram_id
    access.linked_at = datetime.now(UTC)
    access.updated_at = datetime.now(UTC)
    db.delete(challenge)
    audit(db, "field_staff_linked", "success", actor=user.id, target=user.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409, "telegram_identity_conflict", "Telegram account is already linked"
        ) from exc
    return {"name": user.display_name, "directions": _directions(db, user.id), "kind": "staff"}


def _staff_auth(
    db: SessionDep,
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> tuple[User, tuple[str, ...]]:
    telegram_id = verify_init_data(init_data or "", configured_bot_token())
    row = db.execute(
        select(FieldWorkStaffAccess, User)
        .join(User, User.id == FieldWorkStaffAccess.user_id)
        .where(
            FieldWorkStaffAccess.telegram_user_id == telegram_id,
            FieldWorkStaffAccess.is_enabled.is_(True),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.must_change_password.is_(False),
            User.role.in_((Role.ENGINEER, Role.ADMIN)),
        )
    ).one_or_none()
    if row is None:
        raise ApiError(403, "staff_forbidden", "Field Work access denied")
    _, user = row
    allowed = tuple(_directions(db, user.id))
    if not allowed:
        raise ApiError(403, "staff_forbidden", "Field Work access denied")
    return user, allowed


StaffDep = Annotated[tuple[User, tuple[str, ...]], Depends(_staff_auth)]


@router.get("/miniapp/staff/me")
def staff_me(auth: StaffDep, db: SessionDep):
    user, directions = auth
    return {
        "name": user.display_name,
        "directions": directions,
        "kind": "staff",
        "can_verify": has_permission(db, user, Permission.TICKET_FIELD_WORK),
        "can_perform": has_permission(db, user, Permission.FIELD_WORK_PERFORM),
        "role": user.role,
    }


@router.get("/miniapp/staff/handoffs")
def staff_queue(
    auth: StaffDep,
    db: SessionDep,
    queue: Literal["open", "completed", "history"] = "open",
    direction: Direction | None = None,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return queue_handoffs(auth, db, queue, direction, page, page_size)


@router.get("/miniapp/staff/handoffs/{handoff_id}")
def staff_handoff(handoff_id: UUID, auth: StaffDep, db: SessionDep):
    return handoff_detail(handoff_id, auth, db)


@router.get("/miniapp/staff/handoffs/{handoff_id}/changes")
def staff_changes(
    handoff_id: UUID, auth: StaffDep, db: SessionDep,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
):
    return miniapp_changes(handoff_id, auth, db, page)


@router.get("/miniapp/staff/handoffs/{handoff_id}/reschedules")
def staff_reschedules(
    handoff_id: UUID,
    auth: StaffDep,
    db: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return reschedule_history(handoff_id, auth, db, page, page_size)


@router.get("/miniapp/staff/partners")
def staff_partners(auth: StaffDep, db: SessionDep, direction: Direction):
    user, allowed = auth
    if direction not in allowed:
        raise ApiError(403, "direction_forbidden", "Direction access denied")
    installers = db.scalars(
        select(FieldInstaller)
        .join(FieldInstallerDirection)
        .where(
            FieldInstallerDirection.direction == direction,
            FieldInstaller.is_active.is_(True),
        )
        .order_by(FieldInstaller.name, FieldInstaller.id)
    ).all()
    staff = db.execute(
        select(User, FieldWorkStaffAccess)
        .join(FieldWorkStaffAccess, FieldWorkStaffAccess.user_id == User.id)
        .join(FieldWorkStaffDirection, FieldWorkStaffDirection.user_id == User.id)
        .where(
            FieldWorkStaffDirection.direction == direction,
            FieldWorkStaffAccess.is_enabled.is_(True),
            FieldWorkStaffAccess.telegram_user_id.is_not(None),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.id != user.id,
        )
        .order_by(User.display_name, User.id)
    ).all()
    return {"items": [
        *({"id": item.id, "name": item.name, "kind": "installer"} for item in installers),
        *(
            {"id": person.id, "name": person.display_name, "kind": "staff"}
            for person, _ in staff
            if has_permission(db, person, Permission.FIELD_WORK_PERFORM)
        ),
    ]}


@router.post("/miniapp/staff/handoffs/{handoff_id}/report")
def staff_report(
    handoff_id: UUID, payload: CompleteWork, auth: StaffDep, db: SessionDep
):
    user, allowed = auth
    return complete_handoff_as_staff(handoff_id, payload, user, allowed, db)


@router.post("/miniapp/staff/handoffs/{handoff_id}/reschedules")
def staff_reschedule(
    handoff_id: UUID, payload: MiniappReschedule, auth: StaffDep, db: SessionDep
):
    user, allowed = auth
    if not has_permission(db, user, Permission.FIELD_WORK_PERFORM):
        raise ApiError(403, "field_work_perform_forbidden", "Permission denied")
    handoff = _mutable_handoff(db, handoff_id, allowed)
    if payload.scheduled_for is not None and payload.scheduled_date is not None:
        raise ApiError(422, "field_schedule_date_invalid", "Choose a date or date and time")
    if active_prompt(db, handoff.id) is not None or active_dialog(db, handoff.id, lock=True):
        raise ApiError(409, "field_dialog_active", "A Telegram dialog is still active")
    access = db.get(FieldWorkStaffAccess, user.id)
    record = record_reschedule(
        db,
        handoff,
        reason=payload.reason,
        scheduled_for=payload.scheduled_for,
        scheduled_date=payload.scheduled_date,
        source="MINIAPP",
        author_name=user.display_name,
        telegram_user_id=access.telegram_user_id if access else None,
        expected_version=payload.expected_version,
    )
    db.commit()
    return {
        "id": record.id,
        "schedule_version": handoff.schedule_version,
        "schedule_status": handoff.schedule_status,
    }


@router.post("/miniapp/staff/handoffs/{handoff_id}/verify")
def staff_verify(handoff_id: UUID, auth: StaffDep, db: SessionDep):
    user, allowed = auth
    if not has_permission(db, user, Permission.TICKET_FIELD_WORK) or not has_permission(
        db, user, Permission.TICKET_READ
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    row = db.execute(
        select(FieldWorkHandoff, Ticket)
        .join(Ticket, Ticket.id == FieldWorkHandoff.ticket_id)
        .where(
            FieldWorkHandoff.id == handoff_id,
            FieldWorkHandoff.direction.in_(allowed),
            FieldWorkHandoff.delivery_status.in_(("SENT", "PUBLISHED")),
            Ticket.deleted_at.is_(None),
            (Ticket.ticket_type != "INTERNAL")
            | FieldWorkHandoff.internal_dispatch_approved.is_(True),
        )
    ).one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "Field work not found")
    handoff, ticket = row
    report = db.scalar(
        select(FieldExternalMessage).where(
            FieldExternalMessage.handoff_id == handoff.id,
            FieldExternalMessage.kind.in_(("COMPLETION_REPORT", "MINIAPP_REPORT")),
        )
    )
    if report is not None and report.primary_staff_user_id == user.id:
        raise ApiError(409, "self_verification_forbidden", "Another engineer must verify this work")
    result = verify_handoff(db, user.id, ticket.id, handoff.id)
    return {"handoff_id": result.id, "work_status": result.work_status}
