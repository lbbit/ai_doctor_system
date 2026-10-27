"""观测幂等唯一索引改为部分索引（R1 T106）

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-26

背景：`uq_obs_doc_field` 原为 (source_document_id, source_field_ref) 唯一，
不含 deleted_at。当一份报告确认入库后再次"重新抽取并确认新版本"时，
新草稿的同一字段（source_field_ref 形如 `obs:0`）会与上一版**已确认**记录
撞唯一约束，被误报为"该报告已在其他页面处理"。

正确语义（T106）：新版本确认时**原子取代**旧的已确认事实——旧记录软删保留
（审计可追溯），新记录生效。因此幂等唯一只应作用于"当前有效"（未软删）行。

本迁移把 `uq_obs_doc_field` 重建为带 `deleted_at IS NULL` 的部分唯一索引：
- 仍保证同一文档同一字段只有一条**有效**观测（幂等不放松）；
- 允许软删历史行与新行共存（取代链）。

事件侧无需改索引：`uq_event_draft` 含 draft_id，不同草稿版本天然不冲突；
取代由应用层软删旧事件实现。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_WHERE = (
    "source_document_id IS NOT NULL AND source_field_ref IS NOT NULL"
)
_NEW_WHERE = (
    "source_document_id IS NOT NULL AND source_field_ref IS NOT NULL "
    "AND deleted_at IS NULL"
)


def upgrade() -> None:
    # drop 旧索引 + 重建为带 deleted_at IS NULL 的部分唯一索引（PG/SQLite 语法一致）
    op.drop_index("uq_obs_doc_field", table_name="observations")
    op.execute(
        "CREATE UNIQUE INDEX uq_obs_doc_field ON observations "
        f"(source_document_id, source_field_ref) WHERE {_NEW_WHERE}"
    )


def downgrade() -> None:
    op.drop_index("uq_obs_doc_field", table_name="observations")
    op.execute(
        "CREATE UNIQUE INDEX uq_obs_doc_field ON observations "
        f"(source_document_id, source_field_ref) WHERE {_OLD_WHERE}"
    )
