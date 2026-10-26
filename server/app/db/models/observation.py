"""observations, indicator_dictionary, indicator_ref_ranges 模型（§3.2 第 10/11/14 项）。"""

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import Jsonb, TextArray, Vector


class IndicatorDictionary(Base):
    """指标字典（冷启动 TOP50，scripts/seed 导入；§9.3）。"""

    __tablename__ = "indicator_dictionary"
    __table_args__ = (
        UniqueConstraint("code", name="uq_indicator_dictionary_code"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name_cn: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default="其他")
    unit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aliases: Mapped[list] = mapped_column(
        TextArray(), nullable=False, server_default=text("'{}'")
    )
    loinc_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # P2 联网对齐留缝
    is_top_trend: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )  # 是否 TOP20 趋势指标（F-08）
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    ref_ranges: Mapped[List["IndicatorRefRange"]] = relationship(
        "IndicatorRefRange", back_populates="dictionary", cascade="all, delete-orphan"
    )


class IndicatorRefRange(Base):
    """参考区间版本化（§9.3：换试剂盒/换指南不改历史）。"""

    __tablename__ = "indicator_ref_ranges"
    __table_args__ = (
        UniqueConstraint("dictionary_id", "version",
                         name="uq_indicator_ref_ranges_dictionary_id_version"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    dictionary_id: Mapped[str] = mapped_column(
        Text, ForeignKey("indicator_dictionary.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    low_value: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    high_value: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    text_range: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 如 "阴性"
    applies_to: Mapped[dict] = mapped_column(
        Jsonb(), nullable=False, server_default=text("'{}'")
    )  # {"gender":"male","age_min":18,"age_max":70}
    effective_from: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    dictionary: Mapped[IndicatorDictionary] = relationship(
        "IndicatorDictionary", back_populates="ref_ranges"
    )


class Observation(Base):
    """结构化指标时序（F-08；数值计算走 SQL，A-6）。"""

    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint("abnormal_flag IN ('N','L','H','A')",
                        name="ck_observations_abnormal_flag"),
        # B-4 幂等：同一文档 + 同一字段来源不重复入库
        Index(
            "uq_obs_doc_field", "source_document_id", "source_field_ref", unique=True,
            postgresql_where=text(
                "source_document_id IS NOT NULL AND source_field_ref IS NOT NULL"
            ),
            sqlite_where=text(
                "source_document_id IS NOT NULL AND source_field_ref IS NOT NULL"
            ),
        ),
        Index("idx_obs_subject_code_time", "subject_id", "code", "occurred_at"),
        Index("idx_obs_subject_time", "subject_id", text("occurred_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(Text, ForeignKey("accounts.id"), nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, ForeignKey("subjects.id"), nullable=False)
    event_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("medical_events.id", ondelete="SET NULL"), nullable=True
    )
    dictionary_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("indicator_dictionary.id"), nullable=True  # 未命中为 NULL
    )
    code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 冗余字典码
    display_name: Mapped[str] = mapped_column(Text, nullable=False)  # 未命中=原文（E-3）
    value_num: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    value_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ref_low: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    ref_high: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    ref_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    abnormal_flag: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[date] = mapped_column(Date, nullable=False)  # 双时间轴（B-2）
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_document_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("documents.id"), nullable=True
    )
    source_field_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    embedding = mapped_column(Vector(1024), nullable=True)  # P1 预定义，P0 为 NULL
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
