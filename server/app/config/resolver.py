"""配置解析：DB 覆盖 → env 基线 → 内置默认（带 TTL 缓存）。

缓存策略：进程内缓存 30s。配置修改后调用 ``invalidate()`` 立即可见；
多进程（api/worker）之间最多 30s 延迟，属于可接受权衡（重启代价更高）。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.config.registry import SettingSpec, get_spec, list_specs
from app.config.store import load_overrides, mask_secret, resolve_env_value
from app.config.settings import settings as env_settings

logger = logging.getLogger("app.config.resolver")

CACHE_TTL_SECONDS = 30

_cache: Dict[str, Any] = {}
_cache_loaded_at: float = 0.0

SOURCE_DB = "db"
SOURCE_ENV = "env"
SOURCE_DEFAULT = "default"


@dataclass
class ResolvedSetting:
    key: str
    value: Any
    display: Any
    source: str
    spec: SettingSpec


def invalidate() -> None:
    """清空缓存（写入配置后调用）。"""
    global _cache, _cache_loaded_at
    _cache = {}
    _cache_loaded_at = 0.0


def _cache_fresh() -> bool:
    return bool(_cache) and (time.time() - _cache_loaded_at) < CACHE_TTL_SECONDS


def _env_reader(name: str) -> Optional[str]:
    return os.environ.get(name)


def _env_baseline(spec: SettingSpec) -> Any:
    """env 基线；LLM 相关键未显式配置时回退到 Settings 单例的既有字段。"""
    explicit = resolve_env_value(spec, _env_reader)
    if explicit != spec.default or not spec.env_name:
        return explicit
    # 无 env 变量时的语义化回退，保证"没配过"仍可用
    fallback_map = {
        "llm.extraction.base_url": env_settings.llm_base_url,
        "llm.extraction.api_key": env_settings.llm_api_key,
        "llm.extraction.model": env_settings.llm_model,
        "llm.extraction.vlm_model": env_settings.llm_vlm_model,
        "llm.extraction.timeout_seconds": env_settings.llm_timeout_seconds,
        "llm.chat.base_url": env_settings.llm_base_url,
        "llm.chat.api_key": env_settings.llm_api_key,
        "llm.chat.model": env_settings.llm_model,
        "llm.chat.vlm_model": env_settings.llm_vlm_model,
        "llm.chat.timeout_seconds": env_settings.llm_timeout_seconds,
        "privacy.pii_masking_enabled": env_settings.pii_masking_enabled,
        "upload.max_upload_mb": env_settings.max_upload_mb,
    }
    return fallback_map.get(spec.key, explicit)


def _load(db: Optional[Session]) -> Dict[str, Any]:
    global _cache, _cache_loaded_at
    if _cache_fresh():
        return _cache
    overrides: Dict[str, Any] = {}
    if db is not None:
        try:
            overrides = load_overrides(db)
        except Exception:  # noqa: BLE001 - 配置层故障不得拖垮业务
            logger.warning("config.overrides.load_failed, fallback to env")
            overrides = {}
    resolved: Dict[str, Any] = {}
    for spec in list_specs():
        if spec.key in overrides:
            resolved[spec.key] = (overrides[spec.key], SOURCE_DB)
        else:
            value = _env_baseline(spec)
            source = SOURCE_ENV if value != spec.default else SOURCE_DEFAULT
            resolved[spec.key] = (value, source)
    _cache = resolved
    _cache_loaded_at = time.time()
    return _cache


def resolve(db: Optional[Session], key: str) -> ResolvedSetting:
    """解析单个配置项。"""
    spec = get_spec(key)
    data = _load(db)
    value, source = data.get(key, (spec.default, SOURCE_DEFAULT))
    display: Any = value
    if spec.secret_env_only:
        display = mask_secret(value)
        value = value  # 内部调用仍可用真实值（用于构造客户端）
    return ResolvedSetting(key=key, value=value, display=display, source=source, spec=spec)


def resolve_all(db: Optional[Session]) -> Dict[str, Any]:
    """全部已登记项的值（不含掩码，用于内部业务逻辑）。"""
    return {key: val for key, (val, _src) in _load(db).items()}


def get_bool(db: Optional[Session], key: str, default: bool = False) -> bool:
    try:
        value = resolve(db, key).value
        return bool(value)
    except Exception:  # noqa: BLE001
        return default


def get_int(db: Optional[Session], key: str, default: int = 0) -> int:
    try:
        value = resolve(db, key).value
        return int(value)
    except Exception:  # noqa: BLE001
        return default


def get_str(db: Optional[Session], key: str, default: str = "") -> str:
    try:
        value = resolve(db, key).value
        return str(value) if value is not None else default
    except Exception:  # noqa: BLE001
        return default


@dataclass
class LLMProfile:
    """分场景 LLM 配置快照。"""

    scenario: str
    base_url: str
    api_key: str
    model: str
    vlm_model: str
    timeout_seconds: int

    def available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


def resolve_llm_profile(db: Optional[Session], scenario: str) -> LLMProfile:
    """按场景取 LLM 配置；scenario ∈ {chat, extraction}。

    未知场景回退 extraction（成本更低、更保守），并由调用方日志暴露。
    """
    if scenario not in ("chat", "extraction"):
        logger.warning("config.llm.unknown_scenario scenario=%s fallback=extraction", scenario)
        scenario = "extraction"
    prefix = f"llm.{scenario}."
    return LLMProfile(
        scenario=scenario,
        base_url=get_str(db, prefix + "base_url"),
        api_key=get_str(db, prefix + "api_key"),
        model=get_str(db, prefix + "model"),
        vlm_model=get_str(db, prefix + "vlm_model"),
        timeout_seconds=get_int(db, prefix + "timeout_seconds", 60),
    )
