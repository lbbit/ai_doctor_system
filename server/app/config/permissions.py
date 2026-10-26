"""配置写权限判定（§6.9）。

背景：`system_settings` 是**全局**配置（LLM 模型、检索源、限额），
任意登录账户可写会造成越权：一个用户可以改掉全站的抽取模型。

策略（``CONFIG_WRITE_POLICY``）：
- ``admin_only``（默认）：需要 ``Account.role == "admin"``；
  首个注册账户自动成为 admin（见 ``app/auth/service.py``）。
- ``first_account``：仅最早创建的账户可写（无 role 体系时的过渡策略）。
- ``any``：任意登录账户可写，**仅限本地单用户部署**。

邮箱白名单 ``CONFIG_ADMIN_EMAILS`` 优先级最高，便于环境变量紧急授权，
不需要改库即可临时放开（也便于运维在没有 admin 账户时自锁恢复）。

判定完全由代码完成，不读取任何 DB 配置项（避免"改配置解锁配置"的循环依赖）。
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.errors import ForbiddenError
from app.config.settings import settings
from app.db.models import Account

logger = logging.getLogger("app.config.permissions")

ROLE_ADMIN = "admin"
ROLE_USER = "user"

POLICY_ADMIN_ONLY = "admin_only"
POLICY_FIRST_ACCOUNT = "first_account"
POLICY_ANY = "any"

VALID_POLICIES = (POLICY_ADMIN_ONLY, POLICY_FIRST_ACCOUNT, POLICY_ANY)


def is_email_whitelisted(email: Optional[str]) -> bool:
    """邮箱白名单命中（大小写不敏感）。"""
    if not email:
        return False
    return email.strip().lower() in settings.config_admin_email_list


def is_admin(db: Session, account: Account) -> bool:
    """是否具备配置写权限（纯判定，不抛异常，便于复用与展示）。"""
    if account is None or account.status != "active":
        return False
    if is_email_whitelisted(account.email):
        return True

    policy = (settings.config_write_policy or POLICY_ADMIN_ONLY).strip().lower()
    if policy not in VALID_POLICIES:
        # 未知取值按最严格处理，避免配置写错导致越权
        logger.warning("config.policy.unknown value=%s fallback=admin_only", policy)
        policy = POLICY_ADMIN_ONLY

    if policy == POLICY_ANY:
        return True
    if policy == POLICY_ADMIN_ONLY:
        return getattr(account, "role", ROLE_USER) == ROLE_ADMIN
    # first_account：仅最早创建的账户
    first_id = db.scalar(select(Account.id).order_by(Account.created_at.asc()).limit(1))
    return first_id == account.id


def require_config_admin(db: Session, account: Account) -> Account:
    """配置写入前置校验；无权限 → 403 FORBIDDEN。"""
    if not is_admin(db, account):
        logger.warning(
            "config.write_denied account=%s policy=%s",
            account.id if account else None,
            settings.config_write_policy,
        )
        raise ForbiddenError("无权修改系统配置，请联系管理员")
    return account


def assign_role_for_new_account(db: Session) -> str:
    """新注册账户的角色：库里还没有任何账户时给 admin，否则 user。

    这样单用户/自托管部署开箱即可配置；多用户场景下只有首个账户是管理员，
    其余账户必须由管理员显式提升（当前尚无提升接口，见 PLAN §17 待办）。
    """
    existing = db.scalar(select(func.count()).select_from(Account)) or 0
    return ROLE_ADMIN if existing == 0 else ROLE_USER
