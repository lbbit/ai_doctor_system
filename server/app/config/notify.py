"""配置变更通知（§6.9 / PLAN §17）。

目标：全局配置被修改属于高影响操作，除审计外还要让管理员看到。
实现原则：
- **不引入外部通道依赖**：通知落库 + 通过既有审计/设置接口暴露，
  避免为了一封邮件引入 SMTP 配置与失败重试链路。
- **敏感值永不出现在通知里**：只记 key 与变更者，不记新旧值。
- **失败不阻断写操作**：通知写入异常只记日志，不影响配置保存结果。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.ids import new_id
from app.db.models import Account
from app.db.models.audit import AuditLog

logger = logging.getLogger("app.config.notify")

# 这些前缀的配置变更视为高影响（LLM 模型/网关、Agent、检索）
HIGH_IMPACT_PREFIXES = ("llm.", "agent.")
# 这些前缀的变更只影响展示，不必通知
LOW_IMPACT_PREFIXES = ("chat.", "upload.", "privacy.")

ACTION_CONFIG_UPDATE = "config.update"
ACTION_CONFIG_RESET = "config.reset"
ACTION_ROLE_CHANGE = "account.role_change"


@dataclass
class ConfigNotice:
    id: str
    action: str
    actor_email: str
    keys: List[str]
    high_impact: bool
    created_at: Optional[datetime]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "actorEmail": self.actor_email,
            "keys": self.keys,
            "highImpact": self.high_impact,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
        }


def is_high_impact(keys: List[str]) -> bool:
    """是否属于高影响变更（决定是否需要在界面突出提示）。"""
    return any(k.startswith(HIGH_IMPACT_PREFIXES) for k in keys)


def classify_keys(keys: List[str]) -> List[str]:
    """过滤掉纯展示类配置，返回需要通知的 key 列表。"""
    return [k for k in keys if not k.startswith(LOW_IMPACT_PREFIXES)]


def recent_notices(
    db: Session,
    *,
    since_hours: int = 72,
    limit: int = 20,
    account_id: Optional[str] = None,
) -> List[ConfigNotice]:
    """最近配置变更通知（供管理员在设置页查看）。

    ``account_id`` 非空时只返回该账户自己产生的变更（普通用户视角）。
    """
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, since_hours))
    q = select(AuditLog).where(
        AuditLog.action.in_((ACTION_CONFIG_UPDATE, ACTION_CONFIG_RESET, ACTION_ROLE_CHANGE)),
        AuditLog.created_at >= since,
    )
    if account_id:
        q = q.where(AuditLog.account_id == account_id)
    rows = db.execute(q.order_by(AuditLog.created_at.desc()).limit(limit)).scalars().all()

    notices: List[ConfigNotice] = []
    for row in rows:
        after = row.after_value or {}
        if not isinstance(after, dict):
            after = {}
        keys = (
            after.get("changed")
            or after.get("reset")
            or after.get("keys")
            or []
        )
        if isinstance(keys, list) and keys and isinstance(keys[0], dict):
            keys = [str(item.get("key", "")) for item in keys]
        keys = [str(k) for k in keys if k]
        notices.append(
            ConfigNotice(
                id=row.id,
                action=row.action,
                actor_email=str(after.get("actorEmail") or ""),
                keys=keys,
                high_impact=is_high_impact(keys),
                created_at=row.created_at,
            )
        )
    return notices


def notify_config_change(
    db: Session,
    *,
    account_id: Optional[str],
    account_email: Optional[str],
    action: str,
    keys: List[str],
    request_hash: Optional[str] = None,
) -> Optional[AuditLog]:
    """记录一条配置变更通知（本质是带 ``notify`` 语义的审计行）。

    设计说明：不新建表，复用 ``audit_logs``。原因是通知与审计同源同生命周期，
    分表会带来双写不一致与清理策略分叉；用 ``action`` 区分即可查询。
    写入失败只记日志，不抛异常（不得阻断配置保存）。
    """
    try:
        relevant = classify_keys(keys)
        log = AuditLog(
            id=new_id(),
            account_id=account_id,
            subject_id=None,
            actor_type="user",
            actor_id=account_id,
            action=action,
            resource_type="system_setting",
            resource_id=None,
            before_value=None,
            # 只记 key 与变更者，绝不记密钥值
            after_value={
                "keys": relevant,
                "actorEmail": account_email,
                "highImpact": is_high_impact(relevant),
                "requestHash": request_hash,
            },
            request_id=None,
            ip=None,
            user_agent=None,
        )
        db.add(log)
        db.flush()
        return log
    except Exception:  # noqa: BLE001 - 通知失败不得影响主流程
        logger.exception("config.notify_failed action=%s", action)
        return None


def admin_recipients(db: Session) -> List[Account]:
    """管理员列表（用于将来接入邮件/IM 通道；当前仅用于展示归属）。"""
    return list(
        db.execute(
            select(Account).where(Account.role == "admin", Account.status == "active")
        ).scalars()
    )
