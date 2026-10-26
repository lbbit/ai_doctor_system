"""document_assets 模型（R1 T102）：原件与派生资产的统一登记。

为什么需要独立表：长期档案里"一份资料"会派生出多种可读副本
（原件、Markdown 副本、页图、纯文本层）。若只靠 `documents.file_key`
一个字段，就无法表达：

- 派生副本的**版本**（重抽后新增版本，不覆盖旧版）；
- 派生副本与**源文件哈希**的对应关系（源变了要能判断副本是否过期）；
- 副本的**生成器与版本**（同一份原文在不同算法下产物不同，需可区分）；
- 副本的**定位信息**（页码/区域），供"点击引用跳到对应页"。

不变量：
1. `role='original'` 的资产**不可覆盖**（原件是证据）；
2. 同一 (document_id, role, version) 唯一；
3. `source_hash` 记录生成时的源文件哈希，源变化 → 副本需重新生成；
4. 读取必须经归属校验（Account + Subject）与生命周期校验。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
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

# 资产角色
ROLE_ORIGINAL = "original"        # 原件（不可变）
ROLE_MARKDOWN = "markdown"        # 类型化 Markdown 副本
ROLE_TEXT = "normalized_text"     # 纯文本层
ROLE_PAGE_IMAGE = "page_image"    # 页图（渲染产物）
ROLE_PAGE_TEXT = "page_text"      # 页级文本/块（若单独存对象）

ASSET_ROLES = (
    ROLE_ORIGINAL,
    ROLE_MARKDOWN,
    ROLE_TEXT,
    ROLE_PAGE_IMAGE,
    ROLE_PAGE_TEXT,
)


class DocumentAsset(Base):
    """原件/派生资产登记（版本化）。"""

    __tablename__ = "document_assets"
    __table_args__ = (
        CheckConstraint(
            "role IN ('original','markdown','normalized_text','page_image','page_text')",
            name="ck_document_assets_role",
        ),
        UniqueConstraint(
            "document_id", "role", "version",
            name="uq_document_assets_document_role_version",
        ),
        Index("idx_asset_document", "document_id", "role"),
        Index("idx_asset_source_hash", "document_id", "source_hash"),
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
    role: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # 对象存储 key（不暴露真实姓名，只用不可变 ID）
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 内容哈希：用于完整性校验与"副本是否过期"判断
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    # 生成该副本时的**源**内容哈希（原件哈希）；源变化 → 副本过期
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # 生成器与版本（如 'markdown/1.2'、'vlm/1.0'），便于追溯产物差异
    generator: Mapped[str] = mapped_column(Text, nullable=False)
    # 页码（页级资产用），None 表示整篇
    page_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
