"""观测/趋势/字典路由（F-08、E-3）：/api/observations/*、/api/dictionary/*（§4.2 20-21、24-26）。"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.deps import get_current_account
from app.common.pagination import PageParams, page_params
from app.common.responses import ok_response
from app.common.schemas import CamelModel
from app.db.models import Account
from app.db.session import get_db
from app.observations import dictionary as dict_svc
from app.observations import trends

router = APIRouter(tags=["observations"])


class ResolveRequest(CamelModel):
    code: Optional[str] = None
    newCode: Optional[str] = None


@router.get("/api/observations/trend")
def trend(
    request: Request,
    subjectId: Optional[str] = None,
    code: Optional[str] = None,
    dateFrom: Optional[str] = None,
    dateTo: Optional[str] = None,
    limit: int = 20,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """趋势序列（AC-8.1 SQL 确定性；<2 点 insufficient=true）。"""
    data = trends.trend_series(db, account, subjectId, code or "", dateFrom, dateTo, limit)
    return ok_response(data, request)


@router.get("/api/observations")
def list_observations(
    request: Request,
    subjectId: Optional[str] = None,
    code: Optional[str] = None,
    paging: PageParams = Depends(page_params),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """观测列表/明细（分页）。"""
    data = trends.list_observations(
        db, account, subjectId, code, paging.page, paging.page_size
    )
    return ok_response(data, request)


@router.get("/api/dictionary/trend-options")
def trend_options(
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """TOP20 趋势指标选项。"""
    return ok_response(dict_svc.trend_options(db, account), request)


@router.get("/api/dictionary/misses")
def list_misses(
    request: Request,
    resolved: Optional[bool] = None,
    paging: PageParams = Depends(page_params),
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """字典未命中队列（E-3）。"""
    data = dict_svc.list_misses(db, account, resolved, paging.page, paging.page_size)
    return ok_response(data, request)


@router.post("/api/dictionary/misses/{miss_id}/resolve")
def resolve_miss(
    miss_id: str,
    data: ResolveRequest,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """反馈归并到字典（code 或 newCode）。"""
    result = dict_svc.resolve_miss(
        db, account, miss_id, data.code, data.newCode, request
    )
    return ok_response(result, request)
