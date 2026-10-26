"""document_blocks 模型（R1 T103）：页级文本块与来源锚点。

为什么需要：长期档案要求"点击指标/回答引用 → 跳到原件对应位置"。
仅靠 `documents.file_key` 只能打开文件首页，无法定位到具体页甚至区域。

关键设计：
1. **锚点绑定不可变版本**：块记录 `asset_version` 与 `extraction_version`，
   同一页在不同抽取版本下可有不同块；引用必须固定版本，否则重抽后
   旧引用会指向错误内容。
2. **坐标可缺省**：没有可靠 bbox 时只保证页级，标记 `anchor_status`，
   **绝不伪造坐标**。
3. **坐标系必须声明**：`coordinate_system` 说明 bbox 的单位与原点
   （如 `pixel_top_left` / `normalized_0_1`），否则坐标无意义。
4. 块文本是**派生产物**（可重建），不是事实；事实仍是原件与确认后的记录。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 锚点状态：能否定位到区域
ANCHOR_PAGE_ONLY = "page_only"    # 只能定位到页
ANCHOR_REGION = "region"          # 可定位到区域
ANCHOR_UNAVAILABLE = "unavailable"  # 连页都不可靠（罕见）

ANCHOR_STATUSES = (ANCHOR_PAGE_ONLY, ANCHOR_REGION, ANCHOR_UNAVAILABLE)

# 坐标系
COORD_PIXEL_TOP_LEFT = "pixel_top_left"
COORD_NORMALIZED = "normalized_0_1"
COORD_TABLE_CELL = "table_cell"

COORDINATE_SYSTEMS = (COORD_PIXEL_TOP_LEFT, COORD_NORMALIZED, COORD_TABLE_CELL)


class DocumentBlock(Base):
    """页级文本块（带来源锚点）。"""

    __tablename__ = "document_blocks"
    __table_args__ = (
        CheckConstraint(
            "anchor_status IN ('page_only','region','unavailable')",
            name="ck_document_blocks_anchor_status",
        ),
        CheckConstraint(
            "coordinate_system IN ('pixel_top_left','normalized_0_1','table_cell')",
            name="ck_document_blocks_coordinate_system",
        ),
        # 同一抽取版本内 (document, version, page, block) 唯一
        UniqueConstraint(
            "document_id", "extraction_version", "page_no", "block_id",
            name="uq_document_blocks_version_page_block",
        ),
        Index(
            "idx_blocks_doc_version",
            "document_id", "extraction_version", "page_no",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accounts.id"), nullable=False
    )
    subject_id: Mapped[str] = mapped_column(
        Text, ForeignKey("subjects.id"), nullable=False
    )
    # 抽取版本（草稿版本号）：重抽会产生新版本，旧块的引用仍指向旧版本
    extraction_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # 派生资产版本（与 document_assets.version 对应）
    asset_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 块内序号（同一页内稳定）
    block_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 块类型：text/table_row/heading/field
    block_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="text")
    text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # 坐标（可空；为空表示只能定位到页）
    x: Mapped[Optional[float]] = mapped_column(nullable=True)
    y: Mapped[Optional[float]] = mapped_column(nullable=True)
    w: Mapped[Optional[float]] = mapped_column(nullable=True)
    h: Mapped[Optional[float]] = mapped_column(nullable=True)
    coordinate_system: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=COORD_PIXEL_TOP_LEFT
    )
    anchor_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=ANCHOR_PAGE_ONLY
    )
    # 解析器与版本（产物可追溯）
    parser: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parser_version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
