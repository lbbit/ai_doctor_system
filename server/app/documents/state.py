"""文档处理状态与生命周期的单一权威（R0 T004）。

背景：此前 `documents.status` 同时承担两件事——抽取处理进度与"归档"。
归档会把 `confirmed` 覆盖成 `archived`，导致"已确认"这一临床事实被生命周期
动作抹掉，且"已归档 · 待确认"这类组合无法表达。

本模块把两个维度拆开，并集中所有写入：

- `processing_status`（处理状态）：
  pending → processing → awaiting_review → processed，失败为 failed。
  processed 的两种终态（已确认 / 已解析但无内容）由 `content_status` 区分。
- `lifecycle_status`（生命周期）：active / archived / deletion_pending。
  归档只是"从默认视图与检索隐藏"，不改变处理状态，也不删除原件。

兼容策略：旧列 `status` 保留为**派生投影**，由本模块在每次写入时同步，
使既有前端与 API 客户端行为不变；新代码只读写新列。

不变量：
1. `status` 永远等于 `project_legacy_status(...)`，不允许业务代码直接赋值。
2. 归档/取消归档不改变 `processing_status`。
3. 非法转移抛 `StateInvalidError`（与既有 E_STATE_INVALID 语义一致）。
"""

from datetime import datetime, timezone
from typing import Optional

from app.common.errors import StateInvalidError
from app.db.models import Document

# ---- 处理状态 ----
PENDING = "pending"
PROCESSING = "processing"
AWAITING_REVIEW = "awaiting_review"
PROCESSED = "processed"
FAILED = "failed"

PROCESSING_STATUSES = (
    PENDING, PROCESSING, AWAITING_REVIEW, PROCESSED, FAILED,
)

# processed 的细分（保留旧 confirmed / parsed_no_data 的信息，不丢语义）
CONTENT_CONFIRMED = "confirmed"
CONTENT_NO_DATA = "no_data"
CONTENT_STATUSES = (CONTENT_CONFIRMED, CONTENT_NO_DATA)

# ---- 生命周期 ----
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_ARCHIVED = "archived"
LIFECYCLE_DELETION_PENDING = "deletion_pending"

LIFECYCLE_STATUSES = (
    LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED, LIFECYCLE_DELETION_PENDING,
)

# 处理状态机（由旧 DOC_TRANSITIONS 归并而来；归档不再是处理状态）
PROCESSING_TRANSITIONS: dict[str, set[str]] = {
    PENDING: {PROCESSING, FAILED},
    # processing → pending：worker 租约回收后回退等待重试（不是用户主动重试）
    PROCESSING: {AWAITING_REVIEW, PROCESSED, FAILED, PENDING},
    AWAITING_REVIEW: {PROCESSED, PROCESSING},
    PROCESSED: {AWAITING_REVIEW, PROCESSING},
    FAILED: {PENDING, PROCESSING},
}

# 生命周期状态机
LIFECYCLE_TRANSITIONS: dict[str, set[str]] = {
    LIFECYCLE_ACTIVE: {LIFECYCLE_ARCHIVED, LIFECYCLE_DELETION_PENDING},
    LIFECYCLE_ARCHIVED: {LIFECYCLE_ACTIVE},
    LIFECYCLE_DELETION_PENDING: {LIFECYCLE_ACTIVE},
}

# 旧 status 值 → (processing_status, content_status)
LEGACY_TO_PROCESSING: dict[str, tuple[str, Optional[str]]] = {
    "pending": (PENDING, None),
    "extracting": (PROCESSING, None),
    "awaiting_review": (AWAITING_REVIEW, None),
    "confirmed": (PROCESSED, CONTENT_CONFIRMED),
    "parsed_no_data": (PROCESSED, CONTENT_NO_DATA),
    "failed": (FAILED, None),
}


def project_legacy_status(
    processing_status: str,
    content_status: Optional[str],
    lifecycle_status: str,
) -> str:
    """由新状态派生旧 `status` 值（兼容投影，单一权威，禁止各处分叉实现）。"""
    if lifecycle_status in (LIFECYCLE_ARCHIVED, LIFECYCLE_DELETION_PENDING):
        return "archived"
    if processing_status == PENDING:
        return "pending"
    if processing_status == PROCESSING:
        return "extracting"
    if processing_status == AWAITING_REVIEW:
        return "awaiting_review"
    if processing_status == FAILED:
        return "failed"
    if processing_status == PROCESSED:
        return CONTENT_CONFIRMED if content_status == CONTENT_CONFIRMED else "parsed_no_data"
    return "pending"


def assert_processing_transition(current: str, target: str) -> None:
    """处理状态转移校验；非法转移抛 STATE_INVALID。"""
    if current == target:
        return
    if target not in PROCESSING_TRANSITIONS.get(current, set()):
        raise StateInvalidError(f"处理状态不能从 {current} 转移到 {target}")


def assert_lifecycle_transition(current: str, target: str) -> None:
    """生命周期转移校验；非法转移抛 STATE_INVALID。"""
    if current == target:
        return
    if target not in LIFECYCLE_TRANSITIONS.get(current, set()):
        raise StateInvalidError(f"生命周期不能从 {current} 转移到 {target}")


def _legacy_of(document: Document) -> str:
    return (document.status or PENDING).lower()


def processing_of(document: Document) -> str:
    """读取文档的处理状态（兼容尚未回填的历史行）。

    迁移期语义：活跃文档的新旧列由应用层同步，以新列为准；归档文档的旧列
    恒为 `archived`，处理状态只能从新列读（这正是"归档不抹掉已确认"的关键）。
    """
    if lifecycle_of(document) == LIFECYCLE_ARCHIVED:
        return document.processing_status or PENDING

    return LEGACY_TO_PROCESSING.get(_legacy_of(document), (PENDING, None))[0]


def content_of(document: Document) -> Optional[str]:
    """读取 processed 的细分（confirmed / no_data）。"""
    if lifecycle_of(document) == LIFECYCLE_ARCHIVED:
        return document.content_status
    return LEGACY_TO_PROCESSING.get(_legacy_of(document), (PENDING, None))[1]


def lifecycle_of(document: Document) -> str:
    """读取生命周期状态（兼容历史行）。"""
    if document.lifecycle_status and document.lifecycle_status != LIFECYCLE_ACTIVE:
        return document.lifecycle_status
    if _legacy_of(document) == "archived" or document.archived_at:
        return LIFECYCLE_ARCHIVED
    return document.lifecycle_status or LIFECYCLE_ACTIVE


def _effective_state(document: Document) -> tuple[str, Optional[str]]:
    """当前 (processing, content)：与 processing_of/content_of 同源（活跃以旧列为准）。"""
    if lifecycle_of(document) == LIFECYCLE_ARCHIVED:
        return document.processing_status or PENDING, document.content_status
    return LEGACY_TO_PROCESSING.get(_legacy_of(document), (PENDING, None))


def set_processing_status(
    document: Document,
    target: str,
    *,
    content_status: Optional[str] = None,
    allow_same: bool = True,
) -> None:
    """设置处理状态（唯一入口），并同步旧 `status` 投影。

    `content_status` 仅在进入 PROCESSED 时有意义；离开 PROCESSED 自动清空，
    避免"已确认"残留在重新抽取中的文档上。
    """
    current, _current_content = _effective_state(document)
    if current == target:
        if target == PROCESSED and content_status is not None:
            document.content_status = content_status
    else:
        assert_processing_transition(current, target)
        document.processing_status = target
        document.content_status = content_status if target == PROCESSED else None
        if target == PROCESSED and content_status == CONTENT_CONFIRMED:
            document.needs_reconcile = False
    # 旧列始终写入（活跃文档以旧列为兼容权威），同时保留新列以便归档后仍可读
    document.status = project_legacy_status(
        target,
        document.content_status,
        lifecycle_of(document),
    )


def set_lifecycle_status(
    document: Document,
    target: str,
    *,
    archived_at: Optional[datetime] = None,
) -> None:
    """设置生命周期状态（唯一入口），不改变处理状态。"""
    current = lifecycle_of(document)
    if current != target:
        assert_lifecycle_transition(current, target)
        document.lifecycle_status = target
        if target == LIFECYCLE_ARCHIVED:
            document.archived_at = archived_at or datetime.now(timezone.utc)
        elif target == LIFECYCLE_ACTIVE:
            document.archived_at = None
    # 归档时旧列变为 archived，但新列保留归档前的处理状态
    processing, content = _effective_state(document)
    document.status = project_legacy_status(
        processing, content, lifecycle_of(document)
    )


def _sync_legacy(document: Document) -> None:
    """把当前状态投影回旧 `status` 列（保持旧客户端行为不变）。

    归档时用新列保存的处理状态（保留"归档前已确认"）；活跃时用有效状态。
    """
    processing, content = _effective_state(document)
    document.status = project_legacy_status(
        processing, content, lifecycle_of(document)
    )


def sync_states(document: Document) -> None:
    """外部直接改动旧列后调用，补齐新列（迁移/兼容期使用）。"""
    if not document.processing_status:
        legacy = (document.status or "pending").lower()
        if legacy == "archived":
            document.processing_status = PENDING
            document.content_status = None
            document.needs_reconcile = True
        else:
            processing, content = LEGACY_TO_PROCESSING.get(legacy, (PENDING, None))
            document.processing_status = processing
            document.content_status = content
    if not document.lifecycle_status:
        legacy = (document.status or "").lower()
        document.lifecycle_status = (
            LIFECYCLE_ARCHIVED
            if legacy == "archived" or document.archived_at
            else LIFECYCLE_ACTIVE
        )
    _sync_legacy(document)
