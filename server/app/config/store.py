"""system_settings 读写（DB 覆盖层）。

安全边界：
- 密钥类配置（api_key）不落库、不回显明文，只报告"是否已配置"。
- 未登记 key 一律拒绝（依赖 registry）。
- 写入即失效缓存，保证下一次调用生效。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config.registry import (
    ConfigValidationError,
    SettingSpec,
    coerce_and_validate,
    get_spec,
    list_specs,
)
from app.db.models.system_setting import SystemSetting

logger = logging.getLogger("app.config.store")

MASK = "••••••••"


def load_overrides(db: Session) -> Dict[str, Any]:
    """读取全部 DB 覆盖值（key → 已校验值）。

    历史脏数据（类型不符/已下线 key）会被跳过而不是让整站配置失效。
    """
    rows = db.execute(select(SystemSetting)).scalars().all()
    out: Dict[str, Any] = {}
    for row in rows:
        try:
            out[row.key] = coerce_and_validate_allow_secret(row.key, row.value_json)
        except ConfigValidationError:
            logger.warning("config.override.skip key=%s reason=invalid", row.key)
    return out


def coerce_and_validate_allow_secret(key: str, raw: Any) -> Any:
    """读取路径专用：密钥项允许读（仅用于判断是否存在），但不接受写入。

    说明：DB 中本不应存在密钥行；若历史遗留则直接忽略，避免误当有效配置。
    """
    spec = get_spec(key)
    if spec.secret_env_only:
        raise ConfigValidationError(key, "密钥不存于数据库")
    from app.config.registry import _coerce  # noqa: PLC0415

    value = _coerce(spec, raw)
    if spec.kind == "int":
        if spec.minimum is not None and value < spec.minimum:
            raise ConfigValidationError(key, f"不能小于 {int(spec.minimum)}")
        if spec.maximum is not None and value > spec.maximum:
            raise ConfigValidationError(key, f"不能大于 {int(spec.maximum)}")
    if spec.kind == "enum" and value not in spec.choices:
        raise ConfigValidationError(key, f"只能是 {', '.join(spec.choices)} 之一")
    if spec.validate is not None and spec.validate(value):
        raise ConfigValidationError(key, spec.validate(value) or "")
    return value


def upsert_many(
    db: Session,
    items: Iterable[Dict[str, Any]],
    account_id: Optional[str],
) -> List[SettingSpec]:
    """批量写入；**先全量校验再落库**，任一项非法则整体不写。

    返回被修改的 spec 列表（供审计使用）。
    """
    # 第一步：全量校验（任何失败都在此抛出，不产生半写状态）
    validated: List[tuple[SettingSpec, Any]] = []
    for item in items:
        key = str(item.get("key", ""))
        spec = get_spec(key)
        value = coerce_and_validate(key, item.get("value"))
        validated.append((spec, value))

    # 第二步：落库（delete + insert 语义，避免方言 upsert 差异）
    changed: List[SettingSpec] = []
    for spec, value in validated:
        row = db.get(SystemSetting, spec.key)
        if row is None:
            db.add(
                SystemSetting(key=spec.key, value_json=value, updated_by=account_id)
            )
        else:
            row.value_json = value
            row.updated_by = account_id
        changed.append(spec)
    db.flush()
    return changed


def reset_keys(db: Session, keys: Iterable[str]) -> List[str]:
    """删除覆盖项 → 回退 env 基线。"""
    removed: List[str] = []
    for key in keys:
        get_spec(key)  # 未登记 key 直接拒绝
        db.execute(delete(SystemSetting).where(SystemSetting.key == key))
        removed.append(key)
    db.flush()
    return removed


def mask_secret(value: Optional[str]) -> str:
    """密钥展示：只表明"已配置"，绝不回显任何字符。"""
    return MASK if value and value.strip() else ""


def resolve_env_value(spec: SettingSpec, env_reader) -> Any:
    """从 env 基线取默认值（env_reader 抽象出来便于测试）。

    密钥项直接从 env 取值（它们**本来就不允许**写入 DB，
    因此不能走"拒绝 secret"的校验路径）。
    """
    if not spec.env_name:
        return spec.default
    raw = env_reader(spec.env_name)
    if raw is None or raw == "":
        return spec.default
    if spec.secret_env_only:
        return raw.strip() if isinstance(raw, str) else raw
    try:
        return coerce_and_validate_allow_secret(spec.key, raw)
    except ConfigValidationError:
        logger.warning("config.env.skip key=%s reason=invalid", spec.key)
        return spec.default
