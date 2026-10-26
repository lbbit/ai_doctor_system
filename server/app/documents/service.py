"""文档服务：上传、hash 去重(409)、分类分流准备、创建 Document+Job、入队、
签名 URL、重试、归档（F-04/F-13；E-1/E-6/E-9/E-10、B-3、状态机）。"""

import hashlib
import logging
from datetime import date, datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import require_subject, select_owned
from app.common.errors import (
    ConflictError,
    FileTooLargeError,
    NotFoundError,
    StateInvalidError,
    UnsupportedMediaTypeError,
)
from app.common.ids import new_id
from app.config.settings import settings
from app.db.models import (
    Account,
    DerivedRecord,
    Document,
    ExtractionJob,
    Subject,
)
from app.documents.state import (
    CONTENT_CONFIRMED,
    CONTENT_NO_DATA,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    PENDING,
    PROCESSED,
    assert_lifecycle_transition,
    content_of,
    lifecycle_of,
    processing_of,
    set_lifecycle_status,
    set_processing_status,
    sync_states,
)
from app.documents.storage import get_storage_client, original_key
from app.preprocessing.queue import enqueue_extract

logger = logging.getLogger("app.documents")

# E-10 白名单：JPG/PNG/PDF（AC-4.1）；R1 T105 增加 TXT/Markdown 纯文本
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".pdf", ".txt", ".md", ".markdown"}
_EXT_KIND = {
    ".jpg": "IMAGE", ".jpeg": "IMAGE", ".png": "IMAGE", ".pdf": "PDF",
    ".txt": "TEXT", ".md": "TEXT", ".markdown": "TEXT",
}
_EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
# 纯文本扩展名（R1 T105）
_TEXT_EXT = {".txt", ".md", ".markdown"}

# Document 旧 `status` 列的兼容状态机（PRD §9.2 / §3.3）。
# 注意：这是**旧列的兼容映射**，新代码一律走 `app/documents/state.py` 的
# set_processing_status / set_lifecycle_status（处理状态与生命周期正交）。
# 保留此表仅为旧客户端/旧测试的非法转移校验；`test_document_state.py` 会
# 校验它与新状态机在关键边上保持一致。
DOC_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"extracting", "failed", "archived"},
    "extracting": {"awaiting_review", "failed", "parsed_no_data", "archived"},
    # awaiting_review/parsed_no_data → extracting：用户主动“重新抽取”（异步任务，可观测）
    "awaiting_review": {"confirmed", "parsed_no_data", "extracting", "archived"},
    "confirmed": {"awaiting_review", "archived"},  # 撤回确认（审计留痕）
    "failed": {"pending", "extracting", "archived"},  # 用户重试/重新抽取
    "parsed_no_data": {"extracting", "archived"},
    "archived": set(),
}


def assert_transition(current: str, target: str) -> None:
    """状态机校验：非法转移抛 E_STATE_INVALID（§3.3）。"""
    if target not in DOC_TRANSITIONS.get(current, set()):
        raise StateInvalidError(f"文档状态不能从 {current} 转移到 {target}")


def detect_kind(filename: str, head: bytes) -> Tuple[str, str]:
    """按魔数 + 扩展名判定 kind/mime（离线可用，不依赖 libmagic）。

    R1 T105：纯文本（TXT/Markdown）按**内容**校验——不是看魔数，
    而是确认能解码为文本（含常见中文编码），解码失败则拒绝，
    避免把二进制文件当文本存进来。

    注意：文本判定必须用**完整内容**，不能用被截断的 head——
    截断点可能落在多字节字符中间（如 UTF-8 中文 3 字节），
    会把正常中文文本误判为"解码失败"（2026-10-26 实测）。
    """
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_EXT:
        raise UnsupportedMediaTypeError(
            f"仅支持 JPG/PNG/PDF/TXT/MD（收到 {ext or '未知类型'}）"
        )
    if head.startswith(b"%PDF"):
        return "PDF", "application/pdf"
    if head.startswith(b"\xff\xd8"):
        return "IMAGE", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "IMAGE", "image/png"
    if ext in _TEXT_EXT:
        return _EXT_KIND[ext], _EXT_MIME[ext]
    # 头部不匹配魔数 → 仍按扩展名（内容可疑由解析链判失败）
    return _EXT_KIND[ext], _EXT_MIME[ext]


def is_text_content(data: bytes) -> bool:
    """内容是否为可解码文本（用于 TXT/MD 的内容校验）。"""
    if not data:
        return False
    text, _encoding = decode_text(data)
    if not text:
        return False
    # 含过多控制字符 → 更像二进制
    control = sum(1 for ch in text[:4096] if ord(ch) < 9 or 13 < ord(ch) < 32)
    return control / max(1, len(text[:4096])) < 0.05


def decode_text(data: bytes) -> Tuple[str, str]:
    """解码文本 → (文本, 编码)。失败返回 ("", "")。

    依次尝试 UTF-8、UTF-8-SIG、GB18030（中文常见）。
    """
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return "", ""


def doc_out(d: Document) -> dict:
    """文档视图：旧 `status` 保持兼容，同时暴露正交的两个新维度。"""
    sync_states(d)
    return {
        "id": d.id,
        "subjectId": d.subject_id,
        "kind": d.kind,
        "docType": d.doc_type,
        "status": d.status,
        "processingStatus": processing_of(d),
        "lifecycleStatus": lifecycle_of(d),
        "contentStatus": content_of(d),
        "needsReconcile": bool(d.needs_reconcile),
        # R1 T101：来源元数据
        "recordType": d.record_type,
        "occurredStart": d.occurred_start.isoformat() if d.occurred_start else None,
        "occurredEnd": d.occurred_end.isoformat() if d.occurred_end else None,
        "timePrecision": d.time_precision or "unknown",
        "reportedAt": d.reported_at.isoformat() if d.reported_at else None,
        "facility": d.facility,
        "department": d.department,
        "sourceNote": d.source_note,
        "fileName": d.file_name,
        "mimeType": d.mime_type,
        "sizeBytes": d.size_bytes,
        "pageCount": d.page_count,
        "failureReason": d.failure_reason,
        "reviewVersion": d.review_version,
        "contentHash": d.content_hash,
        "archivedAt": d.archived_at.isoformat() if d.archived_at else None,
        "createdAt": d.created_at.isoformat() if d.created_at else None,
        "updatedAt": d.updated_at.isoformat() if d.updated_at else None,
    }


def _parse_user_span(occurred_at: str, precision: Optional[str]) -> "TimeSpan":
    """把用户填写的发生日期解析为时间区间（R1 T101）。

    支持 `YYYY-MM-DD` / `YYYY-MM` / `YYYY` / `YYYY-MM-DD..YYYY-MM-DD`。
    解析失败一律降级为 unknown（不伪造日期），并由调用方留空。
    """
    from app.common import timeline as tl

    raw = (occurred_at or "").strip()
    if not raw:
        return tl.unknown_span()
    if ".." in raw:
        left, _, right = raw.partition("..")
        try:
            start = datetime.fromisoformat(left.strip())
            end = datetime.fromisoformat(right.strip())
        except ValueError:
            return tl.unknown_span()
        if end <= start:
            return tl.unknown_span()
        return tl.range_span(start, end)
    # 显式精度优先：用户说"这是 2026-03"（月）就按月解析，
    # 不能因为字符串里只有 7 个字符就退化成"无法解析"。
    prec = tl.parse_precision(precision) if precision else None
    if prec in (tl.MONTH, tl.YEAR, tl.RANGE):
        try:
            if prec == tl.YEAR:
                return tl.year_span(int(raw[:4]))
            if prec == tl.MONTH:
                parts = raw.split("-")
                return tl.month_span(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return tl.unknown_span()
    try:
        if len(raw) == 4:
            return tl.year_span(int(raw))
        if len(raw) == 7:
            y, m, _ = raw.split("-")
            return tl.month_span(int(y), int(m))
        d = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return tl.unknown_span()
    if prec is None or prec == tl.UNKNOWN:
        prec = tl.DAY
    return tl.span_for_date(d, prec)


def apply_extracted_metadata(
    document: Document,
    *,
    occurred_at: Optional[str] = None,
    time_precision: Optional[str] = None,
    record_type: Optional[str] = None,
    facility: Optional[str] = None,
    department: Optional[str] = None,
) -> None:
    """把抽取出的元数据写回文档（**不覆盖用户已填写的值**，R1 T101）。

    规则（按字段独立判断，不是一刀切）：
    - 该字段用户填过 → 保留用户值；
    - 该字段用户未填 → 采用抽取值；
    - 抽取也拿不到 → 保持 unknown，**不伪造**。

    "用户填过"的判定：字段当前非空即视为已填写。因此必须在用户填写后
    立即落库；AI 抽取只填补空缺。历史行（字段全空）可被抽取值填充。
    """
    from app.common import timeline as tl

    if occurred_at and not document.occurred_start:
        span = _parse_user_span(occurred_at, time_precision)
        if span.known:
            document.occurred_start = span.start
            document.occurred_end = span.end
            document.time_precision = span.precision
    if record_type and not document.record_type:
        document.record_type = record_type
    if facility and not document.facility:
        document.facility = facility
    if department and not document.department:
        document.department = department
    if not document.time_precision:
        document.time_precision = tl.UNKNOWN


def upload_document(
    db: Session,
    account: Account,
    subject_id: Optional[str],
    filename: str,
    content: bytes,
    force: bool = False,
    request=None,
    occurred_at: Optional[str] = None,
    time_precision: Optional[str] = None,
    record_type: Optional[str] = None,
    facility: Optional[str] = None,
    department: Optional[str] = None,
    source_note: Optional[str] = None,
) -> dict:
    """上传：B-1 选档 → E-10 校验 → hash 去重(E-6) → 存 MinIO →
    INSERT documents(pending) + extraction_jobs(queued) → 入队（§5.1 时序）。

    R1 T101：可携带**用户填写的来源元数据**（发生日期、记录类型、机构、科室）。
    用户填写优先于 AI 推断：后续重新抽取**不得**覆盖这些值
    （见 `_apply_extracted_metadata`）。
    """
    subject = require_subject(db, account, subject_id)  # 缺失 → 422

    size = len(content)
    if size == 0:
        raise UnsupportedMediaTypeError("空文件")
    if size > settings.max_upload_bytes:
        raise FileTooLargeError(f"文件超过 {settings.max_upload_mb}MB 限制")

    kind, mime = detect_kind(filename, content[:16] if len(content) > 16 else content)
    # 文本类：额外做内容校验（用**完整内容**，避免截断多字节字符误判）
    if kind == "TEXT" and not is_text_content(content):
        raise UnsupportedMediaTypeError("文本文件解码失败，请确认是 UTF-8 文本")
    content_hash = hashlib.sha256(content).hexdigest()

    # E-6 同 subject 同内容去重
    existing = db.scalar(
        select(Document).where(
            Document.subject_id == subject.id,
            Document.content_hash == content_hash,
            Document.deleted_at.is_(None),
        )
    )
    if existing is not None:
        if not force:
            raise ConflictError(
                code="DUP_FILE",
                message="已存在相同文件",
                detail={"duplicate": True, "existingId": existing.id},
            )
        # 强制上传：旧记录软删（原件物理保留于 MinIO；行保留可审计追溯）
        existing.deleted_at = datetime.now(timezone.utc)
        audit_record(
            db,
            action="document.duplicate_superseded",
            resource_type="document",
            resource_id=existing.id,
            account_id=account.id,
            subject_id=subject.id,
            request=request,
        )

    doc_id = new_id()
    storage = get_storage_client()
    key = original_key(account.id, subject.id, doc_id)
    storage.put(key, content, mime)

    # R1 T101：来源元数据（用户填写优先；未填则留空待 AI/人工补充）
    span = None
    if occurred_at:
        span = _parse_user_span(occurred_at, time_precision)
    document = Document(
        id=doc_id,
        account_id=account.id,
        subject_id=subject.id,
        kind=kind,
        doc_type=None,  # 分类分流在 worker 内完成（AC-4.2）
        status="pending",
        processing_status=PENDING,
        lifecycle_status=LIFECYCLE_ACTIVE,
        content_status=None,
        record_type=record_type or None,
        occurred_start=span.start if span else None,
        occurred_end=span.end if span else None,
        time_precision=span.precision if span else "unknown",
        facility=facility or None,
        department=department or None,
        source_note=source_note or None,
        file_key=key,
        file_name=filename,
        mime_type=mime,
        size_bytes=size,
        content_hash=content_hash,
        page_count=None,
    )
    db.add(document)
    db.flush()

    job = ExtractionJob(id=new_id(), document_id=document.id, state="queued", attempt=0)
    db.add(job)
    db.flush()

    audit_record(
        db,
        action="document.upload",
        resource_type="document",
        resource_id=document.id,
        account_id=account.id,
        subject_id=subject.id,
        after={"fileName": filename, "sizeBytes": size, "kind": kind},
        request=request,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError(code="DUP_FILE", message="已存在相同文件",
                            detail={"duplicate": True})

    db.refresh(document)
    db.refresh(job)
    enqueue_extract(job.id)  # DB 为事实源；入队失败可由 worker 轮询补偿

    logger.info(
        "document.uploaded accountId=%s subjectId=%s docId=%s kind=%s bytes=%s",
        account.id, subject.id, document.id, kind, size,
    )
    return {"document": doc_out(document), "duplicate": bool(existing) if existing else None}


def list_documents(
    db: Session,
    account: Account,
    subject_id: Optional[str],
    status: Optional[str],
    doc_type: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    """分页列表（AC-2.2 按 Subject 隔离）。"""
    subject = require_subject(db, account, subject_id)
    q = select(Document).where(
        Document.subject_id == subject.id,
        Document.deleted_at.is_(None),
    )
    if status:
        # 兼容旧 status 过滤：映射到正交维度（归档行仍按旧语义返回 archived）
        if status == "archived":
            q = q.where(Document.lifecycle_status == LIFECYCLE_ARCHIVED)
        elif status in ("pending", "extracting", "awaiting_review", "confirmed",
                        "parsed_no_data", "failed"):
            legacy_to_processing = {
                "pending": "pending",
                "extracting": "processing",
                "awaiting_review": "awaiting_review",
                "failed": "failed",
            }
            processing = legacy_to_processing.get(status)
            if processing is not None:
                q = q.where(Document.processing_status == processing)
            else:  # confirmed / parsed_no_data → processed + content_status
                q = q.where(
                    Document.processing_status == PROCESSED,
                    Document.content_status == (
                        CONTENT_CONFIRMED if status == "confirmed" else CONTENT_NO_DATA
                    ),
                )
        else:
            q = q.where(Document.processing_status == status)
    if doc_type:
        q = q.where(Document.doc_type == doc_type)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    # R1 T101：按**发生时间**排序（无发生时间的记录排在最后），
    # 而不是按上传时间——长期档案里"什么时候发生的"才是主线。
    rows = (
        db.execute(
            q.order_by(
                Document.occurred_start.is_(None),      # 已知时间在前
                Document.occurred_start.desc(),
                Document.created_at.desc(),
                Document.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    from app.common.pagination import make_page

    return make_page([doc_out(d) for d in rows], page, page_size, int(total))


def select_document(
    db: Session,
    account: Account,
    doc_id: str,
    subject_id: Optional[str] = None,
) -> Document:
    """取文档（归属 + 生命周期校验）；不存在/越权/软删 → 404。

    供资产接口等复用，避免各处重复写归属判断。
    """
    return select_owned(db, account, Document, doc_id, subject_id)


def get_document_detail(
    db: Session,
    account: Account,
    doc_id: str,
    subject_id: Optional[str] = None,
) -> dict:
    """详情：{document, derivedRecord?, job?}；跨档案 → 404（K4）。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    dr = db.scalar(
        select(DerivedRecord)
        .where(DerivedRecord.document_id == document.id)
        .order_by(DerivedRecord.version.desc())
        .limit(1)
    )
    job = db.scalar(
        select(ExtractionJob)
        .where(ExtractionJob.document_id == document.id)
        .order_by(ExtractionJob.created_at.desc())
        .limit(1)
    )
    return {
        "document": doc_out(document),
        "derivedRecord": _dr_brief(dr) if dr else None,
        "job": _job_out(job) if job else None,
    }


def _dr_brief(dr: DerivedRecord) -> dict:
    return {
        "id": dr.id,
        "version": dr.version,
        "parser": dr.parser,
        "confirmedAt": dr.confirmed_at.isoformat() if dr.confirmed_at else None,
    }


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite 返回 naive datetime，统一按 UTC 处理，避免 aware/naive 相减报错。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _job_out(job: ExtractionJob) -> dict:
    """任务视图：含阶段/页进度/耗时/租约状态（前端展示“正在识别第 n 页”）。"""
    now = datetime.now(timezone.utc)
    started = _as_aware(job.started_at)
    elapsed = max(0, int((now - started).total_seconds())) if started else 0
    lease = _as_aware(job.lease_until)
    stale = bool(job.state == "running" and lease is not None and lease < now)
    return {
        "id": job.id,
        "documentId": job.document_id,
        "state": job.state,
        "attempt": job.attempt,
        "parser": job.parser,
        "error": job.error,
        "stage": job.stage,
        "progress": {
            "done": job.progress_done or 0,
            "total": job.progress_total or 0,
        },
        "elapsedSeconds": elapsed,
        "stale": stale,
        "startedAt": started.isoformat() if started else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
    }


def retry_document(
    db: Session,
    account: Account,
    doc_id: str,
    request=None,
    subject_id: Optional[str] = None,
) -> dict:
    """解析失败重试（E-1：最多 1 次；failed → pending）。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    if processing_of(document) != "failed":
        raise StateInvalidError("仅解析失败的文档可重试")

    job = db.scalar(
        select(ExtractionJob)
        .where(ExtractionJob.document_id == document.id)
        .order_by(ExtractionJob.created_at.desc())
        .limit(1)
    )
    max_attempts = settings.extract_max_attempts  # 2：含首次
    if job is not None and job.attempt >= max_attempts - 1:
        raise StateInvalidError("已达最大重试次数，请手动录入")

    set_processing_status(document, PENDING)
    document.failure_reason = None

    new_job = ExtractionJob(
        id=new_id(), document_id=document.id, state="queued",
        attempt=(job.attempt + 1) if job else 1,
    )
    db.add(new_job)
    audit_record(
        db,
        action="document.retry",
        resource_type="document",
        resource_id=document.id,
        account_id=account.id,
        subject_id=document.subject_id,
        request=request,
    )
    db.commit()
    db.refresh(new_job)
    enqueue_extract(new_job.id)
    return {"job": _job_out(new_job)}


def archive_document(
    db: Session,
    account: Account,
    doc_id: str,
    request=None,
    subject_id: Optional[str] = None,
) -> dict:
    """归档（B-3 替代删除；原件不物理删除）。

    归档只改**生命周期**，不改处理状态：已确认的文档归档后仍是"已确认"，
    可恢复；旧 `status` 仍投影为 archived 以兼容既有客户端。
    """
    document = select_owned(db, account, Document, doc_id, subject_id)
    if lifecycle_of(document) != LIFECYCLE_ARCHIVED:
        before = {
            "status": document.status,
            "processingStatus": processing_of(document),
            "lifecycleStatus": lifecycle_of(document),
        }
        set_lifecycle_status(document, LIFECYCLE_ARCHIVED)
        audit_record(
            db,
            action="document.archive",
            resource_type="document",
            resource_id=document.id,
            account_id=account.id,
            subject_id=document.subject_id,
            before=before,
            after={
                "status": document.status,
                "processingStatus": processing_of(document),
                "lifecycleStatus": lifecycle_of(document),
            },
            request=request,
        )
        db.commit()
        db.refresh(document)
    return doc_out(document)


def restore_document(
    db: Session,
    account: Account,
    doc_id: str,
    request=None,
    subject_id: Optional[str] = None,
) -> dict:
    """取消归档：恢复为 active，处理状态保持不变（不重抽、不改临床事实）。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    if lifecycle_of(document) != LIFECYCLE_ARCHIVED:
        raise StateInvalidError("该文档未归档，无需恢复")
    before = {
        "status": document.status,
        "processingStatus": processing_of(document),
        "lifecycleStatus": lifecycle_of(document),
    }
    set_lifecycle_status(document, LIFECYCLE_ACTIVE)
    audit_record(
        db,
        action="document.restore",
        resource_type="document",
        resource_id=document.id,
        account_id=account.id,
        subject_id=document.subject_id,
        before=before,
        after={
            "status": document.status,
            "processingStatus": processing_of(document),
            "lifecycleStatus": lifecycle_of(document),
        },
        request=request,
    )
    db.commit()
    db.refresh(document)
    return doc_out(document)


def content_url(
    db: Session,
    account: Account,
    doc_id: str,
    page: Optional[int] = None,
    subject_id: Optional[str] = None,
) -> dict:
    """原件签名 URL（AC-13.3 带过期；过期由 MinIO 返 403 → 前端刷新，E-9）。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    storage = get_storage_client()
    if page is not None:
        from app.db.models import DocumentPage

        row = db.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == document.id,
                DocumentPage.page_no == page,
            )
        )
        if row is None or not row.image_key:
            raise NotFoundError("页图不存在")
        key = row.image_key
    else:
        key = document.file_key
    url = storage.presign_get(key)
    from datetime import timedelta

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.sign_url_expire_seconds
    )
    return {"url": url, "expiresAt": expires_at.isoformat()}


def page_urls(
    db: Session,
    account: Account,
    doc_id: str,
    subject_id: Optional[str] = None,
) -> list:
    """分页页图 URL 列表（PDF 预览）。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    from app.db.models import DocumentPage

    rows = (
        db.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_no)
        )
        .scalars()
        .all()
    )
    storage = get_storage_client()
    from datetime import timedelta

    out = []
    for row in rows:
        if not row.image_key:
            continue
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.sign_url_expire_seconds
        )
        out.append(
            {
                "pageNo": row.page_no,
                "url": storage.presign_get(row.image_key),
                "expiresAt": expires_at.isoformat(),
            }
        )
    return out
