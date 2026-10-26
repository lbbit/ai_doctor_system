"""document_blocks 表（R1 T103）

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-26

背景：长期档案要求"点击指标/回答引用 → 跳到原件对应页/区域"。仅靠
`documents.file_key` 只能打开文件首页。

关键设计：
- 块记录**抽取版本**（`extraction_version`）与**派生资产版本**（`asset_version`），
  重抽产生新版本，旧版本块保留——否则旧引用会指向被改写的内容；
- 坐标可空：没有可靠 bbox 时只给页级锚点（`anchor_status='page_only'`），
  **绝不伪造坐标**；
- 坐标系必须声明（`coordinate_system`），否则坐标无意义。

唯一键含版本：(document_id, extraction_version, page_no, block_id)。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_blocks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id", sa.Text(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("account_id", sa.Text(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("subject_id", sa.Text(), sa.ForeignKey("subjects.id"), nullable=False),
        sa.Column(
            "extraction_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("asset_version", sa.Integer(), nullable=True),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Text(), nullable=False),
        sa.Column("block_type", sa.Text(), nullable=False, server_default="text"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("w", sa.Float(), nullable=True),
        sa.Column("h", sa.Float(), nullable=True),
        sa.Column(
            "coordinate_system", sa.Text(),
            nullable=False, server_default="pixel_top_left",
        ),
        sa.Column(
            "anchor_status", sa.Text(),
            nullable=False, server_default="page_only",
        ),
        sa.Column("parser", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "anchor_status IN ('page_only','region','unavailable')",
            name="ck_document_blocks_anchor_status",
        ),
        sa.CheckConstraint(
            "coordinate_system IN ('pixel_top_left','normalized_0_1','table_cell')",
            name="ck_document_blocks_coordinate_system",
        ),
        sa.UniqueConstraint(
            "document_id", "extraction_version", "page_no", "block_id",
            name="uq_document_blocks_version_page_block",
        ),
    )
    op.create_index(
        "idx_blocks_doc_version",
        "document_blocks",
        ["document_id", "extraction_version", "page_no"],
    )


def downgrade() -> None:
    op.drop_index("idx_blocks_doc_version", table_name="document_blocks")
    op.drop_table("document_blocks")
