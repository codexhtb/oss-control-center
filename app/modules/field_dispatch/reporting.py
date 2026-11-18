"""Shared report transition for Telegram replies and installer Mini App."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.field_dispatch.models import (
    FieldExternalMessage,
    FieldReportNotification,
    FieldWorkHandoff,
)
from app.modules.field_dispatch.personal_notifications import enqueue_notification
from app.modules.tickets.models import Ticket, TicketAudit, TicketEvent


def record_completed_work(
    db: Session,
    handoff: FieldWorkHandoff,
    report: FieldExternalMessage,
    *,
    reported_at: datetime,
    actor_id: UUID | None = None,
) -> None:
    """Append the report, notify engineers and update only the handoff state.

    Caller must hold a row lock on handoff and commit the surrounding transaction.
    """
    if (
        handoff.delivery_status not in {"SENT", "PUBLISHED"}
        or handoff.work_status != "AWAITING_REPORT"
    ):
        raise ApiError(409, "field_work_already_reported", "Field work already reported")
    db.add(report)
    db.add(FieldReportNotification(handoff_id=handoff.id, message_id=report.id))
    handoff.work_status = "REPORTED"
    handoff.reported_at = reported_at
    handoff.updated_at = datetime.now(UTC)
    ticket = db.get(Ticket, handoff.ticket_id)
    db.add(
        TicketEvent(
            ticket_id=handoff.ticket_id,
            event_type="field_work_reported",
            actor_id=actor_id,
            event_data={"handoff_id": str(handoff.id), "message_kind": report.kind},
            ticket_version=ticket.version if ticket is not None else handoff.ticket_version,
        )
    )
    enqueue_notification(db, handoff, "REPORT_PENDING", dedupe_suffix=str(report.id))
    db.add(
        TicketAudit(
            ticket_id=handoff.ticket_id,
            actor_id=actor_id,
            action="field_work_reported",
            outcome="success",
            origin="SYSTEM",
        )
    )
