"""Redis 队列封装（enqueue/dequeue/心跳）：enqueue=信号，DB 为事实源（§5.1）。"""

import json
import logging
from typing import Optional

import redis as redis_lib

from app.config.settings import settings

logger = logging.getLogger("app.queue")

_client: Optional[redis_lib.Redis] = None


def get_redis() -> redis_lib.Redis:
    global _client
    if _client is None:
        _client = redis_lib.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=3,
            socket_timeout=5,
            decode_responses=True,
        )
    return _client


def enqueue_extract(job_id: str) -> bool:
    """LPUSH queue:extract {jobId}（失败仅告警——DB 中 job 仍为 queued，可补偿）。"""
    try:
        get_redis().lpush(settings.queue_extract, job_id)
        return True
    except redis_lib.RedisError as exc:
        logger.error("enqueue_extract failed jobId=%s err=%s", job_id, exc)
        return False


def dequeue_extract(timeout: int = 3) -> Optional[str]:
    """BRPOP queue:extract → jobId（worker 消费）。"""
    try:
        item = get_redis().brpop(settings.queue_extract, timeout=timeout)
    except redis_lib.RedisError as exc:
        logger.error("dequeue_extract failed err=%s", exc)
        return None
    if not item:
        return None
    _, job_id = item
    return json.loads(job_id) if job_id.strip().startswith(("{", '"')) else job_id


def ping() -> bool:
    try:
        return bool(get_redis().ping())
    except redis_lib.RedisError:
        return False
