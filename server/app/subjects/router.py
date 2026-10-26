"""Subject 路由（F-02/F-03）：/api/subjects/*（§4.2 5-9、43）。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.deps import get_current_account
from app.common.responses import ok_response
from app.db.models import Account
from app.db.session import get_db
from app.subjects import service
from app.subjects.schemas import LastSelectedRequest, SubjectCreate, SubjectUpdate

router = APIRouter(prefix="/api/subjects", tags=["subjects"])


@router.get("")
def list_subjects(
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """成员列表。"""
    items = [service.subject_out(s) for s in service.list_subjects(db, account)]
    return ok_response(items, request)


@router.post("")
def create_subject(
    data: SubjectCreate,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """新建 Subject（AC-2.1）。"""
    subject = service.create_subject(db, account, data, request)
    return ok_response(service.subject_out(subject), request)


@router.put("/last-selected")
def last_selected(
    data: LastSelectedRequest,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """记忆上次选档（AC-2.3）。"""
    subject_id = service.set_last_selected(db, account, data.subjectId)
    return ok_response({"subjectId": subject_id}, request)


@router.patch("/{subject_id}")
def update_subject(
    subject_id: str,
    data: SubjectUpdate,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """编辑 Subject。"""
    subject = service.update_subject(db, account, subject_id, data, request)
    return ok_response(service.subject_out(subject), request)


@router.delete("/{subject_id}")
def delete_subject(
    subject_id: str,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """删除（级联：会话归档、文件软删、审计保留）。"""
    result = service.delete_subject(db, account, subject_id, request)
    return ok_response(result, request)


@router.get("/{subject_id}/stats")
def subject_stats(
    subject_id: str,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """档案概览。"""
    return ok_response(service.subject_stats(db, account, subject_id), request)
