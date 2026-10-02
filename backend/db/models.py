from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("retailer", "deduction_ref", name="uq_case_retailer_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    retailer: Mapped[str] = mapped_column(String(32), index=True)
    deduction_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deduction_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    window_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    claimed_cents: Mapped[int] = mapped_column(Integer, default=0)
    invalid_cents: Mapped[int] = mapped_column(Integer, default=0)
    recovered_cents: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    documents: Mapped[list["Document"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="AuditEvent.id"
    )
    dispute: Mapped["Dispute | None"] = relationship(back_populates="case", uselist=False, cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[str] = mapped_column(String(24), default="unknown")
    classify_stage: Mapped[str] = mapped_column(String(16), default="none")
    classify_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ocr_method: Mapped[str] = mapped_column(String(16), default="none")
    text: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)

    case: Mapped[Case] = relationship(back_populates="documents")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    agent: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(48))
    reasoning: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    case: Mapped[Case] = relationship(back_populates="audit_events")


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="DRAFTED")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    letter: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    erp_memo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    case: Mapped[Case] = relationship(back_populates="dispute")
