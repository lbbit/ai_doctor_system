"""accounts.role（系统配置写权限收敛）

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22

背景：system_settings 是全局配置（LLM 模型、检索源、限额），此前任意登录
账户都能 PATCH，造成越权面。本迁移为 accounts 增加 role 字段，
并**回填最早创建的账户为 admin**，保证既有部署升级后仍可管理配置
（否则升级后无人可写，形成自锁）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),
    )
    # 回填：最早创建的账户提升为 admin，避免升级后无人能改配置
    op.execute(
        """
        UPDATE accounts SET role = 'admin'
        WHERE id = (
            SELECT id FROM accounts ORDER BY created_at ASC, id ASC LIMIT 1
        )
        """
    )


def downgrade() -> None:
    op.drop_column("accounts", "role")
