"""审计服务（F-14；B-5 不可绕过）：audit.record() 通用封装（who/when/what/前值后值）。

设计：record() 只向当前 Session add，不自行 commit——与业务变更同一事务提交，
保证"业务成功则审计必在"的原子性；调用方（router/service）负责 commit。
"""

from typing import Any, Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.ids import new_id
from app.db.models import AuditLog


def _client_meta(request: Optional[Request]) -> tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


def record(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    account_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    actor_type: str = "user",
    actor_id: Optional[str] = None,
    before: Optional[Any] = None,
    after: Optional[Any] = None,
    request: Optional[Request] = None,
    commit: bool = False,
) -> AuditLog:
    """写一条审计（前值/后值、request_id 注入，AC-14.1）。"""
    ip, ua = _client_meta(request)
    request_id = getattr(request.state, "request_id", None) if request else None
    if actor_id is None:
        actor_id = account_id
    log = AuditLog(
        id=new_id(),
        account_id=account_id,
        subject_id=subject_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_value=before,
        after_value=after,
        request_id=request_id,
        ip=ip,
        user_agent=ua,
    )
    db.add(log)
    if commit:
        db.commit()
        db.refresh(log)
    return log


def list_logs(
    db: Session,
    account_id: str,
    *,
    subject_id: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AuditLog], int]:
    """本人操作审计查询（AC-14.4）：分页 + 筛选。"""
    q = select(AuditLog).where(AuditLog.account_id == account_id)
    if subject_id:
        q = q.where(AuditLog.subject_id == subject_id)
    if action:
        q = q.where(AuditLog.action == action)
    if date_from:
        q = q.where(AuditLog.created_at >= date_from)
    if date_to:
        q = q.where(AuditLog.created_at <= date_to)

    from sqlalchemy import func

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        db.execute(
            q.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)
