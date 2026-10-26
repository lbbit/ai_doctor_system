"""FastAPI 实例：middleware、全局异常、路由挂载、健康检查（§4.2/§8.4）。"""

import threading
import time
from contextvars import ContextVar

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.auth.router import router as auth_router
from app.audit.router import router as audit_router
from app.chat.router import router as chat_router
from app.common.errors import register_exception_handlers
from app.common.ids import new_request_id
from app.common.logging import get_logger, setup_logging
from app.config.accounts_router import router as accounts_router
from app.config.router import router as system_config_router
from app.config.settings import settings
from app.documents.router import router as documents_router
from app.observations.router import router as observations_router
from app.redflag.router import router as redflag_router
from app.subjects.router import router as subjects_router
from app.timeline.router import router as timeline_router

setup_logging(settings.log_level, settings.log_format)
logger = get_logger("app")

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

DESCRIPTION = """AI 私人医生系统 P0 — FastAPI 单体服务。
响应统一包裹：`{code, message, data, requestId}`；SSE 流与 /healthz、/metrics 例外。"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI 私人医生系统",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # --- CORS（prod 同源由 nginx 收敛）---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    # --- request-id + 访问日志 middleware（§8.4）---
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-Id") or new_request_id()
        token = request_id_ctx.set(rid)
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-Id"] = rid
        # 健康探针不刷屏
        if request.url.path not in ("/healthz", "/readyz", "/metrics"):
            logger.info(
                "http_request",
                requestId=rid,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                latencyMs=latency_ms,
            )
        return response

    register_exception_handlers(app)

    # --- 路由挂载（§4.2 API 清单）---
    app.include_router(auth_router)      # /api/auth/*
    app.include_router(subjects_router)  # /api/subjects/*
    app.include_router(documents_router) # /api/documents/*, /api/dictionary/*(部分)
    app.include_router(observations_router)  # /api/observations/*, /api/dictionary/*
    app.include_router(timeline_router)  # /api/timeline/*
    app.include_router(chat_router)      # /api/chat/*, /v1/chat/completions
    app.include_router(redflag_router)   # /api/redflag/*
    app.include_router(audit_router)     # /api/audit/*, /api/settings
    app.include_router(system_config_router)  # /api/settings/system
    app.include_router(accounts_router)       # /api/accounts（角色与配置通知）

    # --- 健康检查（裸格式，§4.2 44/45）---
    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        return _deps_health()

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict:
        health = _deps_health()
        return health

    # --- Prometheus（内网）---
    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def _probe_db() -> str:
    try:
        from sqlalchemy import text
        from app.db.session import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"


def _probe_redis() -> str:
    try:
        from app.preprocessing.queue import get_redis
        get_redis().ping()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"


def _probe_minio() -> str:
    try:
        from app.documents.storage import get_storage_client
        get_storage_client().client.list_buckets()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"


# 探活快照：后台守护线程周期探测，/healthz 只读快照（请求路径零阻塞，
# 慢依赖（DNS 失败等）不会拖垮事件循环；K8s/Compose 探活语义不受影响）。
# 种子值保证首次探测前 deps 键完整（status=starting/degraded，不误报空集）。
_probe_snapshot: dict = {
    "deps": {"db": "pending", "redis": "pending", "minio": "pending"},
    "ts": 0.0,
}
_probe_thread = None


def _probe_loop() -> None:
    while True:
        try:
            deps = {"db": _probe_db(), "redis": _probe_redis(), "minio": _probe_minio()}
            _probe_snapshot["deps"] = deps
            _probe_snapshot["ts"] = time.monotonic()
        except Exception:  # noqa: BLE001  探测循环永不退出
            pass
        time.sleep(5)


def _ensure_probe_thread() -> None:
    global _probe_thread
    if _probe_thread is None:
        _probe_thread = threading.Thread(
            target=_probe_loop, name="healthz-probe", daemon=True
        )
        _probe_thread.start()


def _deps_health() -> dict:
    """依赖探活：db / redis / minio（§12.2）。
    后台线程每 5s 探测一次并更新快照；任一失败 → status=degraded（HTTP 200）。"""
    _ensure_probe_thread()
    deps = _probe_snapshot["deps"]
    if _probe_snapshot["ts"] == 0.0:
        # 首次探测尚未完成：键完整但状态未定，报 starting。
        return {"status": "starting", "deps": dict(deps)}
    return {"status": _status_of(deps), "deps": dict(deps)}


def _status_of(deps: dict) -> str:
    return "ok" if all(v == "ok" for v in deps.values()) else "degraded"


app = create_app()
