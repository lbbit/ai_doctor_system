"""规则缓存加载与热更新（AC-11.3：TTL 轮询 + Redis pub/sub 失效，§6.3）。"""

import logging
import threading
import time
from typing import List, Optional

from sqlalchemy import select

from app.config.settings import settings
from app.db.models import RedFlagRule
from app.db.session import SessionLocal

logger = logging.getLogger("app.redflag.loader")

INVALIDATE_CHANNEL = "redflag:invalidate"

_lock = threading.Lock()
_rules: List[RedFlagRule] = []
_loaded_at: float = 0.0
_version_key: str = ""
_subscribed = False


def _load_from_db() -> tuple[List[RedFlagRule], str]:
    db = SessionLocal()
    try:
        rows = list(
            db.execute(
                select(RedFlagRule).where(RedFlagRule.enabled.is_(True))
            ).scalars()
        )
        # detach：避免跨 session 持有
        key = ",".join(sorted(f"{r.code}:{r.version}" for r in rows))
        detached = []
        for r in rows:
            detached.append(
                RedFlagRule(
                    id=r.id, code=r.code, name=r.name, category=r.category,
                    keywords=list(r.keywords or []), keyword_logic=r.keyword_logic,
                    context_logic=r.context_logic, severity=r.severity,
                    message=r.message, action=r.action, enabled=r.enabled,
                    version=r.version, effective_from=r.effective_from,
                    created_at=r.created_at, updated_at=r.updated_at,
                )
            )
        return detached, key
    finally:
        db.close()


def reload(force: bool = False) -> None:
    """重载规则（版本无变化且非 force 时跳过写入）。"""
    global _rules, _loaded_at, _version_key
    try:
        rows, key = _load_from_db()
    except Exception:  # noqa: BLE001
        logger.exception("redflag.reload_failed")
        return
    with _lock:
        if force or key != _version_key or not _rules:
            _rules = rows
            _version_key = key
        _loaded_at = time.monotonic()


def get_rules() -> List[RedFlagRule]:
    """带 TTL 的规则读取（REDFLAG_RELOAD_TTL_SECONDS 默认 60s）。"""
    global _loaded_at
    ttl = settings.redflag_reload_ttl_seconds
    if time.monotonic() - _loaded_at >= ttl:
        reload()
    with _lock:
        return list(_rules)


def invalidate() -> None:
    """主动失效（PATCH 后 PUBLISH redflag:invalidate）。"""
    reload(force=True)
    try:
        from app.preprocessing.queue import get_redis

        get_redis().publish(INVALIDATE_CHANNEL, "1")
    except Exception:  # noqa: BLE001
        pass  # Redis 不可用时依赖 TTL 兜底


def ensure_subscriber() -> None:
    """启动 pub/sub 订阅线程（幂等；daemon 线程）。"""
    global _subscribed
    if _subscribed:
        return
    _subscribed = True

    def _listen() -> None:
        while True:
            try:
                from app.preprocessing.queue import get_redis

                r = get_redis()
                pubsub = r.pubsub()
                pubsub.subscribe(INVALIDATE_CHANNEL)
                for msg in pubsub.listen():
                    if msg.get("type") == "message":
                        reload(force=True)
            except Exception:  # noqa: BLE001
                time.sleep(5)  # Redis 断连退避，TTL 兜底

    threading.Thread(target=_listen, name="redflag-pubsub", daemon=True).start()
