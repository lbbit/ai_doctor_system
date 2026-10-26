"""SQL 趋势聚合（F-08；AC-8.1 确定性计算，A-6 数值不交给模型）。"""

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import require_subject
from app.common.errors import NotFoundError, ValidationError_
from app.db.models import (
    Account,
    IndicatorDictionary,
    IndicatorRefRange,
    Observation,
)

logger = logging.getLogger("app.trends")


def trend_series(
    db: Session,
    account: Account,
    subject_id: Optional[str],
    code: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """按 code 聚合近 N 次观测序列（<2 点返回 insufficient=true，AC-8.3）。"""
    if not code:
        raise ValidationError_("缺少指标 code")
    subject = require_subject(db, account, subject_id)
    limit = max(2, min(limit, 200))

    q = (
        select(Observation)
        .where(
            Observation.subject_id == subject.id,
            Observation.account_id == account.id,
            Observation.code == code,
            Observation.deleted_at.is_(None),
        )
        .order_by(Observation.occurred_at.desc(), Observation.recorded_at.desc())
        .limit(limit)
    )
    if date_from:
        q = q.where(Observation.occurred_at >= date_from)
    if date_to:
        q = q.where(Observation.occurred_at <= date_to)

    rows = list(db.execute(q).scalars())
    rows.reverse()  # 时间正序供折线

    dictionary = db.scalar(
        select(IndicatorDictionary).where(IndicatorDictionary.code == code)
    )
    name = dictionary.name_cn if dictionary else (rows[0].display_name if rows else code)
    unit = (dictionary.unit if dictionary else None) or (rows[0].unit if rows else None)

    # 参考区间带：字典最新版本 → 观测快照兜底
    ref_band = None
    if dictionary:
        rr = db.scalar(
            select(IndicatorRefRange)
            .where(IndicatorRefRange.dictionary_id == dictionary.id)
            .order_by(IndicatorRefRange.version.desc())
            .limit(1)
        )
        if rr is not None and (rr.low_value is not None or rr.high_value is not None):
            ref_band = {
                "low": float(rr.low_value) if rr.low_value is not None else None,
                "high": float(rr.high_value) if rr.high_value is not None else None,
                "text": rr.text_range,
            }
    if ref_band is None and rows:
        r0 = rows[-1]
        if r0.ref_low is not None or r0.ref_high is not None:
            ref_band = {
                "low": float(r0.ref_low) if r0.ref_low is not None else None,
                "high": float(r0.ref_high) if r0.ref_high is not None else None,
                "text": r0.ref_text,
            }

    points = [
        {
            "occurredAt": o.occurred_at.isoformat(),
            "valueNum": float(o.value_num) if o.value_num is not None else None,
            "valueText": o.value_text,
            "abnormalFlag": o.abnormal_flag,
            "refLow": float(o.ref_low) if o.ref_low is not None else None,
            "refHigh": float(o.ref_high) if o.ref_high is not None else None,
            "documentId": o.source_document_id,
            "observationId": o.id,
        }
        for o in rows
    ]
    numeric_points = [p for p in points if p["valueNum"] is not None]

    return {
        "code": code,
        "name": name,
        "unit": unit,
        "points": points,
        "refBand": ref_band,
        "insufficient": len(numeric_points) < 2,  # AC-8.3 数据不足成线
    }


def list_observations(
    db: Session,
    account: Account,
    subject_id: Optional[str],
    code: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    """观测列表/明细（分页）。"""
    from app.common.pagination import make_page

    subject = require_subject(db, account, subject_id)
    q = select(Observation).where(
        Observation.subject_id == subject.id,
        Observation.account_id == account.id,
        Observation.deleted_at.is_(None),
    )
    if code:
        q = q.where(Observation.code == code)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        db.execute(
            q.order_by(Observation.occurred_at.desc(), Observation.recorded_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": o.id,
            "code": o.code,
            "displayName": o.display_name,
            "valueNum": float(o.value_num) if o.value_num is not None else None,
            "valueText": o.value_text,
            "unit": o.unit,
            "refLow": float(o.ref_low) if o.ref_low is not None else None,
            "refHigh": float(o.ref_high) if o.ref_high is not None else None,
            "refText": o.ref_text,
            "abnormalFlag": o.abnormal_flag,
            "occurredAt": o.occurred_at.isoformat(),
            "recordedAt": o.recorded_at.isoformat() if o.recorded_at else None,
            "sourceDocumentId": o.source_document_id,
            "confidence": float(o.confidence) if o.confidence is not None else None,
            "isConfirmed": o.is_confirmed,
            "unmapped": o.code is None,
        }
        for o in rows
    ]
    return make_page(items, page, page_size, int(total))
