"""文档详情四区的后端只读端点（R1 T107）。

覆盖：
- /markdown：返回服务端已鉴权的副本正文（走 API 通道，避免 MinIO 直连 CORS）；
  无副本时 available=False（不报错）。
- /records：只返回**当前有效**（未软删）的已确认事件/观测；重抽取代后旧版本不在此列。
- /versions：草稿版本 + 资产版本 + 最近任务。
- 三者都做归属校验（跨 Account/Subject → 404）与生命周期校验（软删 → 404）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.common.ids import new_id
from app.db.models import Document, DocumentAsset, DerivedRecord, MedicalEvent, Observation
from tests.conftest import make_pdf_with_text, make_png_bytes


def _upload(client, auth, content=None, name="lab.pdf", mime="application/pdf", salt=b""):
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, content or make_pdf_with_text(
            "2026-03-12 Report\nGLU 5.2 mmol/L 3.9-6.1"
        ), mime)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


def _process(client, auth, db, doc_id):
    from app.db.models import ExtractionJob
    from app.preprocessing.pipeline import process_job

    job = db.query(ExtractionJob).filter_by(document_id=doc_id).one()
    process_job(db, job.id)
    db.expire_all()


def test_markdown_endpoint_returns_content(client, auth, db):
    doc = _upload(client, auth)
    _process(client, auth, db, doc["id"])

    resp = client.get(
        f"/api/documents/{doc['id']}/markdown",
        headers=auth["headers"],
        params={"subjectId": auth["subjectId"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["available"] is True
    assert data["role"] == "markdown"
    assert "content" in data and data["content"].startswith("---")


def test_markdown_endpoint_no_asset_available_false(client, auth, db):
    """未处理的文档无副本 → available=False（不报错）。"""
    doc = _upload(client, auth)  # 不 process
    resp = client.get(
        f"/api/documents/{doc['id']}/markdown",
        headers=auth["headers"],
        params={"subjectId": auth["subjectId"]},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is False


def test_records_endpoint_only_active_facts(client, auth, db):
    doc = _upload(client, auth)
    _process(client, auth, db, doc["id"])

    # 确认入库
    dr = db.query(DerivedRecord).filter_by(document_id=doc["id"]).one()
    obs_count = len(dr.payload.get("observations") or [])
    decisions = [{"fieldRef": f"obs:{i}", "action": "confirm"} for i in range(obs_count)]
    client.post(
        f"/api/documents/{doc['id']}/derived-record/confirm",
        headers=auth["headers"],
        json={"version": dr.version, "decisions": decisions},
    )
    db.expire_all()

    resp = client.get(
        f"/api/documents/{doc['id']}/records",
        headers=auth["headers"],
        params={"subjectId": auth["subjectId"]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["observations"]) == obs_count
    assert len(data["events"]) >= 1


def test_versions_endpoint_lists_drafts_and_assets(client, auth, db):
    doc = _upload(client, auth)
    _process(client, auth, db, doc["id"])

    resp = client.get(
        f"/api/documents/{doc['id']}/versions",
        headers=auth["headers"],
        params={"subjectId": auth["subjectId"]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["drafts"]) >= 1
    assert data["drafts"][0]["version"] >= 1
    roles = {a["role"] for a in data["assets"]}
    assert "markdown" in roles


def test_all_three_cross_account_404(client, auth, second_auth, db):
    doc = _upload(client, auth)
    _process(client, auth, db, doc["id"])
    for path in ("markdown", "records", "versions"):
        resp = client.get(
            f"/api/documents/{doc['id']}/{path}", headers=second_auth["headers"]
        )
        assert resp.status_code == 404, path


def test_all_three_soft_deleted_404(client, auth, db):
    doc = _upload(client, auth)
    _process(client, auth, db, doc["id"])
    row = db.get(Document, doc["id"])
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    for path in ("markdown", "records", "versions"):
        resp = client.get(
            f"/api/documents/{doc['id']}/{path}",
            headers=auth["headers"],
            params={"subjectId": auth["subjectId"]},
        )
        assert resp.status_code == 404, path
