"""red_flag_rules, red_flag_hits 模型（§3.2 第 20/21 项；F-11/A-1）。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import Jsonb, TextArray


class RedFlagRule(Base):
    """红旗规则（可热更新：TTL 轮询 + Redis pub/sub 失效，§6.3）。"""

    __tablename__ = "red_flag_rules"
    __table_args__ = (
        CheckConstraint("keyword_logic IN ('any','all')",
                        name="ck_red_flag_rules_keyword_logic"),
        CheckConstraint("severity IN ('high','medium')", name="ck_red_flag_rules_severity"),
        UniqueConstraint("code", name="uq_red_flag_rules_code"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default="成人急症")
    keywords: Mapped[list] = mapped_column(TextArray(), nullable=False)  # 词表（正则串）
    keyword_logic: Mapped[str] = mapped_column(Text, nullable=False, server_default="any")
    context_logic: Mapped[Optional[dict]] = mapped_column(Jsonb(), nullable=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="high")
    message: Mapped[str] = mapped_column(Text, nullable=False)  # 强制展示的转诊文案
    action: Mapped[str] = mapped_column(Text, nullable=False, server_default="refer_now")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    hits: Mapped[List["RedFlagHit"]] = relationship("RedFlagHit", back_populates="rule")


class RedFlagHit(Base):
    """红旗命中记录（A-1 命中即审计）。"""

    __tablename__ = "red_flag_hits"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        Text, ForeignKey("red_flag_rules.id"), nullable=False
    )
    message_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject_id: Mapped[str] = mapped_column(Text, ForeignKey("subjects.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(Text, ForeignKey("accounts.id"), nullable=False)
    matched_terms: Mapped[Optional[list]] = mapped_column(TextArray(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    rule: Mapped[RedFlagRule] = relationship("RedFlagRule", back_populates="hits")
