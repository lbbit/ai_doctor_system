"""K4 零串档：文档详情必须绑定当前 Subject（真实 E2E 发现的 P0 缺陷）。

复现（2026-10-21 浏览器实测）：
当前选中「测试成员A」（空档案），直接访问「本人」名下的已确认文档 URL，
页面正常显示文档名、状态与原件预览 —— 等于跨档案泄露他人报告。

设计依据：PRD §3 K4「跨人串档事故 = 0（零容忍）」、G3「一个 Account 管理多个
Subject，零串档」、ARCHITECTURE §4.1「越权一律 404」。
同 Account 内不同 Subject 也必须隔离：家庭管理员可以在成成员间切换，
但**当前选中的档案**决定可见范围。
"""

import hashlib

from app.common.ids import new_id
from app.db.models import Document, Subject


def _register(client, email):
    resp = client.post(
        "/api/auth/register", json={"email": email, "password": "password123"}
    )
    data = resp.json()["data"]
    return {"headers": {"Authorization": f"Bearer {data['token']}"}, "subject": data["defaultSubject"]}


def _make_doc(db, account_id, subject_id, status="awaiting_review"):
    doc = Document(
        id=new_id(), account_id=account_id, subject_id=subject_id,
        kind="PDF", status=status, file_key="objects/x", file_name="secret.pdf",
        mime_type="application/pdf", size_bytes=100,
        content_hash=hashlib.sha256(new_id().encode()).hexdigest(),
        doc_type="lab_report",
    )
    db.add(doc)
    db.commit()
    return doc


def test_document_detail_404_for_other_subject(client, auth, db):
    """用本人账号创建另一个成员，跨档案访问文档必须 404。"""
    from app.db.models import Account

    account = db.get(Account, auth["account"]["id"])
    other = Subject(
        id=new_id(), account_id=account.id, name="成员B", relation="父亲",
        is_default=False,
    )
    db.add(other)
    db.commit()

    doc = _make_doc(db, account.id, other.id)

    # 默认 subjectId=本人 → 访问成员B的文档，必须 404
    resp = client.get(
        f"/api/documents/{doc.id}?subjectId={auth['subjectId']}",
        headers=auth["headers"],
    )
    assert resp.status_code == 404, resp.text

    # 显式带上正确 subjectId 才允许访问（同 Account 内切换是合法场景）
    ok = client.get(
        f"/api/documents/{doc.id}?subjectId={other.id}", headers=auth["headers"]
    )
    assert ok.status_code == 200, ok.text


def test_derived_record_404_for_other_subject(client, auth, db):
    from app.db.models import Account, DerivedRecord

    account = db.get(Account, auth["account"]["id"])
    other = Subject(
        id=new_id(), account_id=account.id, name="成员C", relation="母亲",
        is_default=False,
    )
    db.add(other)
    db.commit()
    doc = _make_doc(db, account.id, other.id)
    db.add(
        DerivedRecord(
            id=new_id(), document_id=doc.id, subject_id=other.id, version=1,
            payload={"observations": [], "events": [], "mock": False}, parser="table",
        )
    )
    db.commit()

    resp = client.get(
        f"/api/documents/{doc.id}/derived-record?subjectId={auth['subjectId']}",
        headers=auth["headers"],
    )
    assert resp.status_code == 404, resp.text


def test_content_url_404_for_other_subject(client, auth, db):
    from app.db.models import Account

    account = db.get(Account, auth["account"]["id"])
    other = Subject(
        id=new_id(), account_id=account.id, name="成员D", relation="配偶",
        is_default=False,
    )
    db.add(other)
    db.commit()
    doc = _make_doc(db, account.id, other.id, status="confirmed")

    resp = client.get(
        f"/api/documents/{doc.id}/content?subjectId={auth['subjectId']}",
        headers=auth["headers"],
    )
    assert resp.status_code == 404, resp.text


def test_confirm_404_for_other_subject(client, auth, db):
    """跨档确认必须 404，不能把别人的草稿写进自己的时间线。"""
    from app.db.models import Account, DerivedRecord

    account = db.get(Account, auth["account"]["id"])
    other = Subject(
        id=new_id(), account_id=account.id, name="成员E", relation="女儿",
        is_default=False,
    )
    db.add(other)
    db.commit()
    doc = _make_doc(db, account.id, other.id)
    db.add(
        DerivedRecord(
            id=new_id(), document_id=doc.id, subject_id=other.id, version=1,
            payload={
                "report_date": "2026-03-12",
                "observations": [{
                    "raw_name": "血红蛋白", "display_name": "血红蛋白", "code": "HGB",
                    "value": 125, "unit": "g/L", "confidence": 0.9,
                }],
                "events": [], "mock": False,
            },
            parser="table",
        )
    )
    db.commit()

    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm?subjectId={auth['subjectId']}",
        headers=auth["headers"],
        json={"version": 1, "decisions": [{"fieldRef": "obs:0", "action": "confirm"}]},
    )
    assert resp.status_code == 404, resp.text
