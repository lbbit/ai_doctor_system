"""Subject 管理 API 测试（AC-2.1/2.2/2.3、级联删除）。"""


def test_create_and_list(client, auth):
    """AC-2.1 可新建 Subject，列表即时刷新。"""
    resp = client.post(
        "/api/subjects",
        headers=auth["headers"],
        json={"name": "父亲", "relation": "父亲", "birthday": "1960-05-05",
              "gender": "male", "color": "#22C55E"},
    )
    assert resp.status_code == 200
    created = resp.json()["data"]
    assert created["name"] == "父亲" and created["relation"] == "父亲"

    lst = client.get("/api/subjects", headers=auth["headers"]).json()["data"]
    names = [s["name"] for s in lst]
    assert "本人" in names and "父亲" in names


def test_update_subject_audited(client, auth):
    resp = client.post("/api/subjects", headers=auth["headers"],
                       json={"name": "母亲", "relation": "母亲"})
    sid = resp.json()["data"]["id"]
    upd = client.patch(f"/api/subjects/{sid}", headers=auth["headers"],
                       json={"color": "#F59E0B"})
    assert upd.status_code == 200
    assert upd.json()["data"]["color"] == "#F59E0B"

    logs = client.get("/api/audit/logs?action=subject.update",
                      headers=auth["headers"]).json()["data"]
    assert logs["total"] >= 1
    entry = logs["items"][0]
    assert entry["before"]["color"] != "#F59E0B"
    assert entry["after"]["color"] == "#F59E0B"


def test_last_selected_persisted_on_login(client, auth):
    """AC-2.3 重新登录后默认回到上次选中的 Subject。"""
    resp = client.post("/api/subjects", headers=auth["headers"],
                       json={"name": "父亲", "relation": "父亲"})
    sid = resp.json()["data"]["id"]
    put = client.put("/api/subjects/last-selected", headers=auth["headers"],
                     json={"subjectId": sid})
    assert put.status_code == 200

    login = client.post("/api/auth/login",
                        json={"email": "tester@example.com",
                              "password": "password123"})
    assert login.json()["data"]["lastSubjectId"] == sid


def test_delete_cascades(client, auth, db):
    """删除 Subject：会话归档、文件软删、审计保留。"""
    from app.db.models import Conversation, Document, AuditLog, Subject
    from app.common.ids import new_id
    import hashlib

    # 建档 + 会话 + 文档
    sid = client.post("/api/subjects", headers=auth["headers"],
                      json={"name": "奶奶", "relation": "祖父母"}
                      ).json()["data"]["id"]
    conv = client.post("/api/chat/conversations", headers=auth["headers"],
                       json={"subjectId": sid}).json()["data"]

    doc = Document(
        id=new_id(), account_id=auth["account"]["id"], subject_id=sid,
        kind="IMAGE", status="pending", file_key="k", file_name="f.png",
        mime_type="image/png", size_bytes=5,
        content_hash=hashlib.sha256(b"xx").hexdigest(),
    )
    db.add(doc)
    db.commit()

    resp = client.delete(f"/api/subjects/{sid}", headers=auth["headers"])
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["archivedConversations"] == 1
    assert data["softDeletedFiles"] == 1

    db.expire_all()
    conv_row = db.get(Conversation, conv["id"])
    assert conv_row.status == "archived"
    doc_row = db.get(Document, doc.id)
    assert doc_row.deleted_at is not None
    # 审计保留
    assert db.query(AuditLog).filter_by(action="subject.delete").count() >= 1
    # 软删档案不出现在列表
    lst = client.get("/api/subjects", headers=auth["headers"]).json()["data"]
    assert "奶奶" not in [s["name"] for s in lst]


def test_stats_endpoint(client, auth):
    sid = client.post("/api/subjects", headers=auth["headers"],
                      json={"name": "统计对象", "relation": "其他"}
                      ).json()["data"]["id"]
    resp = client.get(f"/api/subjects/{sid}/stats", headers=auth["headers"])
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["eventCount"] == 0 and data["documentCount"] == 0
