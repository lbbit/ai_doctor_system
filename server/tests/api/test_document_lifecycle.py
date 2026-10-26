"""文档生命周期安全：软删/归档后的读取、写入与后台再处理必须一致拒绝。

背景（2026-10-26 静态审计，R0 T003）：
- `app/auth/deps.py::owned_or_404` 只校验 Account/Subject 归属，未过滤 `deleted_at`，
  导致被软删（档案删除、重复上传顶替）的文档仍可按 ID 读取详情、签名原件、
  取草稿、确认入库与重抽。
- `app/preprocessing/pipeline.py::process_job` 与 `app/worker.py` 的补偿/租约回收
  未排除软删文档，可能为已删除记录重新生成草稿或改写状态。

期望语义（与“原件不可物理删除、审计保留”一致）：
- 软删文档对所有读写接口一律 404（不泄露存在性，与 K4 跨档语义一致）。
- 后台任务遇到软删文档时任务转 cancelled，不生成新草稿、不改写文档状态。
- 归档（archived）文档仍可主动查看原件与历史，不等于删除。
"""

from datetime import datetime, timezone

import pytest

from app.common.ids import new_id
from app.db.models import Document, DerivedRecord, ExtractionJob, Observation
from tests.conftest import make_png_bytes


def _upload(client, auth, name="life.png"):
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, make_png_bytes(), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


def _soft_delete(db, doc_id):
    row = db.get(Document, doc_id)
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.expire_all()


# --------------------------------------------------------------------------
# 读路径：软删后一律 404
# --------------------------------------------------------------------------


def test_soft_deleted_document_detail_404(client, auth, db):
    """软删文档详情 → 404（不泄露存在性）。"""
    doc = _upload(client, auth)
    _soft_delete(db, doc["id"])

    resp = client.get(f"/api/documents/{doc['id']}", headers=auth["headers"])
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_soft_deleted_document_content_404(client, auth, db):
    """软删文档签名链接 → 404，不能再拿到原件 URL。"""
    doc = _upload(client, auth)
    _soft_delete(db, doc["id"])

    resp = client.get(f"/api/documents/{doc['id']}/content", headers=auth["headers"])
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_soft_deleted_document_pages_404(client, auth, db):
    """软删文档页图列表 → 404。"""
    doc = _upload(client, auth)
    _soft_delete(db, doc["id"])

    resp = client.get(f"/api/documents/{doc['id']}/pages", headers=auth["headers"])
    assert resp.status_code == 404


def test_soft_deleted_document_derived_record_404(client, auth, db):
    """软删文档草稿 → 404。"""
    doc = _upload(client, auth)
    _soft_delete(db, doc["id"])

    resp = client.get(
        f"/api/documents/{doc['id']}/derived-record", headers=auth["headers"]
    )
    assert resp.status_code == 404


def test_soft_deleted_document_retry_404(client, auth, db):
    """软删文档重试 → 404（不得重新入队处理已删原件）。"""
    doc = _upload(client, auth)
    row = db.get(Document, doc["id"])
    row.status = "failed"
    row.failure_reason = "图片模糊"
    db.commit()
    _soft_delete(db, doc["id"])

    resp = client.post(f"/api/documents/{doc['id']}/retry", headers=auth["headers"])
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_soft_deleted_document_reextract_404(client, auth, db):
    """软删文档重新抽取 → 404。"""
    doc = _upload(client, auth)
    row = db.get(Document, doc["id"])
    row.status = "awaiting_review"
    db.commit()
    _soft_delete(db, doc["id"])

    resp = client.post(
        f"/api/documents/{doc['id']}/derived-record/reextract", headers=auth["headers"]
    )
    assert resp.status_code == 404


def test_soft_deleted_document_confirm_does_not_write(client, auth, db):
    """软删文档确认入库 → 404，且不得产生任何 Observation/事件（K4 同级要求）。"""
    from tests.api.test_review import _seed_review_doc

    doc, dr = _seed_review_doc(db, auth["account"]["id"], auth["subjectId"])
    _soft_delete(db, doc.id)

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
    assert resp.status_code == 404
    db.expire_all()
    assert db.query(Observation).filter_by(source_document_id=doc.id).count() == 0


def test_soft_deleted_document_not_in_list(client, auth, db):
    """软删文档不出现在列表（既有行为保持）。"""
    doc = _upload(client, auth)
    _soft_delete(db, doc["id"])

    resp = client.get(
        f"/api/documents?subjectId={auth['subjectId']}", headers=auth["headers"]
    )
    assert resp.status_code == 200
    ids = [d["id"] for d in resp.json()["data"]["items"]]
    assert doc["id"] not in ids


# --------------------------------------------------------------------------
# 后台任务：软删文档不得被再处理
# --------------------------------------------------------------------------


def test_process_job_cancelled_for_soft_deleted_document(db, auth):
    """worker 处理软删文档 → job cancelled，不生成草稿、不改状态。"""
    from app.preprocessing.pipeline import process_job

    doc = Document(
        id=new_id(),
        account_id=auth["account"]["id"],
        subject_id=auth["subjectId"],
        kind="IMAGE",
        status="pending",
        file_key="objects/x",
        file_name="a.png",
        mime_type="image/png",
        size_bytes=10,
        content_hash=new_id(),
        deleted_at=datetime.now(timezone.utc),
    )
    job = ExtractionJob(id=new_id(), document_id=doc.id, state="queued", attempt=0)
    db.add_all([doc, job])
    db.commit()

    process_job(db, job.id)

    db.expire_all()
    job_row = db.get(ExtractionJob, job.id)
    doc_row = db.get(Document, doc.id)
    assert job_row.state == "cancelled"
    assert doc_row.status == "pending"  # 不得被改成 extracting/failed
    assert db.query(DerivedRecord).filter_by(document_id=doc.id).count() == 0


def test_recover_stale_jobs_skips_soft_deleted_documents(db, auth, fake_queue):
    """租约回收不得为软删文档重新入队。"""
    from app.worker import recover_stale_jobs

    now = datetime.now(timezone.utc)
    doc = Document(
        id=new_id(),
        account_id=auth["account"]["id"],
        subject_id=auth["subjectId"],
        kind="IMAGE",
        status="extracting",
        file_key="objects/x",
        file_name="a.png",
        mime_type="image/png",
        size_bytes=10,
        content_hash=new_id(),
        deleted_at=now,
    )
    job = ExtractionJob(
        id=new_id(),
        document_id=doc.id,
        state="running",
        attempt=0,
        started_at=now,
        lease_until=now.replace(year=now.year - 1),
    )
    db.add_all([doc, job])
    db.commit()

    stats = recover_stale_jobs(db)

    db.expire_all()
    assert stats["requeued"] == 0
    assert stats["failed"] == 0
    assert fake_queue == []
    job_row = db.get(ExtractionJob, job.id)
    assert job_row.state == "cancelled"
    assert db.get(Document, doc.id).status == "extracting"  # 不改写已删记录状态


# --------------------------------------------------------------------------
# 归档（archived）≠ 删除：仍可主动查看
# --------------------------------------------------------------------------


def test_archived_document_still_readable(client, auth):
    """归档文档仍可查看详情与原件（归档不是删除）。"""
    doc = _upload(client, auth)
    arch = client.post(f"/api/documents/{doc['id']}/archive", headers=auth["headers"])
    assert arch.status_code == 200
    assert arch.json()["data"]["status"] == "archived"

    detail = client.get(f"/api/documents/{doc['id']}", headers=auth["headers"])
    assert detail.status_code == 200
    content = client.get(f"/api/documents/{doc['id']}/content", headers=auth["headers"])
    assert content.status_code == 200
    assert content.json()["data"]["url"]
