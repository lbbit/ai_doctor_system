"""structlog 配置 + request-id 上下文（ARCHITECTURE §8.4）。

JSON 单行：{"ts","level","event","requestId","accountId","subjectId","method",
"path","status","latencyMs",...}
日志脱敏：name/id_card/phone 键统一掩码。
"""

import logging
import sys
from typing import Optional

import structlog

_SENSITIVE_KEYS = {"name", "id_card", "phone", "password", "email", "token", "api_key"}

_configured = False


def _mask_sensitive(
    logger: object, method_name: str, event_dict: dict
) -> dict:
    """structlog processor：敏感键统一掩码（日志侧脱敏，§6.2）。"""
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            val = event_dict[key]
            if isinstance(val, str) and val:
                event_dict[key] = val[:1] + "***" if len(val) > 1 else "***"
    return event_dict


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """进程级配置（main.py / worker.py 启动时调用一次）。"""
    global _configured
    if _configured:
        return
    _configured = True

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    # 依赖探测类库的重试告警会刷屏（DNS 失败场景），压到 ERROR
    for noisy in ("urllib3", "urllib3.connectionpool", "minio"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        _mask_sensitive,
    ]
    if fmt == "json":
        processors += [
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
    else:
        processors += [
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", key="ts"),
            structlog.dev.ConsoleRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: Optional[str] = None, **initial) -> structlog.BoundLogger:
    """获取结构化 logger。"""
    return structlog.get_logger(name).bind(**initial)


# 关键事件名常量（§8.4 必打事件）
EV_AUTH_LOGIN = "auth.login"
EV_DOCUMENT_UPLOADED = "document.uploaded"
EV_EXTRACTION_DONE = "extraction.done"
EV_EXTRACTION_FAILED = "extraction.failed"
EV_RECORD_CONFIRMED = "record.confirmed"
EV_CHAT_MESSAGE = "chat.message"
EV_LLM_CALL = "llm.call"
EV_REDFLAG_HIT = "redflag.hit"
