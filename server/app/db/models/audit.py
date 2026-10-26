"""audit_logs 模型（§3.2 第 22 项；F-14/B-5 审计不可绕过）。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Jsonb


class AuditLog(Base):
    """业务审计日志（用户可见；who/when/what/前值后值）。
    与运行日志（stdout JSON）分离（§8.4）。"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user','system','worker')",
                        name="ck_audit_logs_actor_type"),
        Index("idx_audit_account_time", "account_id", text("created_at DESC")),
        Index("idx_audit_resource", "resource_type", "resource_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("accounts.id"), nullable=True  # 系统动作为 NULL
    )
    subject_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("subjects.id"), nullable=True
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    actor_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    before_value: Mapped[Optional[dict]] = mapped_column(Jsonb(), nullable=True)
    after_value: Mapped[Optional[dict]] = mapped_column(Jsonb(), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
