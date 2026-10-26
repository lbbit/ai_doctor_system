"""subjects, profiles 模型（§3.2 第 2/4 项；F-02/F-03、L0 上下文来源）。"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import Jsonb


class Subject(Base):
    """被照护对象（P1 留缝：role TEXT DEFAULT 'owner'（viewer/editor），P0 不实现）。"""

    __tablename__ = "subjects"
    __table_args__ = (
        CheckConstraint(
            "relation IN ('本人','父亲','母亲','祖父母','外祖父母','配偶','儿子','女儿','其他')",
            name="ck_subjects_relation",
        ),
        CheckConstraint("gender IN ('male','female','unknown')", name="ck_subjects_gender"),
        # 同档案不重名（可按需放宽）
        UniqueConstraint("account_id", "name", name="uq_subjects_account_id_name"),
        Index(
            "idx_subjects_account", "account_id",
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        # 每 Account 仅一个 is_default=true：部分唯一索引
        Index(
            "uq_subject_default", "account_id", unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False, server_default="本人")
    birthday: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
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

    account: Mapped["Account"] = relationship("Account", back_populates="subjects")  # noqa: F821
    profile: Mapped[Optional["Profile"]] = relationship(  # noqa: F821
        "Profile", back_populates="subject", uselist=False, cascade="all, delete-orphan"
    )


class Profile(Base):
    """L0 静态上下文来源。"""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("blood_type IN ('A','B','AB','O','unknown')",
                        name="ck_profiles_blood_type"),
    )

    subject_id: Mapped[str] = mapped_column(
        Text, ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )
    blood_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    allergies: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    chronic_diseases: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    medications: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    family_history: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    lifestyle: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    extra: Mapped[dict] = mapped_column(
        Jsonb(), nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    subject: Mapped[Subject] = relationship("Subject", back_populates="profile")
