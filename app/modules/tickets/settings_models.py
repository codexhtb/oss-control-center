"""Typed Service Desk catalogs; ticket decisions retain their historical labels."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DecisionReasonCatalog(Base):
    __tablename__ = "decision_reason_catalog"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_decision_reason_sort"),
        CheckConstraint("version > 0", name="ck_decision_reason_version"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_decision_reason_name"),
        Index("ix_decision_reason_order", "sort_order", "code"),
    )

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index("uq_decision_reason_name_ci", func.lower(DecisionReasonCatalog.name), unique=True)


class TicketTextTemplate(Base):
    __tablename__ = "ticket_text_templates"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('decision', 'comment', 'callback')", name="ck_ticket_template_purpose"
        ),
        CheckConstraint("length(btrim(name)) > 0", name="ck_ticket_template_name"),
        CheckConstraint("length(btrim(body)) > 0", name="ck_ticket_template_body"),
        CheckConstraint("sort_order >= 0", name="ck_ticket_template_sort"),
        CheckConstraint("version > 0", name="ck_ticket_template_version"),
        Index("ix_ticket_template_purpose_order", "purpose", "sort_order", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
