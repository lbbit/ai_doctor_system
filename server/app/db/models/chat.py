"""conversations, messages, message_drafts, traces, trace_refs 模型（§3.2 第 15-19 项）。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import Jsonb, TextArray


class Conversation(Base):
    """问诊会话（F-09；状态机 active → archived；A-7 会话级锁定 Subject）。"""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('active','archived')", name="ck_conversations_status"),
        Index("idx_conv_subject", "subject_id", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text, ForeignKey("accounts.id"), nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, ForeignKey("subjects.id"), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[List["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_messages_role"),
        Index("idx_msg_conv", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    red_flag_hit_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_state: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # 'timeout' 等（E-5，半截内容不落 Trace）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="messages")
    draft: Mapped[Optional["MessageDraft"]] = relationship(  # noqa: F821
        "MessageDraft", back_populates="message", uselist=False, cascade="all, delete-orphan"
    )


class MessageDraft(Base):
    """对话式草稿卡片（F-12；A-8 先草稿后入库）。"""

    __tablename__ = "message_drafts"
    __table_args__ = (
        CheckConstraint("draft_type IN ('event','observation','both')",
                        name="ck_message_drafts_draft_type"),
        CheckConstraint("status IN ('pending','confirmed','discarded')",
                        name="ck_message_drafts_status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    message_id: Mapped[str] = mapped_column(
        Text, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[str] = mapped_column(Text, ForeignKey("subjects.id"), nullable=False)
    draft_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(Jsonb(), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_event_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("medical_events.id"), nullable=True
    )
    result_observation_ids: Mapped[Optional[list]] = mapped_column(
        TextArray(), nullable=True, server_default=text("'{}'")
    )
    discarded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    message: Mapped[Message] = relationship("Message", back_populates="draft")


class Trace(Base):
    """回答溯源（F-10；A-3：一次回答恰好 1 条 Trace，失败无 Trace）。"""

    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_trace_message"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    message_id: Mapped[str] = mapped_column(
        Text, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[str] = mapped_column(Text, ForeignKey("subjects.id"), nullable=False)
    context_layers: Mapped[dict] = mapped_column(
        Jsonb(), nullable=False, server_default=text("'{}'")
    )
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    refs: Mapped[List["TraceRef"]] = relationship(
        "TraceRef", back_populates="trace", cascade="all, delete-orphan",
        order_by="TraceRef.ref_index",
    )


class TraceRef(Base):
    """引用明细（ref_index 与正文 [1][2] 编号一一对应；多态 ID 由 service 校验归属）。"""

    __tablename__ = "trace_refs"
    __table_args__ = (
        CheckConstraint("ref_type IN ('observation','event','document')",
                        name="ck_trace_refs_ref_type"),
        Index("idx_trace_ref", "trace_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("traces.id", ondelete="CASCADE"), nullable=False
    )
    ref_index: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)  # 不建 FK，service 校验归属
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    trace: Mapped[Trace] = relationship("Trace", back_populates="refs")
