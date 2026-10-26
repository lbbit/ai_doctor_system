"""抽取进度可观测性与崩溃恢复（用户反馈：看不到进度、一直卡在"抽取中"）。

缺陷背景（2026-10-21 实测）：
- `extraction_jobs.lease_until` 只写不读；worker 卡死/被 kill 后 job 永远 `running`，
  文档永远停在 `extracting`（库中存在 >2 小时的两条）。
- 详情接口只回 `state`，前端无法显示"第几页/共几页、已耗时多久"。
"""

from datetime import datetime, timedelta, timezone

from app.common.ids import new_id
from app.db.models import Document, ExtractionJob
from app.worker import recover_stale_jobs


def _make_doc(db, subject_id, account_id, status="extracting"):
    doc = Document(
        id=new_id(),
        account_id=account_id,
        subject_id=subject_id,
        kind="PDF",
        doc_type="lab_report",
        status=status,
        file_key=f"objects/{account_id}/{subject_id}/x/original",
        file_name="report.pdf",
        mime_type="application/pdf",
        size_bytes=100,
        content_hash=new_id(),
        page_count=20,
    )
    db.add(doc)
    db.commit()
    return doc


def _make_job(db, doc, state="running", lease_delta_seconds=-60, attempt=0):
    now = datetime.now(timezone.utc)
    job = ExtractionJob(
        id=new_id(),
        document_id=doc.id,
        state=state,
        attempt=attempt,
        started_at=now - timedelta(minutes=10),
        lease_until=now + timedelta(seconds=lease_delta_seconds),
    )
    db.add(job)
    db.commit()
    return job


def test_job_out_exposes_progress(db, auth):
    """详情接口必须暴露阶段/进度/耗时（前端据此显示进度）。"""
    from app.db.models import Account
    from app.documents.service import get_document_detail

    doc = _make_doc(db, auth["subjectId"], auth["account"]["id"])
    job = _make_job(db, doc, state="running", lease_delta_seconds=120)
    job.stage = "extracting"
    job.progress_done = 3
    job.progress_total = 20
    db.commit()

    acct = db.get(Account, auth["account"]["id"])
    detail = get_document_detail(db, acct, doc.id)

    assert detail["job"]["stage"] == "extracting"
    assert detail["job"]["progress"] == {"done": 3, "total": 20}
    assert detail["job"]["elapsedSeconds"] >= 0
    assert detail["job"]["stale"] is False


def test_recover_expired_running_job_requeues(db, auth, fake_queue):
    """租约过期的 running job → 标记失败 + 文档回 pending + 重新入队（可继续，不无限卡）。"""
    doc = _make_doc(db, auth["subjectId"], auth["account"]["id"])
    job = _make_job(db, doc, state="running", lease_delta_seconds=-60)

    stats = recover_stale_jobs(db)

    db.expire_all()
    assert stats["requeued"] == 1
    assert db.get(Document, doc.id).status == "pending"
    assert db.get(ExtractionJob, job.id).state == "failed"
    assert db.get(ExtractionJob, job.id).error
    assert len(fake_queue) == 1


def test_recover_respects_attempt_limit(db, auth, fake_queue):
    """已达最大尝试次数 → 直接标记 failed，文档 failed 并给出可读原因。"""
    from app.config.settings import settings

    doc = _make_doc(db, auth["subjectId"], auth["account"]["id"])
    # attempt=1 且 extract_max_attempts=2 → 再重试会超过上限
    _make_job(db, doc, state="running", lease_delta_seconds=-60,
              attempt=settings.extract_max_attempts - 1)

    stats = recover_stale_jobs(db)

    db.expire_all()
    assert stats["failed"] == 1
    doc_row = db.get(Document, doc.id)
    assert doc_row.status == "failed"
    assert "中断" in (doc_row.failure_reason or "")
    assert fake_queue == []


def test_recover_ignores_live_lease(db, auth, fake_queue):
    """租约未过期的 running job 不动（避免抢活）。"""
    doc = _make_doc(db, auth["subjectId"], auth["account"]["id"])
    _make_job(db, doc, state="running", lease_delta_seconds=300)

    stats = recover_stale_jobs(db)

    db.expire_all()
    assert stats == {"requeued": 0, "failed": 0}
    assert db.get(Document, doc.id).status == "extracting"
    assert fake_queue == []


def test_recover_orphan_extracting_document(db, auth, fake_queue):
    """文档处于 extracting 但没有任何在跑的 job（worker 崩溃前没写 job）→ 也要回收。"""
    doc = _make_doc(db, auth["subjectId"], auth["account"]["id"])
    job = _make_job(db, doc, state="succeeded", lease_delta_seconds=-600)

    stats = recover_stale_jobs(db)

    db.expire_all()
    assert stats["requeued"] == 1
    assert db.get(Document, doc.id).status == "pending"
    assert len(fake_queue) == 1
    assert db.get(ExtractionJob, job.id).state == "succeeded"  # 历史 job 不改写
