"""HITL 确认入库（F-06）：字段级决策、乐观锁 409、幂等唯一、状态机、审计。

AC-6.1~6.4 / E-7 / B-4 / B-5 落点（§3.3：preprocessing/review.py）。
"""

import logging
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import select_owned
from app.common.errors import (
    NotFoundError,
    ReviewConflictError,
    StateInvalidError,
    ValidationError_,
)
from app.common.ids import new_id
from app.db.models import (
    Account,
    DerivedRecord,
    Document,
    ExtractionJob,
    MedicalEvent,
    MedicalEventDocument,
    Observation,
)
from app.documents.service import assert_transition
from app.documents.state import (
    content_of,
    processing_of,
    set_processing_status,
)
from app.preprocessing.queue import enqueue_extract
from app.preprocessing.schemas import ConfirmRequest, DecisionIn

logger = logging.getLogger("app.review")

_DOC_TYPE_EVENT_TITLE = {
    "lab_report": "化验检查",
    "imaging_report": "影像检查",
    "visit_record": "门诊就诊",
    "other": "健康报告",
}


def get_derived_record(
    db: Session,
    account: Account,
    doc_id: str,
    subject_id: Optional[str] = None,
) -> dict:
    """取草稿（确认卡片数据）+ 置信度摘要 + 与上一版差异；跨档案 → 404。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    dr = _current_dr(db, document)
    if dr is None:
        raise NotFoundError("暂无抽取草稿")
    summary = _confidence_summary(dr.payload)
    previous = db.scalar(
        select(DerivedRecord)
        .where(
            DerivedRecord.document_id == document.id,
            DerivedRecord.version < dr.version,
        )
        .order_by(DerivedRecord.version.desc())
        .limit(1)
    )
    return {
        "id": dr.id,
        "version": dr.version,
        "payload": dr.payload,
        "parser": dr.parser,
        "confidenceSummary": summary,
        "comparison": _compare_versions(previous, dr) if previous else None,
    }


def _obs_identity(obs: dict) -> str:
    """跨版本对齐指标：优先标准 code，否则用原文名。"""
    return (obs.get("code") or obs.get("raw_name") or "").strip()


def _usable_observations(payload: dict) -> list:
    """参与统计/比对的指标：排除排版噪声（T1/年0/每3）。"""
    from app.preprocessing.indicator_aligner import is_noise_text

    return [
        o
        for o in (payload.get("observations") or [])
        if not (is_noise_text(str(o.get("raw_name") or "")) and not o.get("code"))
    ]


def _compare_versions(previous: DerivedRecord, current: DerivedRecord) -> dict:
    """重新抽取后的差异摘要（用户判断“这版是否更完整”）。"""
    prev_items = {_obs_identity(o): o for o in _usable_observations(previous.payload)}
    curr_items = {_obs_identity(o): o for o in _usable_observations(current.payload)}
    added = [k for k in curr_items if k not in prev_items]
    removed = [k for k in prev_items if k not in curr_items]
    changed = [
        k
        for k in curr_items
        if k in prev_items
        and str(curr_items[k].get("value")) != str(prev_items[k].get("value"))
    ]
    return {
        "previousVersion": previous.version,
        "previousObservationCount": len(prev_items),
        "currentObservationCount": len(curr_items),
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "addedNames": [
            curr_items[k].get("display_name") or curr_items[k].get("raw_name")
            for k in added
        ][:20],
        "removedNames": [
            prev_items[k].get("display_name") or prev_items[k].get("raw_name")
            for k in removed
        ][:20],
    }


def _current_dr(db: Session, document: Document) -> Optional[DerivedRecord]:
    return db.scalar(
        select(DerivedRecord)
        .where(DerivedRecord.document_id == document.id)
        .order_by(DerivedRecord.version.desc())
        .limit(1)
    )


def _confidence_summary(payload: dict) -> dict:
    obs = _usable_observations(payload)
    events = payload.get("events") or []
    confs = [float(o.get("confidence") or 0) for o in obs]
    low = [o.get("raw_name") for o in obs if float(o.get("confidence") or 0) < 0.85]
    return {
        "observationCount": len(obs),
        "eventCount": len(events),
        "min": min(confs) if confs else None,
        "max": max(confs) if confs else None,
        "belowThreshold": low,
        "mock": bool(payload.get("mock", False)),
    }


def _parse_ref(v: str) -> tuple[str, int]:
    kind, idx = v.split(":", 1)
    return kind, int(idx)


def _supersede_previous_confirmations(
    db: Session,
    document: Document,
    *,
    current_draft_id: str,
    account: Account,
    request=None,
) -> dict:
    """确认新版本前，软删该文档旧的已确认事件/观测（R1 T106）。

    调用时机：新确认的事件/观测**尚未写入**，因此此刻所有"当前有效"
    （未软删）的、挂在本文档上的事件/观测都属于**上一版确认**。把它们软删
    （保留行与审计），新确认的随后成为唯一有效事实。

    为什么软删而非物理删：已确认事实是临床证据，必须可追溯（B-3/B-5）；
    取代链通过 `deleted_at` + 审计表达，不抹历史。
    """
    now = datetime.now(timezone.utc)
    old_events = list(
        db.execute(
            select(MedicalEvent).where(
                MedicalEvent.document_id == document.id,
                MedicalEvent.deleted_at.is_(None),
            )
        ).scalars()
    )
    old_obs = list(
        db.execute(
            select(Observation).where(
                Observation.source_document_id == document.id,
                Observation.deleted_at.is_(None),
            )
        ).scalars()
    )
    for ev in old_events:
        ev.deleted_at = now
    for ob in old_obs:
        ob.deleted_at = now
    if old_events or old_obs:
        db.flush()
        audit_record(
            db,
            action="record.supersede",
            resource_type="document",
            resource_id=document.id,
            account_id=account.id,
            subject_id=document.subject_id,
            before={
                "activeEventIds": [e.id for e in old_events],
                "activeObservationIds": [o.id for o in old_obs],
            },
            after={
                "supersededEvents": len(old_events),
                "supersededObservations": len(old_obs),
                "newDraftId": current_draft_id,
            },
            request=request,
        )
    return {
        "events": len(old_events),
        "observations": len(old_obs),
    }


def confirm_derived_record(
    db: Session,
    account: Account,
    doc_id: str,
    data: ConfirmRequest,
    request=None,
    subject_id: Optional[str] = None,
) -> dict:
    """HITL 确认入库（P0 全部强制决策）；跨档案 → 404。"""
    document = select_owned(db, account, Document, doc_id, subject_id)
    dr = _current_dr(db, document)

    # ---- 版本/状态校验（E-7 并发确认 409 REVIEW_CONFLICT）----
    if dr is None:
        raise NotFoundError("暂无抽取草稿")
    if data.version != dr.version:
        raise ReviewConflictError()
    current_processing = processing_of(document)
    if current_processing == "processed" and content_of(document) == "confirmed":
        raise ReviewConflictError("该报告已确认入库，请刷新查看")
    if current_processing != "awaiting_review":
        raise StateInvalidError(f"当前状态 {current_processing} 不可确认入库")

    payload = dr.payload
    events = payload.get("events") or []
    observations = payload.get("observations") or []

    # ---- 决策完整性（P0 全强制）----
    decisions: dict[str, DecisionIn] = {d.fieldRef: d for d in data.decisions}
    required = [f"event:{i}" for i in range(len(events))] + [
        f"obs:{i}" for i in range(len(observations))
    ]
    missing = [ref for ref in required if ref not in decisions]
    unknown = [ref for ref in decisions if ref not in required]
    if missing:
        raise ValidationError_(f"存在未决策字段：{', '.join(missing)}")
    if unknown:
        raise ValidationError_(f"未知字段决策：{', '.join(unknown)}")

    # 双时间轴基准（B-2）：报告内检查日期 → 文件日期兜底
    report_date = _to_date(payload.get("report_date")) or _to_date(
        document.created_at.date().isoformat()
    ) or date.today()

    created_events: List[MedicalEvent] = []
    created_obs: List[Observation] = []
    applied_decisions: dict[str, dict] = {}

    # ---- R1 T106：确认新版本前，原子取代旧的已确认事实 ----
    # 同一文档"重新抽取 → 确认新版本"时，旧确认的事件/观测软删保留（审计可追溯），
    # 新确认的成为当前有效事实。不静默改写、不撞幂等唯一约束。
    superseded = _supersede_previous_confirmations(
        db, document, current_draft_id=dr.id, account=account, request=request
    )

    # ---- 事件决策 ----
    for i, ev_raw in enumerate(events):
        ref = f"event:{i}"
        decision = decisions[ref]
        if decision.action == "reject":
            applied_decisions[ref] = {"action": "reject"}
            continue
        ev = dict(ev_raw)
        if decision.action == "correct" and isinstance(decision.value, dict):
            ev.update({k: v for k, v in decision.value.items() if v is not None})
        occurred = _to_date(ev.get("occurred_at")) or report_date
        event = MedicalEvent(
            id=new_id(),
            account_id=account.id,
            subject_id=document.subject_id,
            event_type=ev.get("type") or "other",
            occurred_at=occurred,
            occurred_at_precision="day",
            recorded_at=datetime.now(timezone.utc),
            title=ev.get("title") or _DOC_TYPE_EVENT_TITLE.get(document.doc_type or "", "健康报告"),
            narrative=ev.get("narrative"),
            diagnosis=ev.get("diagnosis"),
            department=ev.get("department"),
            event_source="document",
            document_id=document.id,
            draft_id=dr.id,  # B-4 幂等：同文档同草稿唯一
        )
        db.add(event)
        created_events.append(event)
        applied_decisions[ref] = {"action": decision.action}

    db.flush()  # 拿到 event.id

    # 主事件（观测挂靠）：优先第一个事件，否则由报告合成
    primary_event = created_events[0] if created_events else None
    if primary_event is None and (observations or payload.get("events") == []):
        # 全部事件被拒绝但有观测：合成承载事件（AC-6.2 确认生成 MedicalEvent）
        if observations and not events:
            primary_event = MedicalEvent(
                id=new_id(),
                account_id=account.id,
                subject_id=document.subject_id,
                event_type="checkup" if document.doc_type == "lab_report" else "other",
                occurred_at=report_date,
                occurred_at_precision="day",
                recorded_at=datetime.now(timezone.utc),
                title=_DOC_TYPE_EVENT_TITLE.get(document.doc_type or "", "健康报告"),
                narrative=f"来自报告《{document.file_name}》的结构化确认",
                event_source="document",
                document_id=document.id,
                draft_id=f"{dr.id}#synthetic",
            )
            db.add(primary_event)
            db.flush()
            created_events.append(primary_event)

    # 事件 ↔ 证据 refs
    for event in created_events:
        db.add(
            MedicalEventDocument(
                event_id=event.id, document_id=document.id, ref_note="hitl_confirm"
            )
        )

    # ---- 观测决策 ----
    for i, obs_raw in enumerate(observations):
        ref = f"obs:{i}"
        decision = decisions[ref]
        if decision.action == "reject":
            obs_raw["status"] = "rejected"
            applied_decisions[ref] = {"action": "reject"}
            continue
        obs = dict(obs_raw)
        if decision.action == "correct" and isinstance(decision.value, dict):
            obs.update({k: v for k, v in decision.value.items() if v is not None})
            # 用户改了指标名：重新匹配字典，避免“修正了名字却仍显示未命中”
            if "display_name" in decision.value or "raw_name" in decision.value:
                obs = _remap_after_rename(db, obs)
        value = obs.get("value")
        value_num = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else (
            _as_float(value)
        )
        value_text = None if value_num is not None else (
            str(value) if value is not None else None
        )
        occurred = _to_date(obs.get("occurred_at")) or report_date
        row = Observation(
            id=new_id(),
            account_id=account.id,
            subject_id=document.subject_id,
            event_id=primary_event.id if primary_event else None,
            dictionary_id=obs.get("dictionary_id"),
            code=obs.get("code"),
            display_name=obs.get("display_name") or obs.get("raw_name"),
            value_num=value_num,
            value_text=value_text,
            unit=obs.get("unit"),
            ref_low=_as_float(obs.get("ref_low")),
            ref_high=_as_float(obs.get("ref_high")),
            ref_text=obs.get("ref_text"),
            abnormal_flag=obs.get("abnormal_flag"),
            occurred_at=occurred,
            recorded_at=datetime.now(timezone.utc),
            source_document_id=document.id,
            source_field_ref=ref,  # B-4 幂等唯一（document+field）
            confidence=_as_float(obs.get("confidence")),
            is_confirmed=True,
        )
        db.add(row)
        created_obs.append(row)
        obs_raw["status"] = "confirmed" if decision.action == "confirm" else "corrected"
        applied_decisions[ref] = {"action": decision.action}

    # ---- 状态机转移（§3.3；处理状态与生命周期正交）----
    has_content = bool(created_events or created_obs)
    set_processing_status(
        document,
        "processed",
        content_status="confirmed" if has_content else "no_data",
    )
    document.review_version += 1
    dr.confirmed_at = datetime.now(timezone.utc)
    payload["_decisions"] = applied_decisions
    dr.payload = payload
    # JSONB 嵌套变更需显式标脏（同一引用赋值不会触发 change tracking）
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(dr, "payload")

    # ---- 审计（B-5：前值/后值，AC-6.2）----
    audit = audit_record(
        db,
        action="record.confirm",
        resource_type="document",
        resource_id=document.id,
        account_id=account.id,
        subject_id=document.subject_id,
        before={
            "status": "awaiting_review",
            "processingStatus": "awaiting_review",
            "reviewVersion": document.review_version - 1,
            "decisions": {
                k: v.get("action") for k, v in applied_decisions.items()
            },
        },
        after={
            "status": document.status,
            "processingStatus": processing_of(document),
            "contentStatus": content_of(document),
            "reviewVersion": document.review_version,
            "superseded": superseded,
            "eventIds": [e.id for e in created_events],
            "observationIds": [o.id for o in created_obs],
        },
        request=request,
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ReviewConflictError("该报告已在其他页面处理（重复确认被幂等约束拦截）") from exc

    for o in created_obs:
        db.refresh(o)
    logger.info(
        "record.confirmed doc=%s status=%s events=%s obs=%s",
        document.id, document.status, len(created_events), len(created_obs),
    )
    return {
        "documentStatus": document.status,
        "processingStatus": processing_of(document),
        "contentStatus": content_of(document),
        "superseded": superseded,
        "events": [_event_out(e) for e in created_events],
        "observations": [_obs_out(o) for o in created_obs],
        "auditId": audit.id,
    }


def reextract(
    db: Session,
    account: Account,
    doc_id: str,
    request=None,
    subject_id: Optional[str] = None,
) -> dict:
    """重新抽取：入队异步任务（version 由 worker 落草稿时 +1）。

    历史实现是在请求内同步跑解析链：20 页 PDF 必然超过前端 60s 请求超时，
    用户只能看到失败的 network error（2026-10-21 实测）。改为复用 worker 链路后，
    进度、超时降级、租约回收与首次上传完全一致。
    """
    document = select_owned(db, account, Document, doc_id, subject_id)

    # 已在处理中 → 不重复入队（防止双击产生多份草稿）；先判此条，
    # 因为重新抽取会把状态提前置为 extracting。
    active = db.scalar(
        select(ExtractionJob)
        .where(
            ExtractionJob.document_id == document.id,
            ExtractionJob.state.in_(("queued", "running")),
        )
        .limit(1)
    )
    if active is not None:
        raise ReviewConflictError("该报告正在处理中，请等待当前任务完成")

    current_processing = processing_of(document)
    if current_processing not in ("awaiting_review", "failed", "processed"):
        raise StateInvalidError("当前状态不支持重新抽取")

    current = _current_dr(db, document)
    attempt = db.scalar(
        select(func.max(ExtractionJob.attempt)).where(
            ExtractionJob.document_id == document.id
        )
    )
    previous_status = current_processing
    set_processing_status(document, "processing")
    document.failure_reason = None
    job = ExtractionJob(
        id=new_id(),
        document_id=document.id,
        state="queued",
        attempt=int(attempt or 0) + 1,
        stage="queued",
    )
    db.add(job)
    audit_record(
        db,
        action="record.reextract",
        resource_type="document",
        resource_id=document.id,
        account_id=account.id,
        subject_id=document.subject_id,
        before={"status": previous_status, "version": current.version if current else 0},
        after={"status": "extracting", "nextVersion": (current.version if current else 0) + 1},
        request=request,
    )
    db.commit()
    db.refresh(job)
    enqueue_extract(job.id)

    return {
        "queued": True,
        "job": {
            "id": job.id,
            "documentId": job.document_id,
            "state": job.state,
            "attempt": job.attempt,
            "stage": job.stage,
            "progress": {"done": 0, "total": 0},
            "elapsedSeconds": 0,
            "stale": False,
            "parser": None,
            "error": None,
            "startedAt": None,
            "finishedAt": None,
        },
    }


# ---- helpers ----

def _to_date(value) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_float(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _event_out(e: MedicalEvent) -> dict:
    return {
        "id": e.id,
        "eventType": e.event_type,
        "title": e.title,
        "occurredAt": e.occurred_at.isoformat(),
        "recordedAt": e.recorded_at.isoformat() if e.recorded_at else None,
        "documentId": e.document_id,
    }


def _obs_out(o: Observation) -> dict:
    return {
        "id": o.id,
        "code": o.code,
        "displayName": o.display_name,
        "valueNum": float(o.value_num) if o.value_num is not None else None,
        "valueText": o.value_text,
        "unit": o.unit,
        "abnormalFlag": o.abnormal_flag,
        "occurredAt": o.occurred_at.isoformat(),
        "confidence": float(o.confidence) if o.confidence is not None else None,
    }


def _remap_after_rename(db: Session, obs: dict) -> dict:
    """用户修正指标名后重新查字典：命中则带出标准 code/名称/参考区间。

    未命中时保留用户输入（`display_name`），不静默丢弃，符合 E-3「原文可保留」。
    参考区间仅在用户未提供时补齐，避免覆盖报告原文。
    """
    from app.preprocessing.mapper import (
        _latest_ref_range,
        _to_number,
        load_dictionary_ensured,
        match_indicator,
        _compute_flag,
    )

    name = str(obs.get("display_name") or obs.get("raw_name") or "").strip()
    if not name:
        return obs
    entries = load_dictionary_ensured(db)
    entry = match_indicator(entries, name)
    if entry is None:
        obs["code"] = None
        obs["dictionary_id"] = None
        obs["display_name"] = name
        obs["unmapped"] = True
        return obs

    obs["code"] = entry.code
    obs["dictionary_id"] = entry.id
    obs["display_name"] = entry.name_cn
    obs["unmapped"] = False
    if not obs.get("unit") and entry.unit:
        obs["unit"] = entry.unit
    if obs.get("ref_low") is None and obs.get("ref_high") is None and not obs.get("ref_text"):
        rr = _latest_ref_range(db, entry.id)
        if rr is not None:
            obs["ref_low"] = float(rr.low_value) if rr.low_value is not None else None
            obs["ref_high"] = float(rr.high_value) if rr.high_value is not None else None
            obs["ref_text"] = rr.text_range
    obs["abnormal_flag"] = _compute_flag(
        _to_number(obs.get("value")),
        obs.get("ref_low"),
        obs.get("ref_high"),
        obs.get("abnormal_flag"),
    )
    return obs
