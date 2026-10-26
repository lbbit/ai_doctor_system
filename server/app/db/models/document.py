"""documents, document_pages 模型（§3.2 第 5/6 项；F-04/F-13、状态机 PRD §9.2）。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Document 合法状态（状态机，§3.3）
DOC_STATUSES = (
    "pending",
    "extracting",
    "awaiting_review",
    "confirmed",
    "parsed_no_data",
    "failed",
    "archived",
)


class Document(Base):
    """原件证据层（B-3 永不物理删除，仅 archive 软删）。"""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("kind IN ('PDF','IMAGE','DICOM','AUDIO','TEXT')",
                        name="ck_documents_kind"),
        CheckConstraint(
            "doc_type IN ('lab_report','imaging_report','visit_record','other')",
            name="ck_documents_doc_type",
        ),
        CheckConstraint(
            "status IN ('pending','extracting','awaiting_review','confirmed',"
            "'parsed_no_data','failed','archived')",
            name="ck_documents_status",
        ),
        CheckConstraint("size_bytes <= 20971520", name="ck_documents_size_bytes"),
        # E-6 同 subject 同内容去重（强制上传需显式覆盖，服务层处理）
        Index(
            "uq_doc_hash", "subject_id", "content_hash", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("idx_doc_subject_status", "subject_id", "status"),
        # R0 T004：处理状态与生命周期正交，各自建索引（列表按维度筛选）
        Index("idx_doc_subject_processing", "subject_id", "processing_status"),
        Index("idx_doc_subject_lifecycle", "subject_id", "lifecycle_status"),
        # R1 T101：按发生时间检索/排序（R2 混合检索的元数据过滤通道）
        Index("idx_doc_subject_occurred", "subject_id", text("occurred_start DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accounts.id"), nullable=False
    )
    subject_id: Mapped[str] = mapped_column(  # 写入必须选档（B-1）
        Text, ForeignKey("subjects.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="IMAGE")
    doc_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # R1 T101：稳定的业务大类（长期组织与筛选用），与可扩展的 doc_type 并存
    record_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # R1 T101：发生时间（医疗事实时间）与精度；reported_at=报告签发时间
    occurred_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    occurred_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    time_precision: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # R1 T101：来源机构/科室（标准主数据待 R1 T108 治理）
    facility: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # R1 T101：来源说明（人工填写/导入来源），便于追溯"这条记录从哪来"
    source_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 旧单一 status：迁移期仍为写权威，派生列由 _sync_state 统一同步
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # 处理状态（与生命周期正交，R0 T004）：pending/processing/awaiting_review/processed/failed
    processing_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 生命周期：active/archived/deletion_pending（软删=不在默认视图，不等于物理删除）
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active"
    )
    # processed 细分：confirmed（已确认入库）/ no_data（已解析但无临床字段）
    content_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 历史行无法推断处理状态时置 True，要求人工/后台对账，不得猜测为 processed
    needs_reconcile: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    file_key: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 乐观锁（E-7），确认时 +1
    review_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pages: Mapped[List["DocumentPage"]] = relationship(
        "DocumentPage", back_populates="document", cascade="all, delete-orphan",
        order_by="DocumentPage.page_no",
    )


class DocumentPage(Base):
    """多页 PDF/VLM 分页处理。"""

    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_no",
                         name="uq_document_pages_document_id_page_no"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    image_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    document: Mapped[Document] = relationship("Document", back_populates="pages")
