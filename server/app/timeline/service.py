"""时间线服务（F-07）：事件流分组（年/月）、筛选、摘要、空档案引导数据。"""

import logging
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import require_subject
from app.common.errors import ValidationError_
from app.common.pagination import make_page
from app.db.models import Account, Document, MedicalEvent, Observation

logger = logging.getLogger("app.timeline")

EVENT_TYPES = (
    "visit", "hospitalization", "surgery", "checkup",
    "medication", "vaccine", "self_reported", "other",
)


def _base_query(account: Account, subject_id: str, types, date_from, date_to):
    q = select(MedicalEvent).where(
        MedicalEvent.subject_id == subject_id,
        MedicalEvent.account_id == account.id,
        MedicalEvent.deleted_at.is_(None),
    )
    if types:
        wanted = [t.strip() for t in types.split(",") if t.strip()]
        invalid = [t for t in wanted if t not in EVENT_TYPES]
        if invalid:
            raise ValidationError_(f"未知事件类型：{', '.join(invalid)}")
        q = q.where(MedicalEvent.event_type.in_(wanted))
    if date_from:
        q = q.where(MedicalEvent.occurred_at >= date_from)
    if date_to:
        q = q.where(MedicalEvent.occurred_at <= date_to)
    return q


def _event_out(db: Session, ev: MedicalEvent) -> dict:
    obs_count = (
        db.scalar(
            select(func.count(Observation.id)).where(
                Observation.event_id == ev.id,
                Observation.deleted_at.is_(None),
            )
        )
        or 0
    )
    return {
        "id": ev.id,
        "eventType": ev.event_type,
        "occurredAt": ev.occurred_at.isoformat(),
        "occurredAtPrecision": ev.occurred_at_precision,
        "recordedAt": ev.recorded_at.isoformat() if ev.recorded_at else None,
        "title": ev.title,
        "narrative": ev.narrative,
        "diagnosis": ev.diagnosis,
        "department": ev.department,
        "eventSource": ev.event_source,
        "documentId": ev.document_id,
        "observationCount": int(obs_count),
    }


def list_events(
    db: Session,
    account: Account,
    subject_id: Optional[str],
    types: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    """事件流：按 occurredAt 倒序分页，服务端按 年/月 分组（AC-7.1/7.2）。"""
    subject = require_subject(db, account, subject_id)
    q = _base_query(account, subject.id, types, date_from, date_to)

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        db.execute(
            q.order_by(MedicalEvent.occurred_at.desc(), MedicalEvent.recorded_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    groups: dict[tuple, list] = {}
    ordered_keys: list[tuple] = []
    for ev in rows:
        key = (ev.occurred_at.year, ev.occurred_at.month)
        if key not in groups:
            groups[key] = []
            ordered_keys.append(key)
        groups[key].append(_event_out(db, ev))

    result_groups = [
        {"year": y, "month": m, "items": groups[(y, m)]} for (y, m) in ordered_keys
    ]
    data = make_page(result_groups, page, page_size, int(total))
    data["groups"] = result_groups
    return data


def timeline_summary(db: Session, account: Account, subject_id: Optional[str]) -> dict:
    """时间线摘要：类型计数、时间范围、文档数（角标）；空档案返回零值（AC-7.3）。"""
    subject = require_subject(db, account, subject_id)

    type_rows = db.execute(
        select(MedicalEvent.event_type, func.count(MedicalEvent.id))
        .where(
            MedicalEvent.subject_id == subject.id,
            MedicalEvent.account_id == account.id,
            MedicalEvent.deleted_at.is_(None),
        )
        .group_by(MedicalEvent.event_type)
    ).all()
    type_counts = {t: int(c) for t, c in type_rows}

    range_row = db.execute(
        select(
            func.min(MedicalEvent.occurred_at),
            func.max(MedicalEvent.occurred_at),
        ).where(
            MedicalEvent.subject_id == subject.id,
            MedicalEvent.account_id == account.id,
            MedicalEvent.deleted_at.is_(None),
        )
    ).one()
    doc_count = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.subject_id == subject.id,
                Document.deleted_at.is_(None),
            )
        )
        or 0
    )
    return {
        "typeCounts": type_counts,
        "dateRange": {
            "from": range_row[0].isoformat() if range_row[0] else None,
            "to": range_row[1].isoformat() if range_row[1] else None,
        },
        "documentCount": int(doc_count),
        "eventCount": sum(type_counts.values()),
        "isEmpty": sum(type_counts.values()) == 0 and int(doc_count) == 0,
    }
