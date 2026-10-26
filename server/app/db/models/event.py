"""medical_events, medical_event_documents 模型（§3.2 第 12/13 项；双时间轴 B-2）。"""

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import Vector


class MedicalEvent(Base):
    """事件时间线（F-07）。occurred_at=业务发生时间（AI 时序推理唯一依据），
    recorded_at=录入时间，两者不互相覆盖（B-2）。"""

    __tablename__ = "medical_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('visit','hospitalization','surgery','checkup',"
            "'medication','vaccine','self_reported','other')",
            name="ck_medical_events_event_type",
        ),
        CheckConstraint(
            "occurred_at_precision IN ('day','month','year','unknown')",
            name="ck_medical_events_occurred_at_precision",
        ),
        CheckConstraint("event_source IN ('document','chat','manual')",
                        name="ck_medical_events_event_source"),
        # B-4 幂等防重：同文档同事件唯一
        Index(
            "uq_event_draft", "document_id", "draft_id", unique=True,
            postgresql_where=text("document_id IS NOT NULL AND draft_id IS NOT NULL"),
            sqlite_where=text("document_id IS NOT NULL AND draft_id IS NOT NULL"),
        ),
        Index("idx_event_subject_time", "subject_id", text("occurred_at DESC")),
        # idx_event_embedding（ivfflat）仅迁移创建（需 pgvector），模型不建
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text, ForeignKey("accounts.id"), nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, ForeignKey("subjects.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[date] = mapped_column(Date, nullable=False)
    occurred_at_precision: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="day"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    narrative: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diagnosis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="document"
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("documents.id"), nullable=True  # sourceDocumentId 主证据
    )
    draft_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(1024), nullable=True)  # P1 L3 填充，P0 NULL
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MedicalEventDocument(Base):
    """事件 ↔ 证据多对多 refs[]。"""

    __tablename__ = "medical_event_documents"

    event_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("medical_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.id"), primary_key=True
    )
    ref_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
