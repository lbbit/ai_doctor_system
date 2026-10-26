"""extraction_jobs 增加阶段/进度字段（抽取可观测性）

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21

背景：用户反馈“上传后一直显示抽取中，看不到进度”。仅靠 state 无法表达
“读取原件 / 渲染页图 / 第 n 页识别 / 字典映射”等阶段，也无法展示页级进度，
因此为 extraction_jobs 增加 stage / progress_done / progress_total。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("extraction_jobs", sa.Column("stage", sa.Text(), nullable=True))
    op.add_column(
        "extraction_jobs", sa.Column("progress_done", sa.Integer(), nullable=True)
    )
    op.add_column(
        "extraction_jobs", sa.Column("progress_total", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("extraction_jobs", "progress_total")
    op.drop_column("extraction_jobs", "progress_done")
    op.drop_column("extraction_jobs", "stage")
