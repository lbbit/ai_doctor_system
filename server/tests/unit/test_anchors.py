"""来源锚点与页块（R1 T103）。

不变量：
1. **坐标不可信时不给坐标**（`anchor_status=page_only`），不伪造 bbox；
2. 坐标系必须声明；
3. **版本隔离**：重抽产生新版本块，旧版本块仍保留（旧引用仍能定位）；
4. 落库幂等：同 (document, version, page, block) 不重复；
5. `source_field_ref` 带块 ID 时可解析出定位；仅下标时**不猜测**位置。
"""

from __future__ import annotations

import pytest

from app.common.ids import new_id
from app.db.models import Document
from app.db.models.document_block import (
    ANCHOR_PAGE_ONLY,
    ANCHOR_REGION,
    COORD_NORMALIZED,
)
from app.preprocessing import anchors
from tests.conftest import make_png_bytes


def _upload(client, auth, salt=b""):
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("a.png", make_png_bytes(salt), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


def _doc(db, doc_id):
    db.expire_all()
    return db.get(Document, doc_id)


def test_block_without_bbox_is_page_only(client, auth, db):
    """无坐标 → 只给页级锚点，不伪造坐标。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    block = anchors.make_block(
        document=row, page_no=3, block_id="b1", text="血红蛋白 145 g/L"
    )
    assert block.anchor_status == ANCHOR_PAGE_ONLY
    assert block.x is None and block.y is None
    assert block.w is None and block.h is None
    assert block.page_no == 3


def test_block_with_valid_bbox_gets_region(client, auth, db):
    """坐标可信 → 可定位到区域，并声明坐标系。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    block = anchors.make_block(
        document=row, page_no=1, block_id="b1", text="血红蛋白 145 g/L",
        bbox={"x": 10, "y": 20, "w": 100, "h": 12},
        coordinate_system=COORD_NORMALIZED,
    )
    assert block.anchor_status == ANCHOR_REGION
    assert block.x == 10 and block.h == 12
    assert block.coordinate_system == COORD_NORMALIZED


def test_block_with_invalid_bbox_is_not_region(client, auth, db):
    """坐标不完整/尺寸非正 → 不得当作可定位区域。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    for bad in (
        {"x": 10, "y": 20, "w": 0, "h": 12},
        {"x": None, "y": 20, "w": 100, "h": 12},
        {"x": 1, "y": 2},
        "not-a-dict",
        None,
    ):
        block = anchors.make_block(
            document=row, page_no=1, block_id="b", text="x", bbox=bad
        )
        assert block.anchor_status == ANCHOR_PAGE_ONLY, bad


def test_store_blocks_is_idempotent(client, auth, db):
    """同版本重复落库不产生重复行。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    blocks = [
        anchors.make_block(document=row, page_no=1, block_id="b1", text="甲"),
        anchors.make_block(document=row, page_no=1, block_id="b2", text="乙"),
    ]
    anchors.store_blocks(db, row, blocks, extraction_version=1)
    db.commit()
    assert len(anchors.list_blocks(db, row)) == 2

    # 再次落同版本（replace）→ 仍是 2 行
    again = [
        anchors.make_block(document=row, page_no=1, block_id="b1", text="甲改"),
    ]
    anchors.store_blocks(db, row, again, extraction_version=1)
    db.commit()
    rows = anchors.list_blocks(db, row)
    assert len(rows) == 1
    assert rows[0].text == "甲改"


def test_versions_are_isolated(client, auth, db):
    """重抽产生新版本块，旧版本块保留（旧引用仍能定位）。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])

    v1 = [anchors.make_block(document=row, page_no=1, block_id="b1", text="第一版")]
    anchors.store_blocks(db, row, v1, extraction_version=1)
    db.commit()

    v2 = [anchors.make_block(document=row, page_no=1, block_id="b1", text="第二版")]
    anchors.store_blocks(db, row, v2, extraction_version=2)
    db.commit()

    all_rows = anchors.list_blocks(db, row)
    assert len(all_rows) == 2
    # 按版本可分别定位
    assert anchors.find_block(db, row, "b1", extraction_version=1).text == "第一版"
    assert anchors.find_block(db, row, "b1", extraction_version=2).text == "第二版"


def test_anchor_for_ref_resolves_block(client, auth, db):
    """`source_field_ref` 带块 ID → 解析出页与区域。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    anchors.store_blocks(
        db, row,
        [anchors.make_block(
            document=row, page_no=2, block_id="b7", text="血糖 6.8",
            bbox={"x": 1, "y": 2, "w": 30, "h": 8},
        )],
        extraction_version=1,
    )
    db.commit()

    anchor = anchors.anchor_for_ref(db, row, "obs:0#b7", extraction_version=1)
    assert anchor is not None
    assert anchor["pageNo"] == 2
    assert anchor["blockId"] == "b7"
    assert anchor["anchorStatus"] == ANCHOR_REGION
    assert anchor["bbox"]["w"] == 30


def test_anchor_for_ref_without_block_id_returns_none(client, auth, db):
    """仅下标的 ref → **不猜测**位置（宁可无定位，不给错位置）。"""
    doc = _upload(client, auth)
    row = _doc(db, doc["id"])
    anchors.store_blocks(
        db, row,
        [anchors.make_block(document=row, page_no=1, block_id="b1", text="x")],
        extraction_version=1,
    )
    db.commit()
    assert anchors.anchor_for_ref(db, row, "obs:0", extraction_version=1) is None
    assert anchors.anchor_for_ref(db, row, None) is None
    assert anchors.anchor_for_ref(db, row, "obs:0#missing") is None
