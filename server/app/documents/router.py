"""文档路由（F-04/F-13）：/api/documents/*（§4.2 10-16）。"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_account, resolve_subject_id
from app.common.errors import AppError
from app.common.pagination import PageParams, page_params
from app.common.responses import api_ok, ok_response
from app.db.models import Account
from app.db.session import get_db
from app.documents import service
from app.documents.schemas import DocumentOut  # noqa: F401  (契约引用)
from app.preprocessing.schemas import ConfirmRequest

logger = logging.getLogger("app.documents.router")

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("")
async def upload_document(
    request: Request,
    file: UploadFile = File(..., description="JPG/PNG/PDF ≤20MB"),
    subjectId: Optional[str] = Form(default=None),
    force: bool = Form(default=False),
    occurredAt: Optional[str] = Form(default=None, description="用户填写的发生日期"),
    timePrecision: Optional[str] = Form(default=None),
    recordType: Optional[str] = Form(default=None),
    facility: Optional[str] = Form(default=None),
    department: Optional[str] = Form(default=None),
    sourceNote: Optional[str] = Form(default=None),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """上传（multipart：file + subjectId + force）；重复且未 force → 409 DUP_FILE。

    R1 T101：可携带用户填写的来源元数据（发生日期、记录类型、机构、科室），
    这些值优先于 AI 推断，重新抽取不会覆盖。
    """
    content = await file.read()
    result = service.upload_document(
        db,
        account,
        subjectId,
        file.filename or "unnamed",
        content,
        force=force,
        request=request,
        occurred_at=occurredAt,
        time_precision=timePrecision,
        record_type=recordType,
        facility=facility,
        department=department,
        source_note=sourceNote,
    )
    # §5.1：201 {document}
    return JSONResponse(status_code=201, content=api_ok(result, request))


@router.get("")
def list_documents(
    request: Request,
    subjectId: Optional[str] = None,
    status: Optional[str] = None,
    docType: Optional[str] = None,
    paging: PageParams = Depends(page_params),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """列表（筛选 status/type，分页）。"""
    data = service.list_documents(
        db, account, subjectId, status, docType, paging.page, paging.page_size
    )
    return ok_response(data, request)


@router.post("/batch")
async def upload_documents_batch(
    request: Request,
    files: list[UploadFile] = File(..., description="多个文件（JPG/PNG/PDF/TXT/MD）"),
    subjectId: Optional[str] = Form(default=None),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """批量上传（R1 T105）。

    逐项返回结果：单个文件失败（格式/大小/重复）不影响其他文件，
    已成功的原件**不回滚**（用户不该因为一个坏文件丢掉其余资料）。
    所有文件必须属于同一 Subject（写操作强制选档）——缺 Subject 时
    **整体 422**（不是逐项报错），否则用户会看到一堆无意义的失败。
    """
    # 先校验档案：缺 Subject 直接 422，不进入逐项处理
    from app.auth.deps import require_subject

    require_subject(db, account, subjectId)

    items: list[dict] = []
    for upload in files:
        content = await upload.read()
        filename = upload.filename or "unnamed"
        try:
            result = service.upload_document(
                db,
                account,
                subjectId,
                filename,
                content,
                request=request,
            )
            items.append({"ok": True, "fileName": filename, **result})
        except AppError as exc:
            db.rollback()
            items.append({
                "ok": False,
                "fileName": filename,
                "code": exc.code,
                "message": exc.message,
            })
        except Exception as exc:  # noqa: BLE001 - 单项失败不得中断整批
            db.rollback()
            logger.exception("documents.batch_item_failed file=%s", filename)
            items.append({
                "ok": False,
                "fileName": filename,
                "code": "INTERNAL",
                "message": f"{type(exc).__name__}",
            })
    succeeded = sum(1 for i in items if i["ok"])
    return JSONResponse(
        status_code=201,
        content=api_ok(
            {"items": items, "total": len(items), "succeeded": succeeded,
             "failed": len(items) - succeeded},
            request,
        ),
    )


@router.get("/{doc_id}")
def get_document(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """详情（含草稿、任务）；跨档案访问 → 404（K4 零串档）。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(service.get_document_detail(db, account, doc_id, subject_id), request)


@router.post("/{doc_id}/retry")
def retry_document(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """解析失败重试（E-1，1 次）。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(
        service.retry_document(db, account, doc_id, request, subject_id), request
    )


@router.post("/{doc_id}/archive")
def archive_document(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """归档（B-3 替代删除；只改生命周期，不改处理状态）。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(
        service.archive_document(db, account, doc_id, request, subject_id), request
    )


@router.post("/{doc_id}/restore")
def restore_document(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """取消归档（恢复为 active；处理状态与已确认事实保持不变）。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(
        service.restore_document(db, account, doc_id, request, subject_id), request
    )


@router.get("/{doc_id}/content")
def document_content(
    doc_id: str,
    request: Request,
    page: Optional[int] = None,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """原件签名 URL（带过期时间）；跨档案 → 404。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(service.content_url(db, account, doc_id, page, subject_id), request)


@router.get("/{doc_id}/pages")
def document_pages(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """分页页图 URL 列表（PDF 预览）。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(service.page_urls(db, account, doc_id, subject_id), request)


# ---- 资产（R1 T102）：原件与派生副本 ----


@router.get("/{doc_id}/assets")
def list_document_assets(
    doc_id: str,
    request: Request,
    role: Optional[str] = None,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """列出文档的派生资产（版本、生成器、是否过期）。跨档 → 404。"""
    from app.documents import assets as asset_service

    subject_id = resolve_subject_id(db, account, subjectId)
    document = service.select_document(db, account, doc_id, subject_id)
    rows = asset_service.list_assets(db, document, role=role)
    return ok_response(
        {
            "items": [
                asset_service.asset_out(
                    a, stale=asset_service.is_stale(a, document)
                )
                for a in rows
            ]
        },
        request,
    )


@router.get("/{doc_id}/assets/{role}")
def get_document_asset(
    doc_id: str,
    role: str,
    request: Request,
    version: Optional[int] = None,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """取资产内容（Markdown 副本/文本层等）；跨档或软删 → 404。

    返回签名 URL（含时效）而非直接吐字节，避免大文件走 API 通道。
    """
    from app.documents import assets as asset_service

    subject_id = resolve_subject_id(db, account, subjectId)
    document = service.select_document(db, account, doc_id, subject_id)
    asset = asset_service.get_asset(db, document, role, version=version)
    storage = service.get_storage_client()
    from datetime import timedelta

    from app.config.settings import settings

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.sign_url_expire_seconds
    )
    return ok_response(
        {
            **asset_service.asset_out(
                asset, stale=asset_service.is_stale(asset, document)
            ),
            "url": storage.presign_get(asset.object_key),
            "expiresAt": expires_at.isoformat(),
        },
        request,
    )


# ---- 抽取与确认 F-05/F-06（§4.2 17-19；实现于 preprocessing/review.py）----


@router.get("/{doc_id}/markdown")
def document_markdown(
    doc_id: str,
    request: Request,
    version: Optional[int] = None,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """Markdown 副本正文（R1 T107 四区之一）；跨档/软删 → 404。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(
        service.document_markdown(db, account, doc_id, subject_id, version=version),
        request,
    )


@router.get("/{doc_id}/records")
def document_records(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """本文档已确认的结构化事实（R1 T107 四区之一）；跨档/软删 → 404。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(
        service.document_records(db, account, doc_id, subject_id), request
    )


@router.get("/{doc_id}/versions")
def document_versions(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """处理与版本历史（R1 T107 四区之一）；跨档/软删 → 404。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(
        service.document_versions(db, account, doc_id, subject_id), request
    )


@router.get("/{doc_id}/derived-record")
def get_derived_record(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """取草稿（确认卡片数据）；跨档案 → 404。"""
    from app.auth.deps import resolve_subject_id
    from app.preprocessing.review import get_derived_record as _get

    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(_get(db, account, doc_id, subject_id), request)


@router.post("/{doc_id}/derived-record/confirm")
def confirm_derived_record(
    doc_id: str,
    data: ConfirmRequest,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """HITL 确认入库（AC-6.1~6.4；并发冲突 409 REVIEW_CONFLICT）。"""
    from app.auth.deps import resolve_subject_id
    from app.preprocessing.review import confirm_derived_record as _confirm

    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(_confirm(db, account, doc_id, data, request, subject_id), request)


@router.post("/{doc_id}/derived-record/reextract")
def reextract_derived_record(
    doc_id: str,
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """重新抽取（异步入队；进度/超时处理与首次上传一致）。"""
    from app.auth.deps import resolve_subject_id
    from app.preprocessing.review import reextract as _reextract

    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(_reextract(db, account, doc_id, request, subject_id), request)
