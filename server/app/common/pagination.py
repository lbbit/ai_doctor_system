"""分页参数/返回（§4.1：?page=1&pageSize=20 → data:{items,page,pageSize,total}）。"""

import math
from typing import Any, Generic, List, Sequence, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

MAX_PAGE_SIZE = 100


class PageParams(BaseModel):
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class Page(BaseModel, Generic[T]):
    items: List[T]
    page: int
    pageSize: int
    total: int


def make_page(items: Sequence[Any], page: int, page_size: int, total: int) -> dict:
    """构建分页信封（camelCase pageSize，§8.3）。"""
    return {
        "items": list(items),
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size) if page_size else 0,
    }


def page_params(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE, description="每页条数"),
) -> PageParams:
    """FastAPI 依赖：分页查询参数。"""
    return PageParams(page=page, page_size=page_size)
