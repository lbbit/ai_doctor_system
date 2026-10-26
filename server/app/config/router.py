"""系统配置路由（§6.9）：/api/settings/system。

契约：
- ``GET`` 返回全部白名单项，密钥类只返回掩码与"是否已配置"。
- ``PATCH`` 批量写入，**任一项非法整体拒绝**（不产生半写状态），写入后清缓存。
- ``POST /test`` 对指定 key 做联通性探测（LLM 最小请求 / 搜索空探测）。
- 所有写入记审计 ``config.update``；密钥类字段不审计具体值。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import get_current_account
from app.common.errors import ValidationError_
from app.common.responses import ok_response
from app.config import resolver
from app.config.mail import notify_by_mail
from app.config.notify import is_high_impact, notify_config_change
from app.config.permissions import is_admin, require_config_admin
from app.config.registry import ConfigValidationError, get_spec, list_specs
from app.config.store import mask_secret, reset_keys, upsert_many
from app.db.models import Account
from app.db.session import get_db

logger = logging.getLogger("app.config.router")

router = APIRouter(prefix="/api/settings/system", tags=["settings"])


class SettingItemIn(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: Any = None


class PatchPayload(BaseModel):
    items: List[SettingItemIn] = Field(min_length=1)


class ResetPayload(BaseModel):
    keys: List[str] = Field(min_length=1)


def _item_out(db: Session, key: str) -> dict:
    resolved = resolver.resolve(db, key)
    spec = resolved.spec
    if spec.secret_env_only:
        configured = bool(resolved.value and str(resolved.value).strip())
        return {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "group": spec.group,
            "kind": spec.kind,
            "isSecret": True,
            "secretConfigured": configured,
            "value": mask_secret(resolved.value) if configured else "",
            "default": "",
            "source": resolved.source,
            "readOnly": True,
            "requiresRestart": False,
            "min": None,
            "max": None,
            "choices": [],
            "envName": spec.env_name,
        }
    return {
        "key": spec.key,
        "label": spec.label,
        "description": spec.description,
        "group": spec.group,
        "kind": spec.kind,
        "isSecret": False,
        "secretConfigured": False,
        "value": resolved.value,
        "default": spec.default,
        "source": resolved.source,
        "readOnly": False,
        "requiresRestart": False,
        "min": spec.minimum,
        "max": spec.maximum,
        "choices": list(spec.choices),
        "envName": spec.env_name,
    }


@router.get("")
def get_system_settings(
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """全部配置项（含来源 db/env/default，密钥掩码）。

    读权限：登录即可（配置项展示对排查问题有帮劣）；
    写权限：由 ``require_config_admin`` 收敛，见 PATCH/RESET。
    """
    resolver.invalidate()
    can_write = is_admin(db, account)
    items = [_item_out(db, spec.key) for spec in list_specs()]
    groups: List[str] = []
    for item in items:
        if item["group"] not in groups:
            groups.append(item["group"])
    return ok_response(
        {
            "items": items,
            "groups": groups,
            "editable": can_write,
            "note": (
                "密钥类配置出于安全只展示是否已配置，需通过环境变量或 Secret 修改。"
                if can_write
                else "当前账户为只读权限，如需修改请联系管理员。"
            ),
        },
        request,
    )


@router.patch("")
def patch_system_settings(
    payload: PatchPayload,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """批量更新配置；任一项非法 → 422 且不写入任何项。无权限 → 403。"""
    require_config_admin(db, account)
    raw_items = [{"key": i.key, "value": i.value} for i in payload.items]
    try:
        changed = upsert_many(db, raw_items, account_id=account.id)
    except ConfigValidationError as exc:
        raise ValidationError_(f"配置项 {exc.key} 不合法：{exc.reason}") from exc

    # 审计：只记录 key 与非敏感值的变更，密钥类不入审计值
    audit_record(
        db,
        action="config.update",
        resource_type="system_setting",
        resource_id=None,
        account_id=account.id,
        subject_id=None,
        after={
            "changed": [
                {"key": s.key, "value": None if s.secret_env_only else None}
                for s in changed
            ]
        },
        request=request,
    )
    # 通知：高影响变更（llm./agent.）需告知管理员；失败不影响保存
    notify_config_change(
        db,
        account_id=account.id,
        account_email=account.email,
        action="config.update",
        keys=[s.key for s in changed],
    )
    db.commit()
    resolver.invalidate()
    from app.llm.client import reset_client_cache

    reset_client_cache()
    # 邮件通知：异步发送，失败不影响保存（§6.9）
    changed_keys = [s.key for s in changed]
    notify_by_mail(
        action="config.update",
        keys=changed_keys,
        actor_email=account.email,
        high_impact=is_high_impact(changed_keys),
        db=db,
    )
    logger.info("config.update account=%s keys=%s", account.id, [s.key for s in changed])
    return ok_response(
        {"updated": [s.key for s in changed], "items": [_item_out(db, s.key) for s in changed]},
        request,
    )


@router.post("/reset")
def reset_system_settings(
    payload: ResetPayload,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """删除覆盖项 → 回退 env 基线。无权限 → 403。"""
    require_config_admin(db, account)
    try:
        removed = reset_keys(db, payload.keys)
    except ConfigValidationError as exc:
        raise ValidationError_(f"配置项 {exc.key} 不合法：{exc.reason}") from exc
    audit_record(
        db,
        action="config.reset",
        resource_type="system_setting",
        resource_id=None,
        account_id=account.id,
        subject_id=None,
        after={"reset": removed},
        request=request,
    )
    notify_config_change(
        db,
        account_id=account.id,
        account_email=account.email,
        action="config.reset",
        keys=removed,
    )
    db.commit()
    resolver.invalidate()
    from app.llm.client import reset_client_cache

    reset_client_cache()
    notify_by_mail(
        action="config.reset",
        keys=removed,
        actor_email=account.email,
        high_impact=is_high_impact(removed),
        db=db,
    )
    return ok_response({"reset": removed}, request)


@router.post("/test")
def test_system_setting(
    payload: Dict[str, str],
    request: Request,
    account: Account = Depends(get_current_account),  # noqa: ARG001
    db: Session = Depends(get_db),
):
    """联通性探测：LLM 发最小请求；搜索发空查询；其他返回 unsupported。"""
    require_config_admin(db, account)
    key = str(payload.get("key") or "")
    try:
        spec = get_spec(key)
    except ConfigValidationError as exc:
        raise ValidationError_(f"配置项 {exc.key} 不合法：{exc.reason}") from exc

    if key.startswith("llm."):
        scenario = "chat" if key.startswith("llm.chat.") else "extraction"
        return ok_response(_test_llm(db, scenario), request)
    if key.startswith("agent."):
        return ok_response(_test_search(db), request)
    return ok_response(
        {"supported": False, "ok": None, "detail": "该配置项不支持联通性测试"}, request
    )


def _test_llm(db: Session, scenario: str) -> Optional[dict]:
    from app.config.resolver import resolve_llm_profile
    from app.common.errors import LLMTimeoutError, LLMUnavailableError
    from app.llm.client import LLMClient

    profile = resolve_llm_profile(db, scenario)
    if not profile.available():
        return {"supported": True, "ok": False, "detail": "未配置 API Key（离线降级）"}
    client = LLMClient(profile)
    try:
        client.chat(
            [{"role": "user", "content": "ping"}],
            temperature=0.0,
        )
        return {"supported": True, "ok": True, "detail": f"连通正常（模型 {profile.model}）"}
    except LLMTimeoutError:
        return {"supported": True, "ok": False, "detail": "连接超时"}
    except LLMUnavailableError as exc:
        return {"supported": True, "ok": False, "detail": str(exc) or "服务不可用"}
    except Exception as exc:  # noqa: BLE001 - 探测失败不应 500
        return {"supported": True, "ok": False, "detail": f"探测失败：{type(exc).__name__}"}


def _test_search(db: Session) -> dict:
    provider = resolver.get_str(db, "agent.search_provider", "free")
    if provider == "none":
        return {"supported": True, "ok": False, "detail": "当前未启用联网搜索"}
    try:
        from app.agent.search import build_provider

        p = build_provider(db)
        if p is None:
            return {"supported": True, "ok": False, "detail": "搜索提供方未就绪"}
        results = p.search("hypertension", max_results=1)
        return {
            "supported": True,
            "ok": True,
            "detail": f"{p.name} 返回 {len(results)} 条结果",
        }
    except Exception as exc:  # noqa: BLE001
        return {"supported": True, "ok": False, "detail": f"搜索探测失败：{type(exc).__name__}"}
