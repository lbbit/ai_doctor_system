"""文档服务：上传、hash 去重(409)、分类分流准备、创建 Document+Job、入队、
签名 URL、重试、归档（F-04/F-13；E-1/E-6/E-9/E-10、B-3、状态机）。"""

import hashlib
import logging
from datetime import datetime, timezone
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
from app.documents.storage import get_storage_client, original_key
from app.preprocessing.queue import enqueue_extract

logger = logging.getLogger("app.documents")

# E-10 白名单：JPG/PNG/PDF（AC-4.1）
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".pdf"}
_EXT_KIND = {".jpg": "IMAGE", ".jpeg": "IMAGE", ".png": "IMAGE", ".pdf": "PDF"}
_EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}

# Document 状态机合法边（PRD §9.2 / §3.3）；非法转移 → STATE_INVALID
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
    """按魔数 + 扩展名判定 kind/mime（离线可用，不依赖 libmagic）。"""
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_EXT:
        raise UnsupportedMediaTypeError(f"仅支持 JPG/PNG/PDF（收到 {ext or '未知类型'}）")
    if head.startswith(b"%PDF"):
        return "PDF", "application/pdf"
    if head.startswith(b"\xff\xd8"):
        return "IMAGE", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "IMAGE", "image/png"
    # 头部不匹配魔数 → 仍按扩展名（内容可疑由解析链判失败）
    return _EXT_KIND[ext], _EXT_MIME[ext]


def doc_out(d: Document) -> dict:
    return {
        "id": d.id,
        "subjectId": d.subject_id,
        "kind": d.kind,
        "docType": d.doc_type,
        "status": d.status,
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


def upload_document(
    db: Session,
    account: Account,
    subject_id: Optional[str],
    filename: str,
    content: bytes,
    force: bool = False,
    request=None,
) -> dict:
    """上传：B-1 选档 → E-10 校验 → hash 去重(E-6) → 存 MinIO →
    INSERT documents(pending) + extraction_jobs(queued) → 入队（§5.1 时序）。"""
    subject = require_subject(db, account, subject_id)  # 缺失 → 422

    size = len(content)
    if size == 0:
        raise UnsupportedMediaTypeError("空文件")
    if size > settings.max_upload_bytes:
        raise FileTooLargeError(f"文件超过 {settings.max_upload_mb}MB 限制")

    kind, mime = detect_kind(filename, content[:16])
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

    document = Document(
        id=doc_id,
        account_id=account.id,
        subject_id=subject.id,
        kind=kind,
        doc_type=None,  # 分类分流在 worker 内完成（AC-4.2）
        status="pending",
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
        q = q.where(Document.status == status)
    if doc_type:
        q = q.where(Document.doc_type == doc_type)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        db.execute(
            q.order_by(Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    from app.common.pagination import make_page

    return make_page([doc_out(d) for d in rows], page, page_size, int(total))


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
    if document.status != "failed":
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

    assert_transition(document.status, "pending")
    document.status = "pending"
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
    """归档（B-3 替代删除；原件不物理删除）。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    if document.status != "archived":
        assert_transition(document.status, "archived")
        before = {"status": document.status}
        document.status = "archived"
        document.archived_at = datetime.now(timezone.utc)
        audit_record(
            db,
            action="document.archive",
            resource_type="document",
            resource_id=document.id,
            account_id=account.id,
            subject_id=document.subject_id,
            before=before,
            after={"status": "archived"},
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
