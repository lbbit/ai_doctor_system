"""时间线路由（F-07）：/api/timeline/*（§4.2 22-23）。"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.deps import get_current_account
from app.common.pagination import PageParams, page_params
from app.common.responses import ok_response
from app.db.models import Account
from app.db.session import get_db
from app.timeline import service

router = APIRouter(prefix="/api/timeline", tags=["timeline"])


@router.get("/events")
def list_events(
    request: Request,
    subjectId: Optional[str] = None,
    types: Optional[str] = None,
    dateFrom: Optional[str] = None,
    dateTo: Optional[str] = None,
    paging: PageParams = Depends(page_params),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """事件流（按 occurredAt 倒序，年/月分组）。"""
    data = service.list_events(
        db, account, subjectId, types, dateFrom, dateTo,
        paging.page, paging.page_size,
    )
    return ok_response(data, request)


@router.get("/summary")
def timeline_summary(
    request: Request,
    subjectId: Optional[str] = None,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """时间线摘要（角标、类型计数；空档案返回 isEmpty=true）。"""
    return ok_response(service.timeline_summary(db, account, subjectId), request)
