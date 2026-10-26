"""common 模块：错误、响应包裹、日志、分页、ID 生成（§8 跨文件约定）。"""

from app.common.errors import AppError
from app.common.ids import new_id, new_request_id
from app.common.pagination import PageParams, make_page, page_params
from app.common.responses import api_err, api_ok, ok_response

__all__ = [
    "AppError",
    "new_id",
    "new_request_id",
    "PageParams",
    "make_page",
    "page_params",
    "api_err",
    "api_ok",
    "ok_response",
]
