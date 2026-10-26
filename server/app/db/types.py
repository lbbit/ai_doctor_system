"""跨 dialect 共享列类型（PostgreSQL 全量特性 / SQLite 本地验证降级）。

- Vector: PG=VECTOR(dim)（需 pgvector 扩展）；SQLite=同名类型（P0 恒 NULL，仅建列）
- CaseInsensitiveString: PG=CITEXT；SQLite=String(255)
- Jsonb / TextArray: PG=JSONB / TEXT[]；SQLite=JSON
"""

from sqlalchemy import String, types
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB


class Vector(types.UserDefinedType):
    """pgvector 列（§3.2 VECTOR(1024)，P0 只建列不填充，P1 填充）。"""

    cache_ok = True

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return f"VECTOR({self.dim})"

    def bind_processor(self, dialect):
        return None

    def result_processor(self, dialect, coltype):
        return None


class CaseInsensitiveString(types.TypeDecorator):
    """email 列：PG=CITEXT（不区分大小写唯一）；SQLite=String。"""

    impl = String(255)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(CITEXT())
        return dialect.type_descriptor(String(255))


def Jsonb() -> types.TypeEngine:
    """JSONB（PG）/ JSON（SQLite）。"""
    return JSONB().with_variant(types.JSON(), "sqlite")


def TextArray() -> types.TypeEngine:
    """TEXT[]（PG）/ JSON（SQLite）。"""
    return ARRAY(String).with_variant(types.JSON(), "sqlite")
