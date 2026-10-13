"""Subscriber 360 routes; mock by default, NetUP only by explicit configuration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.config import get_settings
from app.core.errors import ApiError
from app.modules.billing.mock import MockBillingProvider
from app.modules.billing.netup.config import NetupConfig
from app.modules.billing.netup.provider import NetUPBillingProvider
from app.modules.billing.provider import BillingProvider, UnavailableBillingProvider
from app.modules.billing.schemas import LookupRequest, SubscriberCard
from app.modules.billing.service import create_lookup, stored_card
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.identity.service import AuthContext, audit

router = APIRouter(prefix="/subscriber-lookups", tags=["billing"])


def get_billing_provider() -> BillingProvider:
    if get_settings().billing_provider == "NETUP":
        try:
            return NetUPBillingProvider.from_config(
                NetupConfig.from_env(app_env=get_settings().app_env)
            )
        except ValueError:
            return UnavailableBillingProvider()
    return MockBillingProvider()


ProviderDep = Annotated[BillingProvider, Depends(get_billing_provider)]


def _allowed(identity: AuthContext, db: SessionDep) -> AuthContext:
    if Permission.SUBSCRIBER_READ not in permissions_for(Role(identity.user.role)):
        audit(db, "permission_denied", "denied", actor=identity.user.id)
        db.commit()
        raise ApiError(403, "forbidden", "Permission denied")
    return identity


def can_lookup(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db)


def can_read_lookup(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db)


LookupDep = Annotated[AuthContext, Depends(can_lookup)]
LookupReadDep = Annotated[AuthContext, Depends(can_read_lookup)]


@router.post("", response_model=SubscriberCard)
def lookup(
    payload: LookupRequest,
    response: Response,
    identity: LookupDep,
    db: SessionDep,
    provider: ProviderDep,
) -> SubscriberCard:
    snapshot = create_lookup(db, identity.user.id, payload.account_no, provider)
    response.headers["Cache-Control"] = "no-store"
    return stored_card(db, identity.user.id, snapshot.id)


@router.get("/{lookup_id}", response_model=SubscriberCard)
def get_lookup(
    lookup_id: UUID, response: Response, identity: LookupReadDep, db: SessionDep
) -> SubscriberCard:
    response.headers["Cache-Control"] = "no-store"
    return stored_card(db, identity.user.id, lookup_id)
