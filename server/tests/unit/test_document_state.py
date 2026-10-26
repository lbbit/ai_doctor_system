"""处理状态与生命周期正交（R0 T004）。

背景：`documents.status` 此前同时表达"抽取进度"与"归档"，归档会覆盖
`confirmed`，且无法表达"已归档 · 待确认"这类组合。

期望语义：
1. `processing_status`（pending/processing/awaiting_review/processed/failed）与
   `lifecycle_status`（active/archived/deletion_pending）互不覆盖。
2. 归档不改变处理状态；取消归档可恢复。
3. 旧 `status` 列保持为派生投影，旧客户端行为不变。
4. 非法转移抛 STATE_INVALID；历史无法推断的行标 needs_reconcile，不猜 processed。
"""

from datetime import datetime, timezone

import pytest

from app.common.errors import StateInvalidError
from app.db.models import Document
from app.documents import state as st
from app.documents.service import doc_out
from tests.conftest import make_png_bytes


def _upload(client, auth, name="st.png"):
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, make_png_bytes(), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


# --------------------------------------------------------------------------
# 状态投影：不变量
# --------------------------------------------------------------------------


def test_legacy_projection_matches_new_columns():
    """旧 status 必须能由新状态完全确定（无歧义）。"""
    assert st.project_legacy_status(st.PENDING, None, st.LIFECYCLE_ACTIVE) == "pending"
    assert st.project_legacy_status(st.PROCESSING, None, st.LIFECYCLE_ACTIVE) == "extracting"
    assert (
        st.project_legacy_status(st.AWAITING_REVIEW, None, st.LIFECYCLE_ACTIVE)
        == "awaiting_review"
    )
    assert (
        st.project_legacy_status(st.PROCESSED, st.CONTENT_CONFIRMED, st.LIFECYCLE_ACTIVE)
        == "confirmed"
    )
    assert (
        st.project_legacy_status(st.PROCESSED, st.CONTENT_NO_DATA, st.LIFECYCLE_ACTIVE)
        == "parsed_no_data"
    )
    assert st.project_legacy_status(st.FAILED, None, st.LIFECYCLE_ACTIVE) == "failed"
    # 归档覆盖旧投影（保持旧客户端看到的 archived）
    assert (
        st.project_legacy_status(st.PROCESSED, st.CONTENT_CONFIRMED, st.LIFECYCLE_ARCHIVED)
        == "archived"
    )


def test_set_processing_status_syncs_legacy_column():
    """写新状态时旧 status 同步更新（唯一同步点）。"""
    doc = Document(
        id="d1", account_id="a", subject_id="s", kind="IMAGE", status="pending",
        file_key="k", file_name="f.png", mime_type="image/png", size_bytes=1,
        content_hash="h",
    )
    st.set_processing_status(doc, st.PROCESSING)
    assert doc.processing_status == st.PROCESSING
    assert doc.status == "extracting"

    st.set_processing_status(doc, st.AWAITING_REVIEW)
    assert doc.status == "awaiting_review"

    st.set_processing_status(doc, st.PROCESSED, content_status=st.CONTENT_CONFIRMED)
    assert doc.processing_status == st.PROCESSED
    assert doc.content_status == st.CONTENT_CONFIRMED
    assert doc.status == "confirmed"


def test_leaving_processed_clears_content_status():
    """离开 processed 必须清空 content_status（不得残留"已确认"）。"""
    doc = Document(
        id="d1", account_id="a", subject_id="s", kind="IMAGE", status="confirmed",
        file_key="k", file_name="f.png", mime_type="image/png", size_bytes=1,
        content_hash="h", processing_status=st.PROCESSED,
        content_status=st.CONTENT_CONFIRMED, lifecycle_status=st.LIFECYCLE_ACTIVE,
    )
    st.set_processing_status(doc, st.PROCESSING)
    assert doc.content_status is None
    assert doc.status == "extracting"


def test_illegal_processing_transition_raises():
    doc = Document(
        id="d1", account_id="a", subject_id="s", kind="IMAGE", status="pending",
        file_key="k", file_name="f.png", mime_type="image/png", size_bytes=1,
        content_hash="h", processing_status=st.PENDING,
        lifecycle_status=st.LIFECYCLE_ACTIVE,
    )
    with pytest.raises(StateInvalidError):
        st.set_processing_status(doc, st.PROCESSED)


def test_archived_row_needs_reconcile_not_guessed_processed():
    """历史 archived 行无法反推处理状态 → needs_reconcile，不得猜为 processed。"""
    doc = Document(
        id="d1", account_id="a", subject_id="s", kind="IMAGE", status="archived",
        file_key="k", file_name="f.png", mime_type="image/png", size_bytes=1,
        content_hash="h",
    )
    st.sync_states(doc)
    assert doc.lifecycle_status == st.LIFECYCLE_ARCHIVED
    assert doc.processing_status == st.PENDING
    assert doc.needs_reconcile is True
    assert doc.content_status is None


# --------------------------------------------------------------------------
# 归档不再覆盖处理状态
# --------------------------------------------------------------------------


def test_archive_does_not_change_processing_status(client, auth, db):
    """归档已确认文档 → 处理状态仍是 processed/confirmed，可恢复。"""
    from tests.api.test_review import _seed_review_doc

    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"],
        json={
            "version": 1,
            "decisions": [
                {"fieldRef": "event:0", "action": "confirm"},
                {"fieldRef": "obs:0", "action": "confirm"},
                {"fieldRef": "obs:1", "action": "reject"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    row = db.get(Document, doc.id)
    assert row.processing_status == st.PROCESSED
    assert row.content_status == st.CONTENT_CONFIRMED

    arch = client.post(f"/api/documents/{doc.id}/archive", headers=auth["headers"])
    assert arch.status_code == 200
    data = arch.json()["data"]
    # 旧客户端字段保持兼容
    assert data["status"] == "archived"
    # 新字段体现正交
    assert data["processingStatus"] == st.PROCESSED
    assert data["lifecycleStatus"] == st.LIFECYCLE_ARCHIVED

    db.expire_all()
    row = db.get(Document, doc.id)
    assert row.processing_status == st.PROCESSED
    assert row.content_status == st.CONTENT_CONFIRMED


def test_api_exposes_both_status_dimensions(client, auth):
    """详情与列表都返回两个维度，便于前端分列显示。"""
    doc = _upload(client, auth)
    detail = client.get(f"/api/documents/{doc['id']}", headers=auth["headers"])
    data = detail.json()["data"]["document"]
    assert data["processingStatus"] == st.PENDING
    assert data["lifecycleStatus"] == st.LIFECYCLE_ACTIVE
    assert data["status"] == "pending"

    listing = client.get(
        f"/api/documents?subjectId={auth['subjectId']}", headers=auth["headers"]
    )
    item = listing.json()["data"]["items"][0]
    assert "processingStatus" in item and "lifecycleStatus" in item
