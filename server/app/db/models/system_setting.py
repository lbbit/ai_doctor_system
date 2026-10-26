"""system_settings（运行时配置覆盖层，§6.9）。

设计要点：
- 只存「可在线修改」的业务参数；密钥类字段（api_key）**永不入库**，
  仅在 registry 中声明为 read_only_from_env，界面只读展示。
- value_json 使用 JSON 以便不同类型（bool/int/str/对象）共用一张表。
- 敏感值不入库，因此本表不含密文字段，避免误用。
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


class SystemSetting(Base):
    """单条运行时配置覆盖（key 为主键，缺省即回退 env 基线）。"""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    # SQLite 测试用 JSON，PostgreSQL 用 JSONB（with_variant 保证两端可用）
    value_json: Mapped[Any] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    updated_by: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
