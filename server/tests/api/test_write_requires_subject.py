"""写请求必须选档专项（AC-3.1/3.2/3.3 / K4 零串档）。"""

import hashlib


def test_upload_missing_subject_422(client, auth, db):
    """AC-3.2 无 subjectId 的写请求 → 422 SUBJECT_REQUIRED，不落库。"""
    from tests.conftest import make_png_bytes
    from app.db.models import Document

    before = db.query(Document).count()
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        files={"file": ("a.png", make_png_bytes(), "image/png")},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "SUBJECT_REQUIRED"
    db.expire_all()
    assert db.query(Document).count() == before  # AC-3.3 无新增行


def test_conversation_missing_subject_422(client, auth, db):
    """B-1 对话创建缺 subjectId → 422 SUBJECT_REQUIRED，不落库。"""
    from app.db.models import Conversation

    before = db.query(Conversation).count()
    resp = client.post("/api/chat/conversations", headers=auth["headers"], json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == "SUBJECT_REQUIRED"
    db.expire_all()
    assert db.query(Conversation).count() == before


def test_unknown_subject_404(client, auth, db):
    """subjectId 存在但非本人 → 404（K4 防 IDOR）。"""
    resp = client.post("/api/subjects", headers=auth["headers"],
                       json={"name": "别人", "relation": "其他"})
    resp2 = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": "01JXXXXXXXXXXXXXXXXXXXXXXX"},
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\n0000", "image/png")},
    )
    assert resp2.status_code == 404


def test_last_selected_requires_existing_subject(client, auth):
    resp = client.put("/api/subjects/last-selected", headers=auth["headers"],
                      json={"subjectId": "nope"})
    assert resp.status_code == 404
