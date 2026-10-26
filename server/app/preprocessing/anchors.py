"""来源锚点服务（R1 T103）：块落库与定位解析。

职责：
- 把解析产物（页文本、表格行、字段位置）落成 `document_blocks`；
- 由指标/事件的 `source_field_ref` 反查"文档 + 页 + 区域"；
- 锚点**绑定抽取版本**，重抽不改变旧引用的解释。

关键约束：
1. **没有可靠坐标就不给坐标**：`anchor_status=page_only`，不伪造 bbox。
2. **坐标系必须声明**：否则坐标无法解释。
3. **版本隔离**：同一页在不同抽取版本下可有不同块；旧引用仍定位旧版本。
4. 落库幂等：同一 (document, version, page, block) 唯一，重复写入不产生重复行。
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.common.ids import new_id
from app.db.models import Document, DocumentBlock
from app.db.models.document_block import (
    ANCHOR_PAGE_ONLY,
    ANCHOR_REGION,
    ANCHOR_UNAVAILABLE,
    COORD_PIXEL_TOP_LEFT,
)

logger = logging.getLogger("app.preprocessing.anchors")


def _coord_ok(bbox: Optional[dict]) -> bool:
    """坐标是否可信（四个数值都存在且 w/h 为正）。"""
    if not isinstance(bbox, dict):
        return False
    try:
        w = float(bbox.get("w") or 0)
        h = float(bbox.get("h") or 0)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0:
        return False
    return bbox.get("x") is not None and bbox.get("y") is not None


def make_block(
    *,
    document: Document,
    page_no: int,
    block_id: str,
    text: str,
    extraction_version: int = 1,
    asset_version: Optional[int] = None,
    block_type: str = "text",
    bbox: Optional[dict] = None,
    coordinate_system: str = COORD_PIXEL_TOP_LEFT,
    parser: Optional[str] = None,
    parser_version: Optional[str] = None,
) -> DocumentBlock:
    """构造一个块（不落库）。

    坐标不可信时**只给页级锚点**，不填任何坐标值——
    伪造坐标会让"点击跳转到原文"落到错误位置，比不跳更糟。
    """
    has_region = _coord_ok(bbox)
    return DocumentBlock(
        id=new_id(),
        document_id=document.id,
        account_id=document.account_id,
        subject_id=document.subject_id,
        extraction_version=extraction_version,
        asset_version=asset_version,
        page_no=page_no,
        block_id=block_id,
        block_type=block_type,
        text=text or "",
        x=float(bbox["x"]) if has_region else None,
        y=float(bbox["y"]) if has_region else None,
        w=float(bbox["w"]) if has_region else None,
        h=float(bbox["h"]) if has_region else None,
        coordinate_system=coordinate_system if has_region else COORD_PIXEL_TOP_LEFT,
        anchor_status=ANCHOR_REGION if has_region else ANCHOR_PAGE_ONLY,
        parser=parser,
        parser_version=parser_version,
    )


def store_blocks(
    db: Session,
    document: Document,
    blocks: Iterable[DocumentBlock],
    *,
    extraction_version: int = 1,
    replace: bool = True,
) -> int:
    """落块（幂等）。

    `replace=True` 时先删除**同版本**的旧块（重抽同版本覆盖）；
    不同版本的块共存，保证旧引用仍能定位。
    """
    block_list = list(blocks)
    if replace:
        db.execute(
            delete(DocumentBlock).where(
                DocumentBlock.document_id == document.id,
                DocumentBlock.extraction_version == extraction_version,
            )
        )
        db.flush()
    for b in block_list:
        b.extraction_version = extraction_version
        db.add(b)
    db.flush()
    return len(block_list)


def list_blocks(
    db: Session,
    document: Document,
    *,
    page_no: Optional[int] = None,
    extraction_version: Optional[int] = None,
) -> list[DocumentBlock]:
    """列出块（按页、块序号）。"""
    q = select(DocumentBlock).where(DocumentBlock.document_id == document.id)
    if page_no is not None:
        q = q.where(DocumentBlock.page_no == page_no)
    if extraction_version is not None:
        q = q.where(DocumentBlock.extraction_version == extraction_version)
    return list(
        db.execute(q.order_by(DocumentBlock.page_no, DocumentBlock.block_id)).scalars()
    )


def find_block(
    db: Session,
    document: Document,
    block_id: str,
    *,
    extraction_version: Optional[int] = None,
) -> Optional[DocumentBlock]:
    """按块 ID 定位（可限定抽取版本）。"""
    q = select(DocumentBlock).where(
        DocumentBlock.document_id == document.id,
        DocumentBlock.block_id == block_id,
    )
    if extraction_version is not None:
        q = q.where(DocumentBlock.extraction_version == extraction_version)
    return db.execute(q.limit(1)).scalars().first()


def block_out(block: DocumentBlock) -> dict:
    """块视图（供引用回链与前端定位）。"""
    out = {
        "blockId": block.block_id,
        "pageNo": block.page_no,
        "blockType": block.block_type,
        "text": block.text,
        "anchorStatus": block.anchor_status,
        "extractionVersion": block.extraction_version,
    }
    if block.anchor_status == ANCHOR_REGION:
        out["bbox"] = {
            "x": block.x,
            "y": block.y,
            "w": block.w,
            "h": block.h,
            "coordinateSystem": block.coordinate_system,
        }
    return out


def anchor_for_ref(
    db: Session,
    document: Document,
    field_ref: Optional[str],
    *,
    extraction_version: Optional[int] = None,
) -> Optional[dict]:
    """由 `source_field_ref` 解析出定位信息。

    现有 `source_field_ref` 形如 `obs:0` / `event:1`（字段下标），
    R1 T103 起可带块 ID（如 `obs:0#b12`）。两者都支持：
    - 带块 ID → 精确定位到块（页 + 区域）；
    - 仅有下标 → **不猜测**位置，返回 None（宁可无定位，不给错位置）。
    """
    if not field_ref or "#" not in field_ref:
        return None
    block_id = field_ref.split("#", 1)[1].strip()
    if not block_id:
        return None
    block = find_block(
        db, document, block_id, extraction_version=extraction_version
    )
    if block is None:
        return None
    return block_out(block)
