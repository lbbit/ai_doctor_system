"""统一响应包裹格式（ARCHITECTURE §8.1，前后端唯一契约）。

成功：{"code": 0, "message": "ok", "data": ..., "requestId": "01J..."}
失败：{"code": "SUBJECT_REQUIRED", "message": "...", "data": null, "requestId": "..."}

例外：SSE 流不包裹（OpenAI chunk 形态）；/v1/chat/completions 用 OpenAI 错误形态；
/healthz、/metrics 裸格式。
"""

from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from app.common.ids import new_request_id

SUCCESS_CODE = 0


def get_request_id(request: Optional[Request] = None) -> str:
    """优先取中间件注入的 request_id，缺失则生成新的。"""
    if request is not None:
        rid = getattr(request.state, "request_id", None)
        if rid:
            return rid
    return new_request_id()


def api_ok(
    data: Any = None,
    request: Optional[Request] = None,
    message: str = "ok",
) -> dict:
    """成功包裹。"""
    return {
        "code": SUCCESS_CODE,
        "message": message,
        "data": data,
        "requestId": get_request_id(request),
    }


def api_err(
    code: str,
    message: str,
    http_status: int = 400,
    request: Optional[Request] = None,
    data: Any = None,
) -> JSONResponse:
    """失败包裹（JSONResponse）。"""
    return JSONResponse(
        status_code=http_status,
        content={
            "code": code,
            "message": message,
            "data": data,
            "requestId": get_request_id(request),
        },
    )


def ok_response(data: Any = None, request: Optional[Request] = None) -> JSONResponse:
    """api_ok 的 JSONResponse 形态（路由直接返回）。"""
    return JSONResponse(content=api_ok(data, request))
