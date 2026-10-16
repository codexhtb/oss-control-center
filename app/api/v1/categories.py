"""Managed Service Desk categories with immutable ticket labels."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.identity.access import has_permission
from app.modules.identity.roles import Permission
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.models import TicketCategory
from app.modules.tickets.schemas import CategoryView, CreateCategory, UpdateCategory

router = APIRouter(prefix="/ticket-categories", tags=["ticket-categories"])


def _can_manage(db: SessionDep, identity: AuthContext) -> None:
    if not has_permission(db, identity.user, Permission.IDENTITY_MANAGE):
        raise ApiError(403, "forbidden", "Permission denied")


@router.get("", response_model=list[CategoryView])
def categories(
    identity: CurrentIdentityDep,
    db: SessionDep,
    response: Response,
    include_inactive: Annotated[bool, Query()] = False,
):
    manage = has_permission(db, identity.user, Permission.IDENTITY_MANAGE)
    if not manage and not has_permission(db, identity.user, Permission.TICKET_CREATE):
        raise ApiError(403, "forbidden", "Permission denied")
    if include_inactive and not manage:
        raise ApiError(403, "forbidden", "Permission denied")
    query = select(TicketCategory)
    if not include_inactive:
        query = query.where(TicketCategory.is_active.is_(True))
    response.headers["Cache-Control"] = "no-store"
    return db.scalars(
        query.order_by(TicketCategory.sort_order, TicketCategory.name).limit(200)
    ).all()


@router.post("", response_model=CategoryView, status_code=201)
def create_category(payload: CreateCategory, identity: CsrfIdentityDep, db: SessionDep):
    _can_manage(db, identity)
    item = TicketCategory(name=payload.name, sort_order=payload.sort_order, is_other=False)
    db.add(item)
    try:
        db.flush()
        audit(
            db,
            "category_created",
            "success",
            actor=identity.user.id,
            details={"category_id": str(item.id), "name": item.name},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "category_name_conflict", "Category name already exists") from None
    db.refresh(item)
    return item


@router.patch("/{category_id}", response_model=CategoryView)
def update_category(
    category_id: UUID,
    payload: UpdateCategory,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    _can_manage(db, identity)
    item = db.scalar(
        select(TicketCategory).where(TicketCategory.id == category_id).with_for_update()
    )
    if item is None:
        raise ApiError(404, "category_not_found", "Category not found")
    if if_match is None:
        raise ApiError(428, "version_required", "If-Match category version is required")
    if if_match.strip('"') != str(item.version):
        raise ApiError(409, "version_conflict", "Category changed; reload it")
    before = {field: getattr(item, field) for field in payload.model_fields_set}
    for field in payload.model_fields_set:
        setattr(item, field, getattr(payload, field))
    if before == {field: getattr(item, field) for field in payload.model_fields_set}:
        raise ApiError(409, "no_change", "No category fields changed")
    item.version += 1
    from datetime import UTC, datetime

    item.updated_at = datetime.now(UTC)
    try:
        audit(
            db,
            "category_updated",
            "success",
            actor=identity.user.id,
            details={"category_id": str(item.id), "fields": sorted(payload.model_fields_set)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "category_name_conflict", "Category name already exists") from None
    db.refresh(item)
    return item
