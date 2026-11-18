"""Read-only installer Mini App queue and OSS-managed installer allowlist."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.api.v1.identity import SessionDep, require_permission, require_read_permission
from app.core.errors import ApiError
from app.modules.field_dispatch.miniapp_auth import (
    configured_bot_token,
    verify_init_data,
    verify_init_data_user,
)
from app.modules.field_dispatch.models import (
    FieldDispatchUpdate,
    FieldExternalMessage,
    FieldInstaller,
    FieldInstallerAccessRequest,
    FieldInstallerDirection,
    FieldWorkCompletionPrompt,
    FieldWorkHandoff,
    FieldWorkReschedule,
    FieldWorkStaffAccess,
    FieldWorkStaffDirection,
)
from app.modules.field_dispatch.personal_notifications import (
    preference_view,
    update_preferences,
)
from app.modules.field_dispatch.reporting import record_completed_work
from app.modules.field_dispatch.reschedule_dialog import active_dialog
from app.modules.field_dispatch.rescheduling import active_prompt, record_reschedule
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.models import SubscriberSnapshot, Ticket

router = APIRouter(prefix="/field-work", tags=["field-work-miniapp"])
Direction = Literal["SUKHUM", "OCHAMCHIRA"]
Queue = Literal["open", "completed", "history"]
QUEUE_STATUS = {"open": "AWAITING_REPORT", "completed": "REPORTED", "history": "VERIFIED"}
ManagerDep = Annotated[AuthContext, Depends(require_permission(Permission.IDENTITY_MANAGE))]
ReadManagerDep = Annotated[
    AuthContext, Depends(require_read_permission(Permission.IDENTITY_MANAGE))
]


class InstallerWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram_user_id: int = Field(gt=0, lt=2**52)
    name: str = Field(min_length=1, max_length=160)
    is_active: bool = True
    directions: set[Direction] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Name must be trimmed and contain no controls")
        return value


class InstallerUpdate(InstallerWrite):
    expected_updated_at: datetime


class CompleteWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    partner_installer_id: UUID | None = None
    partner_id: UUID | None = None
    partner_kind: Literal["installer", "staff"] | None = None
    work_text: str = Field(min_length=1, max_length=4000)

    @field_validator("work_text")
    @classmethod
    def valid_work_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Work description is required")
        return value.strip()


class MiniappReschedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    scheduled_for: datetime | None = None
    scheduled_date: date | None = None
    expected_version: int = Field(ge=0)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Reason is required")
        return value.strip()

    @field_validator("scheduled_for")
    @classmethod
    def local_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(hours=3)):
            raise ValueError("Use Abkhazia local time (UTC+3)")
        return value


class InstallerConversion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_user_id: UUID
    expected_updated_at: datetime


class AccessRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    directions: set[Direction] = Field(default_factory=set, max_length=2)


class NotificationPreferencesWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notify_new_work: bool
    notify_reschedules: bool
    notify_verification: bool


def _directions(db: SessionDep, installer_id: UUID) -> list[str]:
    return sorted(
        db.scalars(
            select(FieldInstallerDirection.direction).where(
                FieldInstallerDirection.installer_id == installer_id
            )
        ).all()
    )


def _installer_view(db: SessionDep, installer: FieldInstaller) -> dict:
    return {
        "id": installer.id,
        "telegram_user_id": installer.telegram_user_id,
        "name": installer.name,
        "is_active": installer.is_active,
        "directions": _directions(db, installer.id),
        "created_at": installer.created_at,
        "updated_at": installer.updated_at,
    }


@router.get("/installers")
def list_installers(_: ReadManagerDep, db: SessionDep):
    installers = db.scalars(
        select(FieldInstaller).order_by(FieldInstaller.name, FieldInstaller.id)
    ).all()
    return {"items": [_installer_view(db, item) for item in installers]}


@router.post("/installers", status_code=201)
def create_installer(payload: InstallerWrite, identity: ManagerDep, db: SessionDep):
    db.execute(
        text("SELECT pg_advisory_xact_lock(:telegram_id)"),
        {"telegram_id": payload.telegram_user_id},
    )
    if payload.is_active and db.scalar(
        select(FieldWorkStaffAccess.user_id).where(
            FieldWorkStaffAccess.telegram_user_id == payload.telegram_user_id,
            FieldWorkStaffAccess.is_enabled.is_(True),
        )
    ):
        raise ApiError(409, "installer_conflict", "Telegram user already linked to OCC staff")
    installer = FieldInstaller(
        telegram_user_id=payload.telegram_user_id, name=payload.name, is_active=payload.is_active
    )
    try:
        db.add(installer)
        db.flush()
        for direction in payload.directions:
            db.add(FieldInstallerDirection(installer_id=installer.id, direction=direction))
        audit(
            db,
            "field_installer_created",
            "success",
            actor=identity.user.id,
            details={"installer_id": str(installer.id)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "installer_conflict", "Telegram user already registered") from exc
    db.refresh(installer)
    return _installer_view(db, installer)


@router.put("/installers/{installer_id}")
def update_installer(
    installer_id: UUID, payload: InstallerUpdate, identity: ManagerDep, db: SessionDep
):
    installer = db.scalar(
        select(FieldInstaller).where(FieldInstaller.id == installer_id).with_for_update()
    )
    if installer is None:
        raise ApiError(404, "not_found", "Installer not found")
    if installer.updated_at != payload.expected_updated_at:
        raise ApiError(409, "stale_installer", "Installer changed; reload and retry")
    db.execute(
        text("SELECT pg_advisory_xact_lock(:telegram_id)"),
        {"telegram_id": payload.telegram_user_id},
    )
    if payload.is_active and db.scalar(
        select(FieldWorkStaffAccess.user_id).where(
            FieldWorkStaffAccess.telegram_user_id == payload.telegram_user_id,
            FieldWorkStaffAccess.is_enabled.is_(True),
        )
    ):
        raise ApiError(409, "installer_conflict", "Telegram user already linked to OCC staff")
    installer.telegram_user_id = payload.telegram_user_id
    installer.name = payload.name
    installer.is_active = payload.is_active
    installer.updated_at = datetime.now(UTC)
    for existing in db.scalars(
        select(FieldInstallerDirection).where(FieldInstallerDirection.installer_id == installer_id)
    ).all():
        db.delete(existing)
    db.flush()
    for direction in payload.directions:
        db.add(FieldInstallerDirection(installer_id=installer_id, direction=direction))
    audit(
        db,
        "field_installer_updated",
        "success",
        actor=identity.user.id,
        details={"installer_id": str(installer.id)},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "installer_conflict", "Telegram user already registered") from exc
    db.refresh(installer)
    return _installer_view(db, installer)


@router.post("/installers/{installer_id}/convert-to-staff")
def convert_installer_to_staff(
    installer_id: UUID,
    payload: InstallerConversion,
    identity: ManagerDep,
    db: SessionDep,
):
    installer = db.scalar(
        select(FieldInstaller).where(FieldInstaller.id == installer_id).with_for_update()
    )
    user = db.scalar(select(User).where(User.id == payload.target_user_id).with_for_update())
    if installer is None:
        raise ApiError(404, "not_found", "Installer not found")
    if installer.updated_at != payload.expected_updated_at:
        raise ApiError(409, "stale_installer", "Installer changed; reload and retry")
    if (
        user is None
        or user.deleted_at is not None
        or user.role not in (Role.ENGINEER, Role.ADMIN)
    ):
        raise ApiError(404, "user_not_found", "Eligible OCC employee not found")
    db.execute(
        text("SELECT pg_advisory_xact_lock(:telegram_id)"),
        {"telegram_id": installer.telegram_user_id},
    )
    access = db.scalar(
        select(FieldWorkStaffAccess)
        .where(FieldWorkStaffAccess.user_id == user.id)
        .with_for_update()
    )
    if access is not None and access.telegram_user_id not in (None, installer.telegram_user_id):
        raise ApiError(409, "telegram_identity_conflict", "Employee has another Telegram link")
    installer.is_active = False
    installer.updated_at = datetime.now(UTC)
    if access is None:
        access = FieldWorkStaffAccess(user_id=user.id, is_enabled=True)
        db.add(access)
        db.flush()
    else:
        access.is_enabled = True
        access.updated_at = datetime.now(UTC)
    current = set(
        db.scalars(
            select(FieldWorkStaffDirection.direction).where(
                FieldWorkStaffDirection.user_id == user.id
            )
        ).all()
    )
    for direction in set(_directions(db, installer.id)) - current:
        db.add(FieldWorkStaffDirection(user_id=user.id, direction=direction))
    audit(
        db,
        "field_installer_converted",
        "success",
        actor=identity.user.id,
        target=user.id,
        details={"installer_id": str(installer.id)},
    )
    db.commit()
    db.refresh(installer)
    return _installer_view(db, installer)


@router.delete("/installers/{installer_id}", status_code=204)
def delete_installer(
    installer_id: UUID, identity: ManagerDep, db: SessionDep
) -> Response:
    installer = db.scalar(
        select(FieldInstaller).where(FieldInstaller.id == installer_id).with_for_update()
    )
    if installer is None:
        raise ApiError(404, "not_found", "Installer not found")
    linked_records = db.scalar(
        select(func.count())
        .select_from(FieldExternalMessage)
        .where(
            or_(
                FieldExternalMessage.primary_installer_id == installer_id,
                FieldExternalMessage.partner_installer_id == installer_id,
            )
        )
    )
    if linked_records:
        audit(
            db,
            "field_installer_delete_blocked",
            "denied",
            actor=identity.user.id,
            details={"installer_id": str(installer.id), "linked_records": linked_records},
        )
        db.commit()
        raise ApiError(
            409,
            "installer_has_history",
            "Installer has work history and must be deactivated instead",
        )
    audit(
        db,
        "field_installer_deleted",
        "success",
        actor=identity.user.id,
        details={"installer_id": str(installer.id)},
    )
    db.delete(installer)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        audit(
            db,
            "field_installer_delete_blocked",
            "denied",
            actor=identity.user.id,
            details={"installer_id": str(installer_id), "reason": "foreign_key"},
        )
        db.commit()
        raise ApiError(
            409,
            "installer_has_history",
            "Installer has related records and must be deactivated instead",
        ) from exc
    return Response(status_code=204)


def _request_view(item: FieldInstallerAccessRequest) -> dict:
    return {
        "id": item.id,
        "telegram_user_id": item.telegram_user_id,
        "telegram_name": item.telegram_name,
        "status": item.status,
        "directions": item.directions or [],
        "reviewed_by": item.reviewed_by,
        "reviewed_at": item.reviewed_at,
        "created_at": item.created_at,
    }


@router.get("/access-requests")
def list_access_requests(
    _: ReadManagerDep,
    db: SessionDep,
    status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None,
):
    statement = select(FieldInstallerAccessRequest)
    if status:
        statement = statement.where(FieldInstallerAccessRequest.status == status)
    rows = db.scalars(
        statement.order_by(
            FieldInstallerAccessRequest.created_at.desc(), FieldInstallerAccessRequest.id.desc()
        ).limit(200)
    ).all()
    return {"items": [_request_view(item) for item in rows]}


@router.post("/access-requests/{request_id}/decision")
def decide_access_request(
    request_id: UUID,
    payload: AccessRequestDecision,
    identity: ManagerDep,
    db: SessionDep,
):
    if payload.approve and not payload.directions:
        raise ApiError(422, "directions_required", "Choose a direction")
    request = db.scalar(
        select(FieldInstallerAccessRequest)
        .where(FieldInstallerAccessRequest.id == request_id)
        .with_for_update()
    )
    if request is None:
        raise ApiError(404, "not_found", "Access request not found")
    if request.status != "PENDING":
        raise ApiError(409, "access_request_resolved", "Access request is already resolved")
    db.execute(
        text("SELECT pg_advisory_xact_lock(:telegram_id)"),
        {"telegram_id": request.telegram_user_id},
    )
    now = datetime.now(UTC)
    if payload.approve:
        if db.scalar(
            select(FieldWorkStaffAccess.user_id).where(
                FieldWorkStaffAccess.telegram_user_id == request.telegram_user_id,
                FieldWorkStaffAccess.is_enabled.is_(True),
            )
        ):
            raise ApiError(409, "telegram_identity_conflict", "Telegram belongs to OCC staff")
        installer = db.scalar(
            select(FieldInstaller)
            .where(FieldInstaller.telegram_user_id == request.telegram_user_id)
            .with_for_update()
        )
        if installer is None:
            installer = FieldInstaller(
                telegram_user_id=request.telegram_user_id,
                name=request.telegram_name,
                is_active=True,
            )
            db.add(installer)
            db.flush()
        else:
            installer.name = request.telegram_name
            installer.is_active = True
            installer.updated_at = now
            for row in db.scalars(
                select(FieldInstallerDirection).where(
                    FieldInstallerDirection.installer_id == installer.id
                )
            ).all():
                db.delete(row)
            db.flush()
        for direction in payload.directions:
            db.add(FieldInstallerDirection(installer_id=installer.id, direction=direction))
        request.status = "APPROVED"
        request.directions = sorted(payload.directions)
    else:
        request.status = "REJECTED"
        request.directions = []
    request.reviewed_by = identity.user.id
    request.reviewed_at = now
    audit(
        db,
        "field_access_request_decided",
        "success",
        actor=identity.user.id,
        details={"request_id": str(request.id), "decision": request.status},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409, "telegram_identity_conflict", "Telegram identity is already active"
        ) from exc
    db.refresh(request)
    return _request_view(request)


@router.post("/miniapp/access-request", status_code=201)
def request_installer_access(
    db: SessionDep,
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
):
    profile = verify_init_data_user(init_data or "", configured_bot_token())
    telegram_id = int(profile["id"])
    db.execute(text("SELECT pg_advisory_xact_lock(:telegram_id)"), {"telegram_id": telegram_id})
    if db.scalar(
        select(FieldInstaller.id).where(
            FieldInstaller.telegram_user_id == telegram_id, FieldInstaller.is_active.is_(True)
        )
    ) or db.scalar(
        select(FieldWorkStaffAccess.user_id).where(
            FieldWorkStaffAccess.telegram_user_id == telegram_id,
            FieldWorkStaffAccess.is_enabled.is_(True),
        )
    ):
        raise ApiError(409, "telegram_identity_active", "Telegram already has Field Work access")
    pending = db.scalar(
        select(FieldInstallerAccessRequest).where(
            FieldInstallerAccessRequest.telegram_user_id == telegram_id,
            FieldInstallerAccessRequest.status == "PENDING",
        )
    )
    if pending is not None:
        return _request_view(pending)
    latest = db.scalar(
        select(FieldInstallerAccessRequest)
        .where(FieldInstallerAccessRequest.telegram_user_id == telegram_id)
        .order_by(FieldInstallerAccessRequest.created_at.desc())
        .limit(1)
    )
    if latest is not None and latest.created_at > datetime.now(UTC) - timedelta(hours=1):
        raise ApiError(429, "access_request_rate_limited", "Try again later")
    parts = [profile.get("first_name"), profile.get("last_name")]
    name = " ".join(value.strip() for value in parts if isinstance(value, str) and value.strip())
    if not name:
        username = profile.get("username")
        name = f"@{username}" if isinstance(username, str) and username else "Пользователь Telegram"
    request = FieldInstallerAccessRequest(
        telegram_user_id=telegram_id,
        telegram_name=name[:160],
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return _request_view(request)


def _authorized_installer(
    db: SessionDep,
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> tuple[FieldInstaller, tuple[str, ...]]:
    if not init_data:
        raise ApiError(401, "invalid_telegram_auth", "Invalid Telegram authentication")
    telegram_id = verify_init_data(init_data, configured_bot_token())
    installer = db.scalar(
        select(FieldInstaller).where(
            FieldInstaller.telegram_user_id == telegram_id, FieldInstaller.is_active.is_(True)
        )
    )
    if installer is None:
        raise ApiError(403, "installer_forbidden", "Installer access denied")
    directions = tuple(_directions(db, installer.id))
    if not directions:
        raise ApiError(403, "installer_forbidden", "Installer access denied")
    return installer, directions


InstallerDep = Annotated[tuple[FieldInstaller, tuple[str, ...]], Depends(_authorized_installer)]


@router.get("/miniapp/me")
def installer_me(auth: InstallerDep):
    installer, directions = auth
    return {"name": installer.name, "directions": directions}


def _notification_identity(
    db: SessionDep,
    init_data: str | None,
) -> tuple[int, bool]:
    telegram_id = verify_init_data(init_data or "", configured_bot_token())
    installer = db.scalar(
        select(FieldInstaller.id).where(
            FieldInstaller.telegram_user_id == telegram_id,
            FieldInstaller.is_active.is_(True),
        )
    )
    if installer is not None:
        return telegram_id, False
    user = db.scalar(
        select(User)
        .join(FieldWorkStaffAccess, FieldWorkStaffAccess.user_id == User.id)
        .where(
            FieldWorkStaffAccess.telegram_user_id == telegram_id,
            FieldWorkStaffAccess.is_enabled.is_(True),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.must_change_password.is_(False),
            User.role.in_((Role.ENGINEER, Role.ADMIN)),
        )
    )
    if user is None:
        raise ApiError(403, "field_work_forbidden", "Field Work access denied")
    can_verify = has_permission(db, user, Permission.TICKET_FIELD_WORK) and has_permission(
        db, user, Permission.TICKET_READ
    )
    return telegram_id, can_verify


@router.get("/miniapp/notification-settings")
def notification_settings(
    db: SessionDep,
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
):
    telegram_id, can_verify = _notification_identity(db, init_data)
    return preference_view(db, telegram_id, can_verify=can_verify)


@router.post("/miniapp/notification-settings")
def save_notification_settings(
    payload: NotificationPreferencesWrite,
    db: SessionDep,
    init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
):
    telegram_id, can_verify = _notification_identity(db, init_data)
    return update_preferences(
        db,
        telegram_id,
        notify_new_work=payload.notify_new_work,
        notify_reschedules=payload.notify_reschedules,
        notify_verification=payload.notify_verification,
        can_verify=can_verify,
    )


def _item(handoff: FieldWorkHandoff, ticket: Ticket) -> dict:
    return {
        "id": handoff.id,
        "ticket_id": ticket.id,
        "ticket_number": ticket.number,
        "direction": handoff.direction,
        "work_status": handoff.work_status,
        "subject": ticket.subject or ticket.category_name or "Обращение",
        "sent_at": handoff.sent_at,
        "published_at": handoff.published_at,
        "reported_at": handoff.reported_at,
        "verified_at": handoff.verified_at,
        "scheduled_for": handoff.scheduled_for,
        "scheduled_date": handoff.scheduled_date,
        "schedule_status": handoff.schedule_status,
    }


@router.get("/miniapp/handoffs")
def queue_handoffs(
    auth: InstallerDep,
    db: SessionDep,
    queue: Queue = "open",
    direction: Direction | None = None,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    _, allowed = auth
    if direction is not None and direction not in allowed:
        raise ApiError(403, "direction_forbidden", "Direction access denied")
    conditions = (
        FieldWorkHandoff.delivery_status.in_(("SENT", "PUBLISHED")),
        FieldWorkHandoff.work_status == QUEUE_STATUS[queue],
        FieldWorkHandoff.direction.in_((direction,) if direction else allowed),
        Ticket.deleted_at.is_(None),
        Ticket.ticket_type != "INTERNAL",
    )
    source = select(FieldWorkHandoff, Ticket).join(Ticket, Ticket.id == FieldWorkHandoff.ticket_id)
    total = (
        db.scalar(
            select(func.count())
            .select_from(FieldWorkHandoff)
            .join(Ticket, Ticket.id == FieldWorkHandoff.ticket_id)
            .where(*conditions)
        )
        or 0
    )
    ordering = (
        (
            func.coalesce(FieldWorkHandoff.sent_at, FieldWorkHandoff.published_at).asc(),
            FieldWorkHandoff.id.asc(),
        )
        if queue == "open"
        else (FieldWorkHandoff.reported_at.desc(), FieldWorkHandoff.id.desc())
        if queue == "completed"
        else (FieldWorkHandoff.verified_at.desc(), FieldWorkHandoff.id.desc())
    )
    rows = db.execute(
        source.where(*conditions)
        .order_by(*ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_item(handoff, ticket) for handoff, ticket in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/miniapp/handoffs/{handoff_id}")
def handoff_detail(handoff_id: UUID, auth: InstallerDep, db: SessionDep):
    _, allowed = auth
    row = db.execute(
        select(FieldWorkHandoff, Ticket)
        .join(Ticket, Ticket.id == FieldWorkHandoff.ticket_id)
        .where(
            FieldWorkHandoff.id == handoff_id,
            FieldWorkHandoff.delivery_status.in_(("SENT", "PUBLISHED")),
            FieldWorkHandoff.work_status.in_(tuple(QUEUE_STATUS.values())),
            FieldWorkHandoff.direction.in_(allowed),
            Ticket.deleted_at.is_(None),
            Ticket.ticket_type != "INTERNAL",
        )
    ).one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "Field work not found")
    handoff, ticket = row
    snapshot = (
        db.get(SubscriberSnapshot, ticket.subscriber_snapshot_id)
        if ticket.subscriber_snapshot_id
        else None
    )
    latest_schedule = db.scalar(
        select(FieldWorkReschedule)
        .where(FieldWorkReschedule.handoff_id == handoff.id)
        .order_by(FieldWorkReschedule.schedule_version.desc())
        .limit(1)
    )
    report = db.scalar(
        select(FieldExternalMessage)
        .where(
            FieldExternalMessage.handoff_id == handoff.id,
            FieldExternalMessage.kind.in_(("COMPLETION_REPORT", "MINIAPP_REPORT")),
        )
        .order_by(FieldExternalMessage.telegram_created_at.desc())
        .limit(1)
    )
    return {
        **_item(handoff, ticket),
        "reason": handoff.reason,
        "name": ticket.caller_full_name or (snapshot.full_name if snapshot else None),
        "phone": ticket.caller_phone or (snapshot.phone if snapshot else None),
        "account_no": ticket.account_no,
        "city": ticket.city,
        "address": handoff.visit_address
        or ticket.address_override
        or (snapshot.actual_address if snapshot else None),
        "schedule_version": handoff.schedule_version,
        "schedule_reason": latest_schedule.reason if latest_schedule else None,
        "schedule_author": latest_schedule.author_name if latest_schedule else None,
        "report": (
            {
                "text": report.body,
                "installer": report.sender_name,
                "partner": report.partner_name,
                "reported_at": report.telegram_created_at,
            }
            if report
            else None
        ),
    }


def _mutable_handoff(
    db: SessionDep, handoff_id: UUID, allowed: tuple[str, ...], *, lock: bool = True
) -> FieldWorkHandoff:
    statement = (
        select(FieldWorkHandoff)
        .join(Ticket, Ticket.id == FieldWorkHandoff.ticket_id)
        .where(
            FieldWorkHandoff.id == handoff_id,
            FieldWorkHandoff.direction.in_(allowed),
            Ticket.deleted_at.is_(None),
            Ticket.ticket_type != "INTERNAL",
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(of=FieldWorkHandoff)
    handoff = db.scalar(statement)
    if handoff is None:
        raise ApiError(404, "not_found", "Field work not found")
    return handoff


@router.get("/miniapp/partners")
def available_partners(auth: InstallerDep, db: SessionDep, direction: Direction):
    installer, allowed = auth
    if direction not in allowed:
        raise ApiError(403, "direction_forbidden", "Direction access denied")
    rows = db.scalars(
        select(FieldInstaller)
        .join(FieldInstallerDirection)
        .where(
            FieldInstallerDirection.direction == direction,
            FieldInstaller.is_active.is_(True),
            FieldInstaller.id != installer.id,
        )
        .order_by(FieldInstaller.name, FieldInstaller.id)
    ).all()
    items = [{"id": item.id, "name": item.name, "kind": "installer"} for item in rows]
    staff_rows = db.execute(
        select(User, FieldWorkStaffAccess)
        .join(FieldWorkStaffAccess, FieldWorkStaffAccess.user_id == User.id)
        .join(FieldWorkStaffDirection, FieldWorkStaffDirection.user_id == User.id)
        .where(
            FieldWorkStaffDirection.direction == direction,
            FieldWorkStaffAccess.is_enabled.is_(True),
            FieldWorkStaffAccess.telegram_user_id.is_not(None),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .order_by(User.display_name, User.id)
    ).all()
    items.extend(
        {"id": user.id, "name": user.display_name, "kind": "staff"}
        for user, _ in staff_rows
        if has_permission(db, user, Permission.FIELD_WORK_PERFORM)
    )
    return {"items": items}


@router.post("/miniapp/handoffs/{handoff_id}/report")
def complete_handoff(handoff_id: UUID, payload: CompleteWork, auth: InstallerDep, db: SessionDep):
    installer, allowed = auth
    handoff = _mutable_handoff(db, handoff_id, allowed)
    existing = db.scalar(
        select(FieldExternalMessage).where(
            FieldExternalMessage.primary_installer_id == installer.id,
            FieldExternalMessage.client_request_id == payload.client_request_id,
        )
    )
    if existing is not None:
        requested_partner_installer = (
            payload.partner_id
            if payload.partner_kind == "installer"
            else payload.partner_installer_id
        )
        requested_partner_staff = payload.partner_id if payload.partner_kind == "staff" else None
        if (
            existing.handoff_id != handoff_id
            or existing.body != payload.work_text
            or existing.partner_installer_id != requested_partner_installer
            or existing.partner_staff_user_id != requested_partner_staff
        ):
            raise ApiError(409, "report_request_conflict", "Request ID already used")
        return {
            "handoff_id": handoff.id,
            "work_status": handoff.work_status,
            "report_id": existing.id,
        }
    if (
        handoff.delivery_status not in {"SENT", "PUBLISHED"}
        or handoff.work_status != "AWAITING_REPORT"
    ):
        raise ApiError(409, "field_work_already_reported", "Field work already reported")
    if active_dialog(db, handoff.id, lock=True) is not None:
        raise ApiError(409, "field_dialog_active", "A Telegram dialog is still active")
    partner_id = payload.partner_id or payload.partner_installer_id
    partner_kind = payload.partner_kind or ("installer" if payload.partner_installer_id else None)
    partner = None
    partner_staff = None
    if partner_id is not None and partner_kind == "installer":
        if partner_id == installer.id:
            raise ApiError(422, "partner_is_self", "Choose another installer")
        partner = db.scalar(
            select(FieldInstaller)
            .join(FieldInstallerDirection)
            .where(
                FieldInstaller.id == partner_id,
                FieldInstaller.is_active.is_(True),
                FieldInstallerDirection.direction == handoff.direction,
            )
        )
        if partner is None:
            raise ApiError(422, "partner_unavailable", "Partner is not available")
    elif partner_id is not None and partner_kind == "staff":
        partner_staff = db.scalar(
            select(User)
            .join(FieldWorkStaffAccess, FieldWorkStaffAccess.user_id == User.id)
            .join(FieldWorkStaffDirection, FieldWorkStaffDirection.user_id == User.id)
            .where(
                User.id == partner_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                FieldWorkStaffAccess.is_enabled.is_(True),
                FieldWorkStaffDirection.direction == handoff.direction,
            )
        )
        if partner_staff is None or not has_permission(
            db, partner_staff, Permission.FIELD_WORK_PERFORM
        ):
            raise ApiError(422, "partner_unavailable", "Partner is not available")
    elif partner_id is not None:
        raise ApiError(422, "partner_unavailable", "Partner type is required")
    now = datetime.now(UTC)
    for prompt in db.scalars(
        select(FieldWorkCompletionPrompt)
        .where(
            FieldWorkCompletionPrompt.handoff_id == handoff.id,
            FieldWorkCompletionPrompt.status.in_(("REQUESTING", "WAITING", "UNCERTAIN")),
        )
        .with_for_update()
    ).all():
        if prompt.status == "WAITING":
            prompt.status = "CANCELED"
            prompt.completed_at = now
            prompt.deletion_status = "PENDING"
        else:
            prompt.status = "FAILED"
            prompt.error_code = "superseded_by_report"
            prior = db.get(FieldDispatchUpdate, prompt.callback_update_id)
            if prior is not None:
                prior.sync_status = "FAILED"
                prior.sync_error_code = "superseded_by_report"
    update = FieldDispatchUpdate(
        bot_identity="oss_field_miniapp",
        update_id=-db.scalar(select(func.nextval("field_miniapp_update_id_seq"))),
        kind="MINIAPP_REPORT",
        outcome="PROCESSED",
        handoff_id=handoff.id,
        card_edit_status="PENDING"
        if handoff.telegram_message_id and handoff.message_text
        else "NOT_REQUIRED",
    )
    db.add(update)
    db.flush()
    report = FieldExternalMessage(
        id=uuid4(),
        handoff_id=handoff.id,
        update_record_id=update.id,
        chat_id=handoff.chat_id,
        telegram_message_id=None,
        sender_telegram_id=installer.telegram_user_id,
        sender_name=installer.name,
        primary_installer_id=installer.id,
        partner_installer_id=partner.id if partner else None,
        partner_staff_user_id=partner_staff.id if partner_staff else None,
        partner_name=(
            partner.name
            if partner
            else partner_staff.display_name
            if partner_staff
            else None
        ),
        client_request_id=payload.client_request_id,
        kind="MINIAPP_REPORT",
        body=payload.work_text,
        telegram_created_at=now,
    )
    record_completed_work(db, handoff, report, reported_at=now)
    db.commit()
    return {"handoff_id": handoff.id, "work_status": handoff.work_status, "report_id": report.id}


def complete_handoff_as_staff(
    handoff_id: UUID,
    payload: CompleteWork,
    user: User,
    allowed: tuple[str, ...],
    db: SessionDep,
):
    """Record a report by verified OCC staff without creating a fake installer."""
    if not has_permission(db, user, Permission.FIELD_WORK_PERFORM):
        raise ApiError(403, "field_work_perform_forbidden", "Permission denied")
    handoff = _mutable_handoff(db, handoff_id, allowed)
    existing = db.scalar(
        select(FieldExternalMessage).where(
            FieldExternalMessage.primary_staff_user_id == user.id,
            FieldExternalMessage.client_request_id == payload.client_request_id,
        )
    )
    requested_partner_installer = (
        payload.partner_id if payload.partner_kind == "installer" else payload.partner_installer_id
    )
    requested_partner_staff = payload.partner_id if payload.partner_kind == "staff" else None
    if existing is not None:
        if (
            existing.handoff_id != handoff_id
            or existing.body != payload.work_text
            or existing.partner_installer_id != requested_partner_installer
            or existing.partner_staff_user_id != requested_partner_staff
        ):
            raise ApiError(409, "report_request_conflict", "Request ID already used")
        return {
            "handoff_id": handoff.id,
            "work_status": handoff.work_status,
            "report_id": existing.id,
        }
    if (
        handoff.delivery_status not in {"SENT", "PUBLISHED"}
        or handoff.work_status != "AWAITING_REPORT"
    ):
        raise ApiError(409, "field_work_already_reported", "Field work already reported")
    if active_dialog(db, handoff.id, lock=True) is not None:
        raise ApiError(409, "field_dialog_active", "A Telegram dialog is still active")
    partner_installer = None
    partner_staff = None
    if requested_partner_installer is not None:
        partner_installer = db.scalar(
            select(FieldInstaller)
            .join(FieldInstallerDirection)
            .where(
                FieldInstaller.id == requested_partner_installer,
                FieldInstaller.is_active.is_(True),
                FieldInstallerDirection.direction == handoff.direction,
            )
        )
        if partner_installer is None:
            raise ApiError(422, "partner_unavailable", "Partner is not available")
    elif requested_partner_staff is not None:
        if requested_partner_staff == user.id:
            raise ApiError(422, "partner_is_self", "Choose another participant")
        partner_staff = db.scalar(
            select(User)
            .join(FieldWorkStaffAccess, FieldWorkStaffAccess.user_id == User.id)
            .join(FieldWorkStaffDirection, FieldWorkStaffDirection.user_id == User.id)
            .where(
                User.id == requested_partner_staff,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                FieldWorkStaffAccess.is_enabled.is_(True),
                FieldWorkStaffDirection.direction == handoff.direction,
            )
        )
        if partner_staff is None or not has_permission(
            db, partner_staff, Permission.FIELD_WORK_PERFORM
        ):
            raise ApiError(422, "partner_unavailable", "Partner is not available")
    now = datetime.now(UTC)
    for prompt in db.scalars(
        select(FieldWorkCompletionPrompt)
        .where(
            FieldWorkCompletionPrompt.handoff_id == handoff.id,
            FieldWorkCompletionPrompt.status.in_(("REQUESTING", "WAITING", "UNCERTAIN")),
        )
        .with_for_update()
    ).all():
        if prompt.status == "WAITING":
            prompt.status = "CANCELED"
            prompt.completed_at = now
            prompt.deletion_status = "PENDING"
        else:
            prompt.status = "FAILED"
            prompt.error_code = "superseded_by_report"
    update = FieldDispatchUpdate(
        bot_identity="oss_field_miniapp",
        update_id=-db.scalar(select(func.nextval("field_miniapp_update_id_seq"))),
        kind="MINIAPP_REPORT",
        outcome="PROCESSED",
        handoff_id=handoff.id,
        card_edit_status="PENDING"
        if handoff.telegram_message_id and handoff.message_text
        else "NOT_REQUIRED",
    )
    db.add(update)
    db.flush()
    staff_access = db.get(FieldWorkStaffAccess, user.id)
    if staff_access is None or staff_access.telegram_user_id is None:
        raise ApiError(403, "staff_forbidden", "Telegram link is unavailable")
    report = FieldExternalMessage(
        id=uuid4(),
        handoff_id=handoff.id,
        update_record_id=update.id,
        chat_id=handoff.chat_id,
        telegram_message_id=None,
        sender_telegram_id=staff_access.telegram_user_id,
        sender_name=user.display_name,
        primary_staff_user_id=user.id,
        partner_installer_id=partner_installer.id if partner_installer else None,
        partner_staff_user_id=partner_staff.id if partner_staff else None,
        partner_name=(
            partner_installer.name
            if partner_installer
            else partner_staff.display_name
            if partner_staff
            else None
        ),
        client_request_id=payload.client_request_id,
        kind="MINIAPP_REPORT",
        body=payload.work_text,
        telegram_created_at=now,
    )
    record_completed_work(db, handoff, report, reported_at=now, actor_id=user.id)
    db.commit()
    return {"handoff_id": handoff.id, "work_status": handoff.work_status, "report_id": report.id}


@router.get("/miniapp/handoffs/{handoff_id}/reschedules")
def reschedule_history(
    handoff_id: UUID,
    auth: InstallerDep,
    db: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    _, allowed = auth
    handoff = _mutable_handoff(db, handoff_id, allowed, lock=False)
    condition = FieldWorkReschedule.handoff_id == handoff.id
    total = db.scalar(select(func.count()).select_from(FieldWorkReschedule).where(condition)) or 0
    rows = db.scalars(
        select(FieldWorkReschedule)
        .where(condition)
        .order_by(FieldWorkReschedule.schedule_version.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "reason": row.reason,
                "scheduled_for": row.scheduled_for,
                "scheduled_date": row.scheduled_date,
                "author_name": row.author_name,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/miniapp/handoffs/{handoff_id}/reschedules")
def reschedule_from_miniapp(
    handoff_id: UUID, payload: MiniappReschedule, auth: InstallerDep, db: SessionDep
):
    installer, allowed = auth
    handoff = _mutable_handoff(db, handoff_id, allowed)
    if payload.scheduled_for is not None and payload.scheduled_date is not None:
        raise ApiError(422, "field_schedule_date_invalid", "Choose a date or date and time")
    if (
        active_prompt(db, handoff.id) is not None
        or active_dialog(db, handoff.id, lock=True) is not None
    ):
        raise ApiError(409, "field_dialog_active", "A Telegram dialog is still active")
    record = record_reschedule(
        db,
        handoff,
        reason=payload.reason,
        scheduled_for=payload.scheduled_for,
        scheduled_date=payload.scheduled_date,
        source="MINIAPP",
        author_name=installer.name,
        telegram_user_id=installer.telegram_user_id,
        expected_version=payload.expected_version,
    )
    db.commit()
    return {
        "id": record.id,
        "schedule_version": handoff.schedule_version,
        "schedule_status": handoff.schedule_status,
    }
