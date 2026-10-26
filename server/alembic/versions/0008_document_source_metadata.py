"""文档来源元数据与时间精度（R1 T101）

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-26

背景：长期健康档案要求"按发生时间组织"，但 `documents` 此前只有上传时间
（`created_at`），无法表达"这份报告是什么时候的检查"。同时缺少机构、科室、
记录大类等长期检索必需的元数据。

本次迁移新增（全部可空，不影响既有数据与旧客户端）：
- `documents.record_type`：稳定业务大类（检验/病历/体检/影像/用药/文本…）
- `documents.occurred_start` / `occurred_end` / `time_precision`：发生时间与精度
  （datetime/day/month/year/range/unknown）
- `documents.reported_at`：报告签发时间（与发生时间、录入时间三者不互相覆盖）
- `documents.facility` / `department`：来源机构与科室
- `documents.source_note`：来源说明（人工填写/导入来源）
- `observations.specimen` / `observations.method`：标本与检测方法
  （趋势可比性的前提：血/尿同名项目不得直接连线）

回填原则：历史文档**不得**把上传时间伪装成发生时间。
`occurred_start` 保持 NULL，`time_precision` 置 'unknown'，
由 R2 检索层把这类记录单列为"时间未知"，而不是按上传时间排序冒充发生时间。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("record_type", sa.Text(), nullable=True))
    op.add_column(
        "documents", sa.Column("occurred_start", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("occurred_end", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("documents", sa.Column("time_precision", sa.Text(), nullable=True))
    op.add_column(
        "documents", sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("documents", sa.Column("facility", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("department", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("source_note", sa.Text(), nullable=True))

    op.add_column("observations", sa.Column("specimen", sa.Text(), nullable=True))
    op.add_column("observations", sa.Column("method", sa.Text(), nullable=True))

    # 历史文档：时间未知（不得用上传时间冒充发生时间）
    op.execute(
        "UPDATE documents SET time_precision = 'unknown' "
        "WHERE time_precision IS NULL"
    )
    op.execute(
        "UPDATE documents SET record_type = 'other' WHERE record_type IS NULL"
    )

    op.create_index(
        "idx_doc_subject_occurred", "documents", ["subject_id", "occurred_start"]
    )


def downgrade() -> None:
    op.drop_index("idx_doc_subject_occurred", table_name="documents")
    op.drop_column("observations", "method")
    op.drop_column("observations", "specimen")
    op.drop_column("documents", "source_note")
    op.drop_column("documents", "department")
    op.drop_column("documents", "facility")
    op.drop_column("documents", "reported_at")
    op.drop_column("documents", "time_precision")
    op.drop_column("documents", "occurred_end")
    op.drop_column("documents", "occurred_start")
    op.drop_column("documents", "record_type")
