"""SQLAlchemy DeclarativeBase 与命名约定（ARCHITECTURE §3/§8.3）。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase

# 约定：索引 idx_ / 唯一 uq_ 前缀（§8.3）
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """当前 UTC 时间（TIMESTAMPTZ 语义，存 UTC）。"""
    return datetime.now(timezone.utc)


# 通用列工厂：created_at / updated_at（§3.2 全表约定）
def created_at_column():
    return DateTime(timezone=True), dict(
        nullable=False, server_default=func.now(), default=utcnow
    )


def now_utc() -> datetime:
    return utcnow()


def utc_iso(dt: datetime | None) -> str | None:
    """序列化为 ISO8601（Z 结尾），供响应与日志统一格式。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
