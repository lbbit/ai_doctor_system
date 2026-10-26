"""状态机集成测试（PRD §9.2 Document 全转移 / 非法转移 E_STATE_INVALID）。"""

import pytest

from app.common.errors import StateInvalidError
from app.documents.service import DOC_TRANSITIONS, assert_transition


def test_legal_edges():
    """状态机简表合法边。"""
    legal = [
        ("pending", "extracting"),
        ("pending", "failed"),
        ("extracting", "awaiting_review"),
        ("extracting", "failed"),
        ("extracting", "parsed_no_data"),
        ("awaiting_review", "confirmed"),
        ("awaiting_review", "parsed_no_data"),
        ("failed", "pending"),
        ("confirmed", "awaiting_review"),
        ("confirmed", "archived"),
        ("pending", "archived"),
        ("awaiting_review", "archived"),
        ("parsed_no_data", "archived"),
        ("failed", "archived"),
    ]
    for src, dst in legal:
        assert_transition(src, dst)  # 不抛即通过


def test_illegal_edges_raise():
    illegal = [
        ("pending", "confirmed"),          # 未抽取不能直接确认
        ("confirmed", "confirmed"),        # 重复确认（幂等拦截）
        ("awaiting_review", "pending"),
        ("archived", "pending"),           # 归档后不可复活
        ("parsed_no_data", "confirmed"),
        ("failed", "confirmed"),
    ]
    for src, dst in illegal:
        with pytest.raises(StateInvalidError) as exc:
            assert_transition(src, dst)
        assert exc.value.code == "STATE_INVALID"
        assert exc.value.http_status == 409


def test_archived_is_terminal():
    assert DOC_TRANSITIONS["archived"] == set()


def test_retry_failure_state_path(client, auth, db):
    """E-1：failed → 用户重试 → pending（经 API 实测）。"""
    from tests.conftest import make_png_bytes
    from app.db.models import Document

    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("f.png", make_png_bytes(), "image/png")},
    )
    doc_id = resp.json()["data"]["document"]["id"]
    row = db.get(Document, doc_id)
    row.status = "failed"
    db.commit()
    out = client.post(f"/api/documents/{doc_id}/retry", headers=auth["headers"])
    assert out.status_code == 200
    db.expire_all()
    assert db.get(Document, doc_id).status == "pending"
