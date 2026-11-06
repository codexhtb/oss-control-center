"""Managed catalogs for Service Desk; no executable configuration is accepted."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.identity.access import has_permission
from app.modules.identity.roles import Permission, Role
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.models import TicketResolution
from app.modules.tickets.reason_labels import LEGACY_REASON_NAMES
from app.modules.tickets.settings_models import DecisionReasonCatalog, TicketTextTemplate
from app.modules.tickets.settings_schemas import (
    CreateReason,
    CreateTemplate,
    ReasonView,
    TemplatePurpose,
    TemplateView,
    UpdateReason,
    UpdateTemplate,
)

router = APIRouter(prefix="/settings", tags=["service-desk-settings"])
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _manager(db: SessionDep, identity: AuthContext) -> None:
    if identity.user.role != Role.ADMIN or not has_permission(
        db, identity.user, Permission.SETTINGS_MANAGE
    ):
        raise ApiError(403, "forbidden", "Settings management permission required")


def _version(current: int, supplied: str | None) -> None:
    if supplied is None:
        raise ApiError(428, "version_required", "If-Match version is required")
    if supplied.strip('"') != str(current):
        raise ApiError(409, "version_conflict", "Settings changed; reload the list")


def _snapshot(item: DecisionReasonCatalog | TicketTextTemplate) -> dict:
    fields = (
        ("code", "name", "sort_order", "is_active")
        if isinstance(item, DecisionReasonCatalog)
        else ("id", "name", "body", "purpose", "sort_order", "is_active")
    )
    return {
        field: str(getattr(item, field)) if field == "id" else getattr(item, field)
        for field in fields
    }


@router.get("/reasons", response_model=list[ReasonView])
def reasons(
    identity: CurrentIdentityDep,
    db: SessionDep,
    response: Response,
    include_inactive: Annotated[bool, Query()] = False,
):
    manager = identity.user.role == Role.ADMIN and has_permission(
        db, identity.user, Permission.SETTINGS_MANAGE
    )
    if not manager and not has_permission(db, identity.user, Permission.TICKET_DECISION):
        raise ApiError(403, "forbidden", "Decision permission required")
    if include_inactive and not manager:
        raise ApiError(403, "forbidden", "Settings management permission required")
    query = select(DecisionReasonCatalog)
    if not include_inactive:
        query = query.where(DecisionReasonCatalog.is_active.is_(True))
    response.headers["Cache-Control"] = "no-store"
    return db.scalars(
        query.order_by(DecisionReasonCatalog.sort_order, DecisionReasonCatalog.code)
    ).all()


@router.post("/reasons", response_model=ReasonView, status_code=201)
def create_reason(payload: CreateReason, identity: CsrfIdentityDep, db: SessionDep):
    _manager(db, identity)
    item = DecisionReasonCatalog(code=uuid4().hex.upper(), **payload.model_dump())
    db.add(item)
    try:
        db.flush()
        audit(
            db, "decision_reason_created", "success", actor=identity.user.id,
            details={"after": _snapshot(item)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "reason_name_conflict", "Decision reason name already exists") from None
    db.refresh(item)
    return item


@router.patch("/reasons/{code}", response_model=ReasonView)
def update_reason(
    code: str,
    payload: UpdateReason,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    _manager(db, identity)
    item = db.scalar(
        select(DecisionReasonCatalog).where(DecisionReasonCatalog.code == code).with_for_update()
    )
    if item is None:
        raise ApiError(404, "reason_not_found", "Decision reason not found")
    _version(item.version, if_match)
    if code == "NOT_ESTABLISHED" and payload.is_active is False:
        raise ApiError(
            409, "default_reason_required", "Default reason must remain active for old clients"
        )
    before = _snapshot(item)
    for field in payload.model_fields_set:
        setattr(item, field, getattr(payload, field))
    after = _snapshot(item)
    if before == after:
        raise ApiError(409, "no_change", "No reason fields changed")
    item.version += 1
    item.updated_at = datetime.now(UTC)
    audit(
        db,
        "decision_reason_updated",
        "success",
        actor=identity.user.id,
        details={"before": before, "after": after},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "reason_name_conflict", "Decision reason name already exists") from None
    db.refresh(item)
    return item


@router.delete("/reasons/{code}", status_code=204)
def delete_reason(
    code: str, identity: CsrfIdentityDep, db: SessionDep, if_match: IfMatch = None,
):
    _manager(db, identity)
    item = db.scalar(
        select(DecisionReasonCatalog).where(DecisionReasonCatalog.code == code).with_for_update()
    )
    if item is None:
        raise ApiError(404, "reason_not_found", "Decision reason not found")
    _version(item.version, if_match)
    if code in LEGACY_REASON_NAMES:
        raise ApiError(409, "builtin_reason", "Built-in reason cannot be deleted")
    if db.scalar(select(TicketResolution.id).where(TicketResolution.reason == code).limit(1)):
        raise ApiError(409, "reason_in_use", "Reason is used by a ticket decision")
    before = _snapshot(item)
    db.delete(item)
    audit(db, "decision_reason_deleted", "success", actor=identity.user.id,
          details={"before": before})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "reason_in_use", "Reason is used by a ticket decision") from None
    return Response(status_code=204)


PURPOSE_PERMISSION = {
    TemplatePurpose.DECISION: Permission.TICKET_DECISION,
    TemplatePurpose.COMMENT: Permission.TICKET_COMMENT,
    TemplatePurpose.CALLBACK: Permission.TICKET_CALLBACK,
}


@router.get("/templates", response_model=list[TemplateView])
def templates(
    identity: CurrentIdentityDep,
    db: SessionDep,
    response: Response,
    purpose: TemplatePurpose | None = None,
    include_inactive: Annotated[bool, Query()] = False,
):
    manager = identity.user.role == Role.ADMIN and has_permission(
        db, identity.user, Permission.SETTINGS_MANAGE
    )
    if not manager:
        if purpose is None or not has_permission(db, identity.user, PURPOSE_PERMISSION[purpose]):
            raise ApiError(403, "forbidden", "Ticket action permission required")
        if include_inactive:
            raise ApiError(403, "forbidden", "Settings management permission required")
    query = select(TicketTextTemplate)
    if purpose is not None:
        query = query.where(TicketTextTemplate.purpose == purpose.value)
    if not include_inactive:
        query = query.where(TicketTextTemplate.is_active.is_(True))
    response.headers["Cache-Control"] = "no-store"
    return db.scalars(
        query.order_by(
            TicketTextTemplate.purpose,
            TicketTextTemplate.sort_order,
            TicketTextTemplate.name,
            TicketTextTemplate.id,
        )
    ).all()


@router.post("/templates", response_model=TemplateView, status_code=201)
def create_template(payload: CreateTemplate, identity: CsrfIdentityDep, db: SessionDep):
    _manager(db, identity)
    item = TicketTextTemplate(**payload.model_dump(), created_by=identity.user.id)
    db.add(item)
    db.flush()
    audit(
        db,
        "text_template_created",
        "success",
        actor=identity.user.id,
        details={"after": _snapshot(item)},
    )
    db.commit()
    db.refresh(item)
    return item


@router.patch("/templates/{template_id}", response_model=TemplateView)
def update_template(
    template_id: UUID,
    payload: UpdateTemplate,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    _manager(db, identity)
    item = db.scalar(
        select(TicketTextTemplate).where(TicketTextTemplate.id == template_id).with_for_update()
    )
    if item is None:
        raise ApiError(404, "template_not_found", "Template not found")
    _version(item.version, if_match)
    before = _snapshot(item)
    for field in payload.model_fields_set:
        setattr(item, field, getattr(payload, field))
    after = _snapshot(item)
    if before == after:
        raise ApiError(409, "no_change", "No template fields changed")
    item.version += 1
    item.updated_at = datetime.now(UTC)
    audit(
        db,
        "text_template_updated",
        "success",
        actor=identity.user.id,
        details={"before": before, "after": after},
    )
    db.commit()
    db.refresh(item)
    return item
