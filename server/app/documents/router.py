"""文档路由（F-04/F-13）：/api/documents/*（§4.2 10-16）。"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_account, resolve_subject_id
from app.common.pagination import PageParams, page_params
from app.common.responses import api_ok, ok_response
from app.db.models import Account
from app.db.session import get_db
from app.documents import service
from app.documents.schemas import DocumentOut  # noqa: F401  (契约引用)
from app.preprocessing.schemas import ConfirmRequest

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("")
async def upload_document(
    request: Request,
    file: UploadFile = File(..., description="JPG/PNG/PDF ≤20MB"),
    subjectId: Optional[str] = Form(default=None),
    force: bool = Form(default=False),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """上传（multipart：file + subjectId + force）；重复且未 force → 409 DUP_FILE。"""
    content = await file.read()
    result = service.upload_document(
        db,
        account,
        subjectId,
        file.filename or "unnamed",
        content,
        force=force,
        request=request,
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
    """归档（B-3，替代删除）。"""
    subject_id = resolve_subject_id(db, account, subjectId)
    return ok_response(
        service.archive_document(db, account, doc_id, request, subject_id), request
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


# ---- 抽取与确认 F-05/F-06（§4.2 17-19；实现于 preprocessing/review.py）----


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
