"""跨 Account 越权专项（K4/IDOR：一律 404，不泄露存在性）。"""

import hashlib


def _upload(client, auth, content: bytes, name="x.png") -> dict:
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, content, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


def test_document_detail_cross_account_404(client, auth, second_auth, db):
    from tests.conftest import make_png_bytes

    doc = _upload(client, auth, make_png_bytes())
    resp = client.get(f"/api/documents/{doc['id']}", headers=second_auth["headers"])
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_document_content_cross_account_404(client, auth, second_auth):
    from tests.conftest import make_png_bytes

    doc = _upload(client, auth, make_png_bytes())
    resp = client.get(f"/api/documents/{doc['id']}/content",
                      headers=second_auth["headers"])
    assert resp.status_code == 404


def test_subject_patch_cross_account_404(client, auth, second_auth):
    resp = client.post("/api/subjects", headers=auth["headers"],
                       json={"name": "私有档", "relation": "其他"})
    sid = resp.json()["data"]["id"]
    resp2 = client.patch(f"/api/subjects/{sid}", headers=second_auth["headers"],
                         json={"name": "篡改"})
    assert resp2.status_code == 404


def test_conversation_cross_account_404(client, auth, second_auth):
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": auth["subjectId"]}).json()["data"]
    resp = client.get(f"/api/chat/conversations/{conv['id']}",
                      headers=second_auth["headers"])
    assert resp.status_code == 404


def test_timeline_cross_account_isolated(client, auth, second_auth):
    """AC-2.2/9.3 另一账户读取时间线只看到自己的空档案。"""
    resp = client.get(f"/api/timeline/events?subjectId={auth['subjectId']}",
                      headers=second_auth["headers"])
    assert resp.status_code == 404  # 他档 subject 直接 404


def test_audit_isolation(client, auth, second_auth):
    """AC-14.4 审计只看本人。"""
    client.post("/api/subjects", headers=auth["headers"],
                json={"name": "记一笔", "relation": "其他"})
    logs = client.get("/api/audit/logs", headers=second_auth["headers"])
    assert logs.status_code == 200
    data = logs.json()["data"]
    # 只应看到本账户自身注册产生的审计，看不到他人 subject.create
    assert all(item["action"] != "subject.create" for item in data["items"])
    assert data["total"] <= 1
