"""documents 处理状态与生命周期正交（R0 T004）

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26

背景：`documents.status` 同时表达"抽取处理进度"与"归档"。归档会把
`confirmed` 覆盖成 `archived`，导致"已确认"这一临床事实被生命周期动作抹掉，
也无法表达"已归档 · 待确认"这类组合。

本次迁移：
- 新增 `processing_status`（pending/processing/awaiting_review/processed/failed）
- 新增 `lifecycle_status`（active/archived/deletion_pending）
- 新增 `content_status`（confirmed/no_data，processed 的细分）
- 新增 `needs_reconcile`（历史行无法反推处理状态时置真，**不得猜为 processed**）
- 旧 `status` 列保留为**派生投影**，由应用层单一同步点维护，旧客户端行为不变。

回填规则：
- `status='archived'` → lifecycle=archived，processing=pending，needs_reconcile=true
  （归档前的处理状态在旧模型里已被覆盖，无法恢复，只能标记待对账）
- 其余按旧状态映射到 processing_status/content_status。

兼容期策略：旧 `status` 仍为写权威之一（应用层同步），本迁移不删列、不加
非空约束；待应用层切读新列并通过全量回归后，再由后续迁移移除旧列。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_MAP = {
    "pending": ("pending", None),
    "extracting": ("processing", None),
    "awaiting_review": ("awaiting_review", None),
    "confirmed": ("processed", "confirmed"),
    "parsed_no_data": ("processed", "no_data"),
    "failed": ("failed", None),
}


def upgrade() -> None:
    op.add_column("documents", sa.Column("processing_status", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("content_status", sa.Text(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "lifecycle_status",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "needs_reconcile",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 回填：归档行无法反推处理状态 → pending + needs_reconcile
    op.execute(
        """
        UPDATE documents
        SET processing_status = 'pending',
            content_status = NULL,
            lifecycle_status = 'archived',
            needs_reconcile = true
        WHERE status = 'archived'
        """
    )
    for legacy, (processing, content) in _LEGACY_MAP.items():
        op.execute(
            f"""
            UPDATE documents
            SET processing_status = '{processing}',
                content_status = {("'" + content + "'") if content else "NULL"},
                lifecycle_status = 'active',
                needs_reconcile = false
            WHERE status = '{legacy}'
            """
        )

    op.create_index(
        "idx_doc_subject_processing", "documents", ["subject_id", "processing_status"]
    )
    op.create_index(
        "idx_doc_subject_lifecycle", "documents", ["subject_id", "lifecycle_status"]
    )


def downgrade() -> None:
    op.drop_index("idx_doc_subject_lifecycle", table_name="documents")
    op.drop_index("idx_doc_subject_processing", table_name="documents")
    op.drop_column("documents", "needs_reconcile")
    op.drop_column("documents", "lifecycle_status")
    op.drop_column("documents", "content_status")
    op.drop_column("documents", "processing_status")
