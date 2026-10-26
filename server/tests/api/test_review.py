"""HITL 确认 API 测试（AC-6.1~6.4、E-7 并发 409、重新抽取）。"""

import hashlib

import pytest

from app.common.ids import new_id
from app.db.models import AuditLog, Document, DerivedRecord, MedicalEvent, Observation


def _seed_review_doc(db, account_id, subject_id, payload=None, version=1):
    doc = Document(
        id=new_id(), account_id=account_id, subject_id=subject_id,
        kind="PDF", status="awaiting_review", file_key="objects/x",
        file_name="lab.pdf", mime_type="application/pdf", size_bytes=100,
        content_hash=hashlib.sha256(new_id().encode()).hexdigest(),
        doc_type="lab_report", review_version=0,
    )
    db.add(doc)
    db.flush()
    payload = payload or {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [
            {"type": "checkup", "title": "年度体检", "occurred_at": "2026-03-12",
             "narrative": None, "diagnosis": None, "department": None,
             "confidence": 0.9}
        ],
        "observations": [
            {"raw_name": "血糖", "display_name": "空腹血糖", "code": "GLU_FASTING",
             "dictionary_id": None, "value": 7.2, "unit": "mmol/L",
             "ref_low": 3.9, "ref_high": 6.1, "ref_text": None,
             "abnormal_flag": "H", "occurred_at": "2026-03-12",
             "confidence": 0.9, "bbox": None, "status": "proposed"},
            {"raw_name": "神秘指标", "display_name": "神秘指标", "code": None,
             "dictionary_id": None, "value": "阴性", "unit": None,
             "ref_low": None, "ref_high": None, "ref_text": "阴性",
             "abnormal_flag": None, "occurred_at": "2026-03-12",
             "confidence": 0.4, "bbox": None, "status": "proposed"},
        ],
        "mock": False,
    }
    dr = DerivedRecord(
        id=new_id(), document_id=doc.id, subject_id=subject_id,
        version=version, payload=payload, parser="rule",
        overall_confidence=0.65,
    )
    db.add(dr)
    db.commit()
    return doc, dr


def test_derived_record_get(client, auth, db):
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.get(f"/api/documents/{doc.id}/derived-record",
                      headers=auth["headers"])
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["version"] == 1
    assert data["parser"] == "rule"
    assert data["confidenceSummary"]["observationCount"] == 2


def test_confirm_creates_event_and_observations(client, auth, db):
    """AC-6.2 确认/修改后生成 MedicalEvent + Observation，Document→confirmed，
    保存修改前后值（审计）。"""
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"],
        json={
            "version": 1,
            "decisions": [
                {"fieldRef": "event:0", "action": "confirm"},
                {"fieldRef": "obs:0", "action": "correct", "value": {"value": 6.8}},
                {"fieldRef": "obs:1", "action": "reject"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["documentStatus"] == "confirmed"
    assert len(data["events"]) == 1
    assert len(data["observations"]) == 1
    # correct 后以编辑值为准
    assert data["observations"][0]["valueNum"] == 6.8

    db.expire_all()
    row = db.get(Document, doc.id)
    assert row.status == "confirmed"
    assert row.review_version == 1
    obs = db.query(Observation).filter_by(source_document_id=doc.id).all()
    assert len(obs) == 1
    assert float(obs[0].value_num) == 6.8
    assert obs[0].is_confirmed is True
    assert db.query(AuditLog).filter_by(action="record.confirm").count() >= 1
    # 拒绝字段不入库（AC-6.3）
    assert all(o.display_name != "神秘指标" for o in obs)
    # payload 字段级状态同步
    dr_row = db.query(DerivedRecord).filter_by(document_id=doc.id).one()
    statuses = [o["status"] for o in dr_row.payload["observations"]]
    assert statuses == ["corrected", "rejected"]


def test_all_rejected_parsed_no_data(client, auth, db):
    """AC-6.3 全部拒绝 → status=parsed_no_data。"""
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"],
        json={
            "version": 1,
            "decisions": [
                {"fieldRef": "event:0", "action": "reject"},
                {"fieldRef": "obs:0", "action": "reject"},
                {"fieldRef": "obs:1", "action": "reject"},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["documentStatus"] == "parsed_no_data"
    db.expire_all()
    assert db.get(Document, doc.id).status == "parsed_no_data"
    assert db.query(Observation).filter_by(source_document_id=doc.id).count() == 0


def test_missing_decision_422(client, auth, db):
    """P0 全部字段必须显式决策。"""
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"],
        json={"version": 1, "decisions": [
            {"fieldRef": "event:0", "action": "confirm"},
        ]},
    )
    assert resp.status_code == 422
    assert "obs:0" in resp.json()["message"]


def test_version_conflict_409(client, auth, db):
    """E-7 版本冲突 → 409 REVIEW_CONFLICT。"""
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    resp = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"],
        json={"version": 99, "decisions": [
            {"fieldRef": "event:0", "action": "confirm"},
            {"fieldRef": "obs:0", "action": "confirm"},
            {"fieldRef": "obs:1", "action": "confirm"},
        ]},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "REVIEW_CONFLICT"


def test_double_confirm_conflict(client, auth, db):
    """AC-6.4 同一文档不可重复入库（二次确认 409）。"""
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    decisions = [
        {"fieldRef": "event:0", "action": "confirm"},
        {"fieldRef": "obs:0", "action": "confirm"},
        {"fieldRef": "obs:1", "action": "reject"},
    ]
    first = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"], json={"version": 1, "decisions": decisions},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/documents/{doc.id}/derived-record/confirm",
        headers=auth["headers"], json={"version": 1, "decisions": decisions},
    )
    assert second.status_code == 409
    db.expire_all()
    assert db.query(Observation).filter_by(source_document_id=doc.id).count() == 1


def test_reextract_bumps_version(client, auth, db, fake_storage, fake_queue):
    """§4.2 #19 重新抽取：异步入队，worker 处理产出 version+1 草稿。"""
    from app.preprocessing.pipeline import process_job
    from tests.conftest import make_pdf_with_text

    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    fake_storage.put(
        doc.file_key,
        make_pdf_with_text("2026-03-12 Report\nGLU 5.2 mmol/L 3.9-6.1"),
    )
    resp = client.post(f"/api/documents/{doc.id}/derived-record/reextract",
                       headers=auth["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["queued"] is True
    assert data["job"]["state"] == "queued"
    assert data["job"]["attempt"] == 1

    db.expire_all()
    assert db.get(Document, doc.id).status == "extracting"

    # worker 消费该任务 → 新草稿 v2
    process_job(db, data["job"]["id"])
    db.expire_all()
    row = db.get(Document, doc.id)
    assert row.status == "awaiting_review"
    versions = sorted(
        r.version for r in db.query(DerivedRecord).filter_by(document_id=doc.id).all()
    )
    assert versions == [1, 2]


def test_reextract_rejects_duplicate_queue(client, auth, db, fake_storage):
    """双击重新抽取：已有在跑任务 → 409，不重复入队。"""
    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    first = client.post(f"/api/documents/{doc.id}/derived-record/reextract",
                        headers=auth["headers"])
    assert first.status_code == 200
    second = client.post(f"/api/documents/{doc.id}/derived-record/reextract",
                         headers=auth["headers"])
    assert second.status_code == 409
    assert second.json()["code"] == "REVIEW_CONFLICT"
