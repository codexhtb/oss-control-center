"""Read-only installer Mini App queue and OSS-managed installer allowlist."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.v1.identity import SessionDep, require_permission, require_read_permission
from app.core.errors import ApiError
from app.modules.field_dispatch.miniapp_auth import configured_bot_token, verify_init_data
from app.modules.field_dispatch.models import (
    FieldInstaller,
    FieldInstallerDirection,
    FieldWorkHandoff,
)
from app.modules.identity.roles import Permission
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


def _item(handoff: FieldWorkHandoff, ticket: Ticket) -> dict:
    return {
        "id": handoff.id,
        "ticket_id": ticket.id,
        "ticket_number": ticket.number,
        "direction": handoff.direction,
        "work_status": handoff.work_status,
        "subject": ticket.subject or ticket.category_name or "Обращение",
        "sent_at": handoff.sent_at,
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
        FieldWorkHandoff.delivery_status == "SENT",
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
    rows = db.execute(
        source.where(*conditions)
        .order_by(FieldWorkHandoff.sent_at.desc(), FieldWorkHandoff.id.desc())
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
            FieldWorkHandoff.delivery_status == "SENT",
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
    }
