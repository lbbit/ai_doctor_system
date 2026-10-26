"""文档上传 API 测试（AC-4.1/4.2/4.3、E-6/E-10、retry/archive/content）。"""

import pytest

from tests.conftest import make_pdf_with_text, make_png_bytes


def _upload(client, auth, content, name, subject=None, force=False, mime=None):
    data = {"subjectId": subject if subject is not None else auth["subjectId"]}
    if force:
        data["force"] = "true"
    return client.post(
        "/api/documents",
        headers=auth["headers"],
        data=data,
        files={"file": (name, content, mime or "image/png")},
    )


def test_upload_ok_201_pending(client, auth):
    """AC-4.1 上传 PNG → 201 + Document(status=pending)。"""
    resp = _upload(client, auth, make_png_bytes(), "report.png")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["code"] == 0
    doc = body["data"]["document"]
    assert doc["status"] == "pending"
    assert doc["kind"] == "IMAGE"
    assert doc["sizeBytes"] > 0
    assert doc["subjectId"] == auth["subjectId"]


def test_upload_pdf_kind(client, auth):
    resp = _upload(client, auth, make_pdf_with_text("GLU 5.2 mmol/L 3.9-6.1"),
                   "lab.pdf", mime="application/pdf")
    assert resp.status_code == 201
    assert resp.json()["data"]["document"]["kind"] == "PDF"


def test_upload_too_large_413(client, auth):
    """E-10 >20MB → 413 FILE_TOO_LARGE。"""
    big = b"%PDF-1.4" + b"0" * (21 * 1024 * 1024)
    resp = _upload(client, auth, big, "big.pdf", mime="application/pdf")
    assert resp.status_code == 413
    assert resp.json()["code"] == "FILE_TOO_LARGE"


def test_upload_unsupported_415(client, auth):
    """E-10 非白名单格式 → 415。"""
    resp = _upload(client, auth, b"GIF89a....", "a.gif", mime="image/gif")
    assert resp.status_code == 415
    assert resp.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_duplicate_409_and_force(client, auth):
    """AC-4.3 重复文件 → 409 DUP_FILE；force=true 可仍要上传。"""
    png = make_png_bytes()
    first = _upload(client, auth, png, "same.png")
    assert first.status_code == 201

    dup = _upload(client, auth, png, "same.png")
    assert dup.status_code == 409
    body = dup.json()
    assert body["code"] == "DUP_FILE"
    assert body["data"]["duplicate"] is True

    forced = _upload(client, auth, png, "same.png", force=True)
    assert forced.status_code == 201


def test_list_requires_subject(client, auth):
    resp = client.get("/api/documents", headers=auth["headers"])
    assert resp.status_code == 422
    resp2 = client.get(f"/api/documents?subjectId={auth['subjectId']}",
                       headers=auth["headers"])
    assert resp2.status_code == 200
    data = resp2.json()["data"]
    assert "items" in data and "total" in data


def test_list_filter_status(client, auth):
    _upload(client, auth, make_png_bytes(), "p1.png")
    resp = client.get(
        f"/api/documents?subjectId={auth['subjectId']}&status=pending",
        headers=auth["headers"],
    )
    assert resp.json()["data"]["total"] >= 1


def test_retry_only_failed(client, auth, db):
    """E-1 仅 failed 可重试；重试 → pending + 新 job。"""
    from app.db.models import Document

    doc = _upload(client, auth, make_png_bytes(), "r.png").json()["data"]["document"]
    resp = client.post(f"/api/documents/{doc['id']}/retry", headers=auth["headers"])
    assert resp.status_code == 409
    assert resp.json()["code"] == "STATE_INVALID"

    row = db.get(Document, doc["id"])
    row.status = "failed"
    row.failure_reason = "图片模糊"
    db.commit()

    ok = client.post(f"/api/documents/{doc['id']}/retry", headers=auth["headers"])
    assert ok.status_code == 200
    assert ok.json()["data"]["job"]["state"] == "queued"
    db.expire_all()
    assert db.get(Document, doc["id"]).status == "pending"


def test_archive_and_content(client, auth):
    """B-3 归档替代删除；AC-13.3 content 签名 URL 带过期。"""
    doc = _upload(client, auth, make_png_bytes(), "a.png").json()["data"]["document"]
    content = client.get(f"/api/documents/{doc['id']}/content",
                         headers=auth["headers"])
    assert content.status_code == 200
    data = content.json()["data"]
    assert data["url"] and data["expiresAt"]

    arch = client.post(f"/api/documents/{doc['id']}/archive", headers=auth["headers"])
    assert arch.status_code == 200
    assert arch.json()["data"]["status"] == "archived"

    # 无 DELETE 端点（B-3）
    assert client.delete(f"/api/documents/{doc['id']}",
                         headers=auth["headers"]).status_code == 405


def test_detail_includes_job(client, auth):
    doc = _upload(client, auth, make_png_bytes(), "d.png").json()["data"]["document"]
    resp = client.get(f"/api/documents/{doc['id']}", headers=auth["headers"])
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["document"]["id"] == doc["id"]
    assert data["job"] is not None and data["job"]["state"] == "queued"
