"""document_assets 表（R1 T102）

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26

背景：长期档案里"一份资料"会派生出多种可读副本（原件、Markdown 副本、页图、
纯文本层）。仅靠 `documents.file_key` 无法表达副本的版本、与源文件的对应关系、
生成器版本与页级定位，因此新增独立登记表。

要点：
- `role='original'` 的行**不可覆盖**（原件是证据）；
- 同一 (document_id, role, version) 唯一；
- `source_hash` 记录生成时的源哈希：源变化后老副本保留但可被标记为 stale；
- `object_key` 只用不可变 ID，不含真实姓名。

回填：历史文档的原件资产无法在不读对象存储的情况下批量登记，
故**不在本迁移回填**；由 `assets.ensure_original_asset()` 按需惰性登记
（读原件 → 登记 → 幂等）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_assets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id", sa.Text(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "account_id", sa.Text(),
            sa.ForeignKey("accounts.id"), nullable=False,
        ),
        sa.Column(
            "subject_id", sa.Text(),
            sa.ForeignKey("subjects.id"), nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("generator", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('original','markdown','normalized_text','page_image','page_text')",
            name="ck_document_assets_role",
        ),
        sa.UniqueConstraint(
            "document_id", "role", "version",
            name="uq_document_assets_document_role_version",
        ),
    )
    op.create_index(
        "idx_asset_document", "document_assets", ["document_id", "role"]
    )
    op.create_index(
        "idx_asset_source_hash", "document_assets", ["document_id", "source_hash"]
    )


def downgrade() -> None:
    op.drop_index("idx_asset_source_hash", table_name="document_assets")
    op.drop_index("idx_asset_document", table_name="document_assets")
    op.drop_table("document_assets")
