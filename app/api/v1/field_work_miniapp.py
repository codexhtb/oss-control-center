"""Read-only installer Mini App queue and OSS-managed installer allowlist."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.v1.identity import SessionDep, require_permission, require_read_permission
from app.core.errors import ApiError
from app.modules.field_dispatch.miniapp_auth import configured_bot_token, verify_init_data
from app.modules.field_dispatch.models import (
    FieldDispatchUpdate,
    FieldExternalMessage,
    FieldInstaller,
    FieldInstallerDirection,
    FieldWorkCompletionPrompt,
    FieldWorkHandoff,
    FieldWorkReschedule,
)
from app.modules.field_dispatch.reporting import record_completed_work
from app.modules.field_dispatch.reschedule_dialog import active_dialog
from app.modules.field_dispatch.rescheduling import active_prompt, record_reschedule
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


class CompleteWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    partner_installer_id: UUID | None = None
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
    statement = (select(FieldWorkHandoff)
        .join(Ticket, Ticket.id == FieldWorkHandoff.ticket_id)
        .where(
            FieldWorkHandoff.id == handoff_id,
            FieldWorkHandoff.direction.in_(allowed),
            Ticket.deleted_at.is_(None),
            Ticket.ticket_type != "INTERNAL",
        )
        .execution_options(populate_existing=True))
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
    return {"items": [{"id": item.id, "name": item.name} for item in rows]}


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
        if (
            existing.handoff_id != handoff_id
            or existing.body != payload.work_text
            or existing.partner_installer_id != payload.partner_installer_id
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
    partner = None
    if payload.partner_installer_id is not None:
        if payload.partner_installer_id == installer.id:
            raise ApiError(422, "partner_is_self", "Choose another installer")
        partner = db.scalar(
            select(FieldInstaller)
            .join(FieldInstallerDirection)
            .where(
                FieldInstaller.id == payload.partner_installer_id,
                FieldInstaller.is_active.is_(True),
                FieldInstallerDirection.direction == handoff.direction,
            )
        )
        if partner is None:
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
        partner_name=partner.name if partner else None,
        client_request_id=payload.client_request_id,
        kind="MINIAPP_REPORT",
        body=payload.work_text,
        telegram_created_at=now,
    )
    record_completed_work(db, handoff, report, reported_at=now)
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
