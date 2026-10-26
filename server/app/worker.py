"""worker 入口：`python -m app.worker`（与 api 同代码不同入口，§1.1）。

- 主循环：BRPOP queue:extract → process_job（分类→解析→映射→写 DerivedRecord）
- 补偿：周期扫描 DB 中滞留的 queued 任务（Redis 信号丢失时仍可推进，DB 为事实源）
- 租约回收：running 但 lease_until 已过期的任务（worker 崩溃/被杀）标记失败并回 pending；
  历史缺陷是 lease_until 只写不读，导致文档永久卡在 extracting（2026-10-21 实测）。
- 心跳：worker_id + lease 写入 extraction_jobs（崩溃任务可回收）
"""

import logging
import os
import signal
import socket
import sys
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.ids import new_id
from app.common.logging import setup_logging
from app.config.settings import settings
from app.db.models import Document, ExtractionJob
from app.db.session import SessionLocal, get_session
from app.preprocessing.pipeline import process_job
from app.preprocessing.queue import dequeue_extract, enqueue_extract

logger = logging.getLogger("app.worker")

_worker_id = f"{socket.gethostname()}:{os.getpid()}"
_stop = False


def _handle_signal(signum, frame):  # noqa: ANN001, ARG001
    global _stop
    _stop = True
    logger.info("worker.shutdown signal=%s", signum)


def _sweep_stale_jobs(max_age_seconds: int = 10) -> None:
    """DB 事实源补偿：滞留 queued 的任务重新入队/直接处理。"""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    with get_session() as db:
        stale = (
            db.execute(
                select(ExtractionJob)
                .where(
                    ExtractionJob.state == "queued",
                    ExtractionJob.created_at < cutoff,
                )
                .order_by(ExtractionJob.created_at)
                .limit(10)
            )
            .scalars()
            .all()
        )
        for job in stale:
            enqueue_extract(job.id)


def recover_stale_jobs(db: Session, limit: int = 20) -> dict:
    """回收租约过期的在跑任务与无任务在跑的 extracting 文档。

    返回 {"requeued": n, "failed": m}：
    - 未达最大尝试次数 → 旧 job 标记 failed，文档回 pending，新建 job 入队重试；
    - 已达上限 → 文档 failed，并写入可读的失败原因（前端显示后用户可手动重试）。
    """
    now = datetime.now(timezone.utc)
    stats = {"requeued": 0, "failed": 0}

    expired = (
        db.execute(
            select(ExtractionJob)
            .where(
                ExtractionJob.state == "running",
                ExtractionJob.lease_until.is_not(None),
                ExtractionJob.lease_until < now,
            )
            .order_by(ExtractionJob.started_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for job in expired:
        if _recover_one(db, job, now, stats):
            stats["requeued"] += 1

    # 孤儿文档：extracting 但没有任何 running/queued job（进程在写 job 前崩溃）。
    # 正常流程里 process_job 会把 document.status=extracting 与 job.state=running 放在同一事务，
    # 所以“extracting 且无在跑任务”必然是中断残留，可安全回收。
    busy_ids = set(
        db.execute(
            select(ExtractionJob.document_id).where(
                ExtractionJob.state.in_(("running", "queued"))
            )
        )
        .scalars()
        .all()
    )
    orphans = (
        db.execute(
            select(Document)
            .where(
                Document.status == "extracting",
                Document.deleted_at.is_(None),
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for doc in orphans:
        if doc.id in busy_ids:
            continue
        history = db.scalar(
            select(ExtractionJob)
            .where(ExtractionJob.document_id == doc.id)
            .order_by(ExtractionJob.created_at.desc())
            .limit(1)
        )
        synthetic = ExtractionJob(
            id=new_id(),
            document_id=doc.id,
            state="running",
            attempt=history.attempt if history else 0,
        )
        db.add(synthetic)
        db.flush()
        if _recover_one(db, synthetic, now, stats):
            stats["requeued"] += 1

    if stats["requeued"] or stats["failed"]:
        logger.warning("worker.recovered %s", stats)
    return stats


def _recover_one(db: Session, job: ExtractionJob, now: datetime, stats: dict) -> bool:
    """单个超时 job 的回收决策；返回是否重新入队。"""
    doc = db.get(Document, job.document_id)
    max_attempts = settings.extract_max_attempts
    error = "抽取任务中断（worker 退出或超时），已自动回收"
    job.state = "failed"
    job.error = error
    job.stage = "failed"
    job.finished_at = now

    if doc is None or doc.status in ("confirmed", "archived", "parsed_no_data"):
        db.commit()
        return False

    if job.attempt + 1 >= max_attempts:
        doc.status = "failed"
        doc.failure_reason = (
            f"{error}；已达最大尝试次数（{max_attempts}），请手动输入或稍后重试"
        )
        stats["failed"] += 1
        db.commit()
        logger.warning("worker.recover_failed doc=%s job=%s", doc.id, job.id)
        return False

    doc.status = "pending"
    doc.failure_reason = None
    next_job = ExtractionJob(
        id=new_id(),
        document_id=doc.id,
        state="queued",
        attempt=job.attempt + 1,
    )
    db.add(next_job)
    db.commit()
    from app.preprocessing.queue import enqueue_extract as _enqueue

    _enqueue(next_job.id)
    logger.info(
        "worker.recover_requeue doc=%s oldJob=%s newJob=%s attempt=%s",
        doc.id, job.id, next_job.id, next_job.attempt,
    )
    return True


def _safe_process(job_id: str) -> None:
    db = SessionLocal()
    try:
        process_job(db, job_id)
    except Exception as exc:  # noqa: BLE001  单任务失败不拖垮 worker
        logger.exception("worker.job_error jobId=%s err=%s", job_id, exc)
        try:
            db.rollback()
            job = db.get(ExtractionJob, job_id)
            if job is not None and job.state in ("queued", "running"):
                job.state = "failed"
                job.error = f"worker error: {type(exc).__name__}: {exc}"[:2000]
                job.finished_at = datetime.now(timezone.utc)
                from app.db.models import Document

                doc = db.get(Document, job.document_id)
                if doc is not None and doc.status in ("extracting", "pending"):
                    doc.status = "failed"
                    doc.failure_reason = job.error
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("worker.fail_mark jobId=%s", job_id)
    finally:
        db.close()


def main() -> None:
    setup_logging(settings.log_level, settings.log_format)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    logger.info("worker.start id=%s queue=%s", _worker_id, settings.queue_extract)

    last_sweep = 0.0
    while not _stop:
        job_id = dequeue_extract(timeout=3)
        if job_id:
            _safe_process(job_id)
            continue
        # 空闲：每 10s 补偿扫描 + 租约回收
        now = time.monotonic()
        if now - last_sweep >= 10:
            last_sweep = now
            try:
                _sweep_stale_jobs()
                with get_session() as db:
                    recover_stale_jobs(db)
            except Exception:  # noqa: BLE001
                logger.exception("worker.sweep_error")
    logger.info("worker.stopped id=%s", _worker_id)


if __name__ == "__main__":
    sys.exit(main())
