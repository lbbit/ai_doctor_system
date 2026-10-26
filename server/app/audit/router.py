"""审计/设置路由（F-14）：/api/audit/*、/api/settings（§4.2 40-42）。"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.audit.service import list_logs
from app.auth.deps import get_current_account, require_subject
from app.common.errors import ValidationError_
from app.common.pagination import PageParams, page_params
from app.common.responses import ok_response
from app.common.schemas import CamelModel
from app.config.settings import settings
from app.db.models import Account
from app.db.session import get_db

router = APIRouter(tags=["settings", "audit"])


@router.get("/api/audit/logs")
def audit_logs(
    request: Request,
    subjectId: Optional[str] = None,
    action: Optional[str] = None,
    dateFrom: Optional[str] = None,
    dateTo: Optional[str] = None,
    paging: PageParams = Depends(page_params),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """本人操作审计（AC-14.4；分页/筛选）。"""
    if subjectId:
        require_subject(db, account, subjectId)  # 越权 → 404
    rows, total = list_logs(
        db,
        account.id,
        subject_id=subjectId,
        action=action,
        date_from=dateFrom,
        date_to=dateTo,
        page=paging.page,
        page_size=paging.page_size,
    )
    from app.common.pagination import make_page

    items = [
        {
            "id": r.id,
            "subjectId": r.subject_id,
            "actorType": r.actor_type,
            "action": r.action,
            "resourceType": r.resource_type,
            "resourceId": r.resource_id,
            "before": r.before_value,
            "after": r.after_value,
            "requestId": r.request_id,
            "ip": r.ip,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return ok_response(make_page(items, paging.page, paging.page_size, total), request)


@router.get("/api/settings")
def get_settings(
    request: Request,
    account: Account = Depends(get_current_account),  # noqa: ARG001
):
    """脱敏开关、本地模型开关、免责版本、置信度阈值（只读）。"""
    return ok_response(
        {
            "piiMaskingEnabled": settings.pii_masking_enabled,
            "localModelMode": settings.local_model_mode,
            "disclaimerVersion": settings.disclaimer_version,
            "confidenceThreshold": settings.hitl_confidence_threshold,  # 只读（P0 全强制确认）
            "autoCommitEnabled": settings.auto_commit_enabled,  # P1 留缝，恒 false
            "maxUploadMb": settings.max_upload_mb,
        },
        request,
    )


class SettingsPatch(CamelModel):
    piiMaskingEnabled: Optional[bool] = None


@router.patch("/api/settings")
def patch_settings(
    data: SettingsPatch,
    request: Request,
    account: Account = Depends(get_current_account),  # noqa: ARG001
    db: Session = Depends(get_db),  # noqa: ARG001
):
    """修改设置（Q-2 裁决：P0 云端模式脱敏只读强制开，仅允许开启不允许关闭）。"""
    if data.piiMaskingEnabled is False and not settings.local_model_mode:
        raise ValidationError_(
            "云端模式下脱敏开关只读（强制开启）；如需整体不出域请部署时设置 LOCAL_MODEL_MODE=true"
        )
    if data.piiMaskingEnabled is not None:
        settings.pii_masking_enabled = data.piiMaskingEnabled

    from app.audit.service import record as audit_record

    audit_record(
        db,
        action="settings.update",
        resource_type="settings",
        resource_id="runtime",
        account_id=account.id,
        after={"piiMaskingEnabled": settings.pii_masking_enabled},
        request=request,
    )
    db.commit()
    return ok_response(
        {
            "piiMaskingEnabled": settings.pii_masking_enabled,
            "localModelMode": settings.local_model_mode,
            "disclaimerVersion": settings.disclaimer_version,
            "confidenceThreshold": settings.hitl_confidence_threshold,
            "autoCommitEnabled": settings.auto_commit_enabled,
            "maxUploadMb": settings.max_upload_mb,
        },
        request,
    )
