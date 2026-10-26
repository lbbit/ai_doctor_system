"""文档来源元数据与发生时间（R1 T101，API 层）。

关键语义：
1. 上传可携带用户填写的发生日期/记录类型/机构/科室；
2. **用户填写的日期不被重新抽取覆盖**（用户输入优先）；
3. 未填日期的文档 `timePrecision=unknown`，**不伪造**发生时间；
4. 非法日期输入降级为 unknown 而非报错或臆造；
5. 元数据可按发生时间检索排序（R2 检索的元数据通道基础）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.common import timeline as tl
from app.db.models import Document
from tests.conftest import make_png_bytes


def _upload(client, auth, name="meta.png", salt=b"", **fields):
    data = {"subjectId": auth["subjectId"]}
    for k, v in fields.items():
        if v is not None:
            data[k] = str(v)
    return client.post(
        "/api/documents",
        headers=auth["headers"],
        data=data,
        files={"file": (name, make_png_bytes(salt), "image/png")},
    )


def test_upload_accepts_user_supplied_metadata(client, auth):
    """上传可携带发生日期/类型/机构/科室。"""
    resp = _upload(
        client, auth,
        occurredAt="2026-03-12", timePrecision="day",
        recordType="lab_report", facility="市第一医院", department="检验科",
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()["data"]["document"]
    assert doc["timePrecision"] == "day"
    assert doc["recordType"] == "lab_report"
    assert doc["facility"] == "市第一医院"
    assert doc["department"] == "检验科"
    assert doc["occurredStart"] is not None
    assert doc["occurredStart"].startswith("2026-03-12")


def test_upload_without_date_is_unknown_not_faked(client, auth):
    """未填日期 → unknown，不得把上传时间当发生时间。"""
    resp = _upload(client, auth)
    assert resp.status_code == 201
    doc = resp.json()["data"]["document"]
    assert doc["timePrecision"] == "unknown"
    assert doc["occurredStart"] is None
    assert doc["occurredEnd"] is None


def test_upload_with_invalid_date_degrades_to_unknown(client, auth):
    """非法日期输入降级为 unknown，不报错也不臆造。"""
    resp = _upload(client, auth, occurredAt="not-a-date")
    assert resp.status_code == 201
    doc = resp.json()["data"]["document"]
    assert doc["timePrecision"] == "unknown"
    assert doc["occurredStart"] is None


def test_month_precision_is_accepted(client, auth):
    """月精度：区间覆盖整月。"""
    resp = _upload(client, auth, occurredAt="2026-03", timePrecision="month")
    assert resp.status_code == 201
    doc = resp.json()["data"]["document"]
    assert doc["timePrecision"] == "month"
    start = datetime.fromisoformat(doc["occurredStart"])
    end = datetime.fromisoformat(doc["occurredEnd"])
    assert (end - start).days == 31  # 2026-03 有 31 天


def test_range_precision_accepted(client, auth):
    """区间（住院期间）：两个端点都必填。"""
    resp = _upload(
        client, auth, occurredAt="2026-01-05..2026-01-12", timePrecision="range"
    )
    assert resp.status_code == 201
    doc = resp.json()["data"]["document"]
    assert doc["timePrecision"] == "range"
    assert doc["occurredStart"] < doc["occurredEnd"]


def test_user_supplied_date_not_overwritten_by_extraction(client, auth, db):
    """重新抽取**不得**覆盖用户填写的发生日期与类型。"""
    from app.documents.service import apply_extracted_metadata

    resp = _upload(
        client, auth, salt=b"a",
        occurredAt="2026-03-12", timePrecision="day",
        recordType="lab_report", facility="市第一医院",
    )
    doc_id = resp.json()["data"]["document"]["id"]

    db.expire_all()
    row = db.get(Document, doc_id)
    original_start = row.occurred_start

    apply_extracted_metadata(
        row, occurred_at="2020-01-01", record_type="other", facility="别家医院"
    )
    db.commit()

    db.expire_all()
    row = db.get(Document, doc_id)
    assert row.occurred_start == original_start
    assert row.record_type == "lab_report"
    assert row.facility == "市第一医院"


def test_extraction_fills_metadata_when_user_left_blank(client, auth, db):
    """用户未填时，抽取出的元数据可写入。"""
    from app.documents.service import apply_extracted_metadata

    resp = _upload(client, auth, salt=b"b")
    doc_id = resp.json()["data"]["document"]["id"]

    db.expire_all()
    row = db.get(Document, doc_id)
    apply_extracted_metadata(
        row, occurred_at="2024-06-01", record_type="visit_record", facility="三院"
    )
    db.commit()

    db.expire_all()
    row = db.get(Document, doc_id)
    assert row.record_type == "visit_record"
    assert row.facility == "三院"
    assert row.occurred_start is not None
    assert row.time_precision == "day"


def test_list_can_order_and_filter_by_occurred_time(client, auth, db):
    """按发生时间排序：最近的在前（而非按上传时间）。"""
    early = _upload(
        client, auth, name="early.png", salt=b"e", occurredAt="2020-01-15"
    ).json()["data"]["document"]
    late = _upload(
        client, auth, name="late.png", salt=b"l", occurredAt="2025-06-20"
    ).json()["data"]["document"]

    resp = client.get(
        f"/api/documents?subjectId={auth['subjectId']}&pageSize=50",
        headers=auth["headers"],
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    order = [d["id"] for d in items]
    # 发生时间较晚的排在前面（"最近发生"在前）
    assert order.index(late["id"]) < order.index(early["id"])


def test_list_puts_unknown_time_last(client, auth):
    """无发生时间的记录排在最后（不与已知时间的记录混排）。"""
    known = _upload(
        client, auth, name="known.png", salt=b"k", occurredAt="2024-03-12"
    ).json()["data"]["document"]
    unknown = _upload(client, auth, name="unknown.png", salt=b"u").json()["data"][
        "document"
    ]

    resp = client.get(
        f"/api/documents?subjectId={auth['subjectId']}&pageSize=50",
        headers=auth["headers"],
    )
    order = [d["id"] for d in resp.json()["data"]["items"]]
    assert order.index(known["id"]) < order.index(unknown["id"])


def test_document_out_exposes_metadata_fields(client, auth):
    """详情返回全部元数据字段（前端可分列展示）。"""
    resp = _upload(client, auth, occurredAt="2026-03-12", recordType="lab_report")
    doc_id = resp.json()["data"]["document"]["id"]
    detail = client.get(f"/api/documents/{doc_id}", headers=auth["headers"])
    doc = detail.json()["data"]["document"]
    for field in (
        "recordType", "occurredStart", "occurredEnd", "timePrecision",
        "reportedAt", "facility", "department", "sourceNote",
    ):
        assert field in doc, field
