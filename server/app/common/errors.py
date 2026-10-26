"""统一异常类与错误码（ARCHITECTURE §4.4/§8.2）+ 全局异常 handler。"""

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.responses import api_err

logger = logging.getLogger("app.errors")

# ---- 错误码常量（§4.4；新增必须同步更新 §4.4 与前端 api/errors.ts）----
BAD_REQUEST = "BAD_REQUEST"
UNAUTHENTICATED = "UNAUTHENTICATED"
TOKEN_EXPIRED = "TOKEN_EXPIRED"
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
LINK_EXPIRED = "LINK_EXPIRED"
NOT_FOUND = "NOT_FOUND"
DUP_FILE = "DUP_FILE"
REVIEW_CONFLICT = "REVIEW_CONFLICT"
STATE_INVALID = "STATE_INVALID"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
SUBJECT_REQUIRED = "SUBJECT_REQUIRED"
VALIDATION_ERROR = "VALIDATION_ERROR"
RATE_LIMITED = "RATE_LIMITED"
LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
LLM_TIMEOUT = "LLM_TIMEOUT"
INTERNAL = "INTERNAL"


class AppError(Exception):
    """业务异常基类；全局 handler 捕获 → api_err 包裹。"""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail


# ---- 常用子类 ----
class SubjectRequiredError(AppError):
    """写请求缺 subjectId（B-1/AC-3.2）。"""

    def __init__(self, message: str = "写入必须选择档案对象") -> None:
        super().__init__(SUBJECT_REQUIRED, message, 422)


class UnauthenticatedError(AppError):
    def __init__(self, message: str = "未登录或登录状态无效", code: str = UNAUTHENTICATED):
        super().__init__(code, message, 401)


class TokenExpiredError(AppError):
    def __init__(self, message: str = "登录已过期，请重新登录"):
        super().__init__(TOKEN_EXPIRED, message, 401)


class InvalidCredentialsError(AppError):
    """登录失败统一 401（AC-1.2，不泄露账号是否存在）。"""

    def __init__(self, message: str = "邮箱或密码错误"):
        super().__init__(INVALID_CREDENTIALS, message, 401)


class NotFoundError(AppError):
    """越权/不存在一律 404（K4/IDOR 防探测）。"""

    def __init__(self, message: str = "资源不存在"):
        super().__init__(NOT_FOUND, message, 404)


class ForbiddenError(AppError):
    def __init__(self, message: str = "无权访问", code: str = "FORBIDDEN"):
        super().__init__(code, message, 403)


class ConflictError(AppError):
    def __init__(self, code: str = DUP_FILE, message: str = "资源冲突", detail: Any = None):
        super().__init__(code, message, 409, detail)


class StateInvalidError(AppError):
    """状态机非法转移（§3.3）。"""

    def __init__(self, message: str = "非法状态转移"):
        super().__init__(STATE_INVALID, message, 409)


class ValidationError_(AppError):
    """业务语义校验失败（422 VALIDATION_ERROR，区别于 pydantic 校验）。"""

    def __init__(self, message: str = "请求参数不合法", detail: Any = None):
        super().__init__(VALIDATION_ERROR, message, 422, detail)


class ReviewConflictError(AppError):
    """并发确认冲突（E-7，乐观锁）。"""

    def __init__(self, message: str = "该报告已在其他页面处理，请刷新后重试"):
        super().__init__(REVIEW_CONFLICT, message, 409)


class FileTooLargeError(AppError):
    def __init__(self, message: str = "文件超过大小限制"):
        super().__init__(FILE_TOO_LARGE, message, 413)


class UnsupportedMediaTypeError(AppError):
    def __init__(self, message: str = "不支持的文件格式"):
        super().__init__(UNSUPPORTED_MEDIA_TYPE, message, 415)


class RateLimitedError(AppError):
    def __init__(self, message: str = "操作过于频繁，请稍后再试"):
        super().__init__(RATE_LIMITED, message, 429)


class LLMUnavailableError(AppError):
    def __init__(self, message: str = "AI 服务暂不可用，请稍后重试或手动录入"):
        super().__init__(LLM_UNAVAILABLE, message, 502)


class LLMTimeoutError(AppError):
    def __init__(self, message: str = "AI 服务响应超时，请重试"):
        super().__init__(LLM_TIMEOUT, message, 504)


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理：统一 → api_err 包裹格式（§8.1/§8.2）。"""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return api_err(exc.code, exc.message, exc.http_status, request, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # FastAPI 内建 HTTP 异常映射到规范错误码
        mapping = {
            401: UNAUTHENTICATED,
            403: "FORBIDDEN",
            404: NOT_FOUND,
            405: BAD_REQUEST,
            409: "CONFLICT",
            413: FILE_TOO_LARGE,
            415: UNSUPPORTED_MEDIA_TYPE,
            422: VALIDATION_ERROR,
            429: RATE_LIMITED,
        }
        code = mapping.get(exc.status_code, INTERNAL if exc.status_code >= 500 else BAD_REQUEST)
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return api_err(code, detail, exc.status_code, request)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # pydantic 校验失败：422 VALIDATION_ERROR；缺 subjectId 的语义由业务层显式抛出
        return api_err(
            VALIDATION_ERROR,
            "请求参数校验失败",
            422,
            request,
            data={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", "")
        logger.exception("unhandled_error requestId=%s path=%s", rid, request.url.path)
        return api_err(INTERNAL, "服务器内部错误（请提供 requestId 排查）", 500, request)
