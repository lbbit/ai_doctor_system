"""配置变更邮件通知（§6.9 / PLAN §17）。

设计约束（AGENTS.md）：
1. **密钥只来自 env/Secret**：`SMTP_PASSWORD` 永不入库、不入日志、不入邮件正文。
2. **失败可降级且可观测**：邮件失败只记日志 + 在审计中留痕，绝不阻断配置保存。
3. **可离线测试**：`_send_mail` 抽成可替换函数，测试用 fake 覆盖成功/失败/超时。
4. **异步不阻塞**：使用后台线程发送，PATCH 请求不等邮件完成（邮件服务器常秒级）。

正文只包含：动作类型、变更 key 列表、操作者邮箱、时间、高影响标记。
绝不包含配置值（可能含敏感信息）。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Callable, List, Optional

from app.config.settings import settings

logger = logging.getLogger("app.config.mail")

# 允许测试替换（monkeypatch 此名字即可离线验证）
_send_mail_impl: Optional[Callable[[EmailMessage, List[str]], None]] = None


@dataclass
class ConfigChangeMail:
    """邮件内容数据（与传输层解耦，便于单测）。"""

    action: str
    keys: List[str]
    actor_email: str
    high_impact: bool
    occurred_at: datetime

    def subject(self) -> str:
        prefix = "[AI私人医生][高影响]" if self.high_impact else "[AI私人医生]"
        return f"{prefix}系统配置变更通知"

    def body_text(self) -> str:
        action_label = {
            "config.update": "修改配置",
            "config.reset": "恢复默认值",
            "account.role_change": "角色变更",
        }.get(self.action, self.action)
        lines = [
            "系统配置发生变更。",
            "",
            f"操作类型：{action_label}",
            f"操作者：{self.actor_email or '（未知）'}",
            f"时间：{self.occurred_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"影响等级：{'高（模型/检索/Agent）' if self.high_impact else '普通'}",
            "",
            "变更项：",
        ]
        lines.extend(f"  - {key}" for key in self.keys)
        lines.extend(
            [
                "",
                "说明：出于安全考虑，本通知不包含任何配置值或密钥内容。",
                "如需核对详情，请登录系统在「设置 → 账户与权限 → 最近配置变更」查看。",
                "",
                "—— AI 私人医生系统自动通知，请勿直接回复。",
            ]
        )
        return "\n".join(lines)


def _build_message(mail: ConfigChangeMail, recipients: List[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = mail.subject()
    msg["From"] = formataddr(("AI 私人医生系统", settings.smtp_sender))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=False)
    msg.set_content(mail.body_text())
    return msg


def _default_send(msg: EmailMessage, recipients: List[str]) -> None:
    """真实发送（SSL 或 STARTTLS）。任何异常向上抛，由调用方降级处理。"""
    host = settings.smtp_host.strip()
    port = settings.smtp_port
    timeout = max(3, settings.smtp_timeout_seconds)
    if settings.smtp_use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg, from_addr=settings.smtp_sender, to_addrs=recipients)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg, from_addr=settings.smtp_sender, to_addrs=recipients)


def resolve_recipients(db=None) -> List[str]:
    """收件人：显式 SMTP_TO 优先；否则回退所有管理员邮箱。"""
    explicit = settings.smtp_recipient_list
    if explicit:
        return explicit
    if db is None:
        return []
    try:
        from app.config.notify import admin_recipients

        return [a.email for a in admin_recipients(db) if a.email]
    except Exception:  # noqa: BLE001
        logger.warning("mail.recipients.resolve_failed")
        return []


def send_config_change_mail(
    mail: ConfigChangeMail,
    *,
    recipients: Optional[List[str]] = None,
    db=None,
    background: bool = True,
) -> bool:
    """发送配置变更通知。

    返回 True 表示已提交发送（不保证对方已收到）。任何失败都返回 False 并记日志，
    **绝不抛出异常**——调用方是配置保存主流程。
    """
    if not settings.smtp_configured():
        logger.info("mail.skip reason=not_configured action=%s", mail.action)
        return False

    to_list = recipients if recipients is not None else resolve_recipients(db)
    if not to_list:
        logger.info("mail.skip reason=no_recipients action=%s", mail.action)
        return False

    try:
        msg = _build_message(mail, to_list)
    except Exception:  # noqa: BLE001
        logger.exception("mail.build_failed action=%s", mail.action)
        return False

    sender = _send_mail_impl or _default_send

    def _run() -> None:
        try:
            sender(msg, to_list)
            logger.info(
                "mail.sent action=%s highImpact=%s recipients=%s",
                mail.action,
                mail.high_impact,
                len(to_list),
            )
        except Exception as exc:  # noqa: BLE001 - 邮件失败不得影响业务
            logger.warning(
                "mail.failed action=%s reason=%s detail=%s",
                mail.action,
                type(exc).__name__,
                str(exc)[:120],
            )

    if background:
        threading.Thread(target=_run, name="config-mail", daemon=True).start()
        return True
    _run()
    return True


def notify_by_mail(
    *,
    action: str,
    keys: List[str],
    actor_email: str,
    high_impact: bool,
    db=None,
    background: bool = True,
) -> bool:
    """便捷入口：从配置变更参数构造并发送邮件。"""
    mail = ConfigChangeMail(
        action=action,
        keys=list(keys),
        actor_email=actor_email or "",
        high_impact=high_impact,
        occurred_at=datetime.now(timezone.utc),
    )
    return send_config_change_mail(mail, db=db, background=background)
