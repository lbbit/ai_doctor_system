"""边缘情况加固：空文件、垃圾 JWT、HITL 跨档越权、无草稿确认。

期望行为均取自实现：documents/service.py(空文件)、auth/security.py(garbage→UNAUTHENTICATED)、
preprocessing/review.py(select_owned→404；dr None→404)。
"""

from tests.api.test_review import _seed_review_doc  # noqa: F401  复用种子 helper


def _upload(client, auth, content: bytes, name="x.png", mime="image/png"):
    return client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, content, mime)},
    )


def test_upload_empty_file_415(client, auth):
    """service L116：size==0 → UNSUPPORTED_MEDIA_TYPE。"""
    resp = _upload(client, auth, b"", "empty.pdf", mime="application/pdf")
    assert resp.status_code == 415
    assert resp.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_garbage_token_unauthenticated(client, auth):
    """decode_token：非过期类 JWTError → UNAUTHENTICATED（区别于 TOKEN_EXPIRED）。"""
    resp = client.get("/api/auth/me",
                      headers={"Authorization": "***"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


def test_derived_record_get_cross_account_404(client, auth, second_auth, db):
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get(f"/api/documents/{doc.id}/derived-record",
                      headers=second_auth["headers"])
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_confirm_cross_account_404(client, auth, second_auth, db):
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=second_auth["headers"],
        json={"version": 1, "decisions": [
            {"fieldRef": "event:0", "action": "reject"},
            {"fieldRef": "obs:0", "action": "reject"},
            {"fieldRef": "obs:1", "action": "reject"},
        ]},
    )
    assert resp.status_code == 404
    # K4：越权确认不得产生任何入库行（本档与彼档都不许）
    from app.db.models import Observation

    db.expire_all()
    assert db.query(Observation).filter_by(source_document_id=doc.id).count() == 0


def test_confirm_without_draft_404(client, auth):
    """pending 文档（worker 未处理、无 DerivedRecord）直接 confirm → 404。"""
    resp = _upload(client, auth, b"%PDF-1.4 x\ntrailer\n%%EOF")
    assert resp.status_code == 201
    doc_id = resp.json()["data"]["document"]["id"]
    resp2 = client.post(
        f"/api/documents/{doc_id}/derived-record/confirm",
        headers=auth["headers"],
        json={"version": 1, "decisions": [{"fieldRef": "event:0", "action": "reject"}]},
    )
    assert resp2.status_code == 404
    assert resp2.json()["code"] == "NOT_FOUND"
