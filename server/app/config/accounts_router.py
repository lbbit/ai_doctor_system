"""账户与权限路由（§6.9 / PLAN §17）：/api/accounts。

职责：
- ``GET``：管理员查看账户列表与角色（不含密码/密钥）。
- ``PATCH /{id}/role``：管理员提升/降级角色，需二次确认语义 + 审计。
- ``GET /config-notices``：最近配置变更通知。

安全约束：
- 只有 admin 可读账户列表与改角色（非 admin 一律 403）。
- **不能把自己降级**：避免唯一管理员自锁。
- **不能降级最后一个 admin**：保证系统始终有人可管理配置。
- 角色变更必写审计 ``account.role_change``（含前后值）。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import get_current_account
from app.common.errors import ForbiddenError, NotFoundError, ValidationError_
from app.common.responses import ok_response
from app.config.notify import recent_notices
from app.config.permissions import ROLE_ADMIN, ROLE_USER, is_admin, require_config_admin
from app.db.models import Account
from app.db.session import get_db

logger = logging.getLogger("app.accounts.router")

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

VALID_ROLES = (ROLE_ADMIN, ROLE_USER)


class RolePayload(BaseModel):
    role: str = Field(min_length=1, max_length=20)


def _account_row(a: Account) -> dict:
    return {
        "id": a.id,
        "email": a.email,
        "displayName": a.display_name,
        "role": getattr(a, "role", ROLE_USER),
        "status": a.status,
        "createdAt": a.created_at.isoformat() if a.created_at else None,
    }


def _admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Account)
        .where(Account.role == ROLE_ADMIN, Account.status == "active")
    ) or 0


@router.get("")
def list_accounts(
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """账户列表（仅管理员可见）。"""
    if not is_admin(db, account):
        raise ForbiddenError("无权查看账户列表")
    rows = db.execute(select(Account).order_by(Account.created_at.asc())).scalars().all()
    return ok_response(
        {
            "items": [_account_row(a) for a in rows],
            "adminCount": _admin_count(db),
            "canManage": True,
        },
        request,
    )


@router.patch("/{account_id}/role")
def change_role(
    account_id: str,
    payload: RolePayload,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """修改账户角色（含自锁与最后管理员保护）。"""
    require_config_admin(db, account)

    role = payload.role.strip().lower()
    if role not in VALID_ROLES:
        raise ValidationError_(f"角色只能是 {', '.join(VALID_ROLES)} 之一")

    target = db.get(Account, account_id)
    if target is None:
        raise NotFoundError("账户不存在")

    before_role = getattr(target, "role", ROLE_USER)
    if before_role == role:
        return ok_response({"account": _account_row(target), "changed": False}, request)

    # 保护 1：不能把自己降级（避免唯一管理员自锁；降级他人是允许的）
    if target.id == account.id and role != ROLE_ADMIN:
        raise ValidationError_("不能降低自己的权限，请让其他管理员操作")

    # 保护 2：不能降级最后一个管理员
    if before_role == ROLE_ADMIN and role != ROLE_ADMIN and _admin_count(db) <= 1:
        raise ValidationError_("系统至少需要保留一名管理员")

    target.role = role
    audit_record(
        db,
        action="account.role_change",
        resource_type="account",
        resource_id=target.id,
        account_id=account.id,
        subject_id=None,
        before={"role": before_role},
        after={"role": role, "targetId": target.id, "actorEmail": account.email},
        request=request,
    )
    db.commit()
    logger.info(
        "account.role_change target=%s from=%s to=%s by=%s",
        target.id,
        before_role,
        role,
        account.id,
    )
    # 邮件通知：角色提权属高影响操作，异步发送且失败不影响结果
    from app.config.mail import notify_by_mail

    notify_by_mail(
        action="account.role_change",
        keys=[f"account.role:{before_role}->{role}"],
        actor_email=account.email,
        high_impact=True,
        db=db,
    )
    return ok_response({"account": _account_row(target), "changed": True}, request)


@router.get("/config-notices")
def config_notices(
    request: Request,
    sinceHours: int = 72,
    limit: int = 20,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """最近配置变更通知。

    管理员看到全部；普通用户只看到自己产生的变更
    （不向他人的操作历史暴露信息）。
    """
    admin = is_admin(db, account)
    notices = recent_notices(
        db,
        since_hours=max(1, min(720, sinceHours)),
        limit=max(1, min(100, limit)),
        account_id=None if admin else account.id,
    )
    return ok_response(
        {
            "items": [n.to_dict() for n in notices],
            "scope": "all" if admin else "mine",
            "isAdmin": admin,
        },
        request,
    )
