"""字典查询/未命中反馈（F-08 选项、E-3 反馈队列归并）。"""

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import select_owned
from app.common.errors import ConflictError, NotFoundError, ValidationError_
from app.common.ids import new_id
from app.common.pagination import make_page
from app.db.models import Account, DictionaryMiss, IndicatorDictionary, Subject

logger = logging.getLogger("app.dictionary")


def trend_options(db: Session, account: Account) -> list:
    """TOP20 趋势指标选项（F-08；is_top_trend 标记来自 seed）。"""
    rows = db.execute(
        select(IndicatorDictionary)
        .where(
            IndicatorDictionary.active.is_(True),
            IndicatorDictionary.is_top_trend.is_(True),
        )
        .order_by(IndicatorDictionary.category, IndicatorDictionary.code)
    ).scalars()
    return [
        {
            "code": r.code,
            "name": r.name_cn,
            "unit": r.unit,
            "category": r.category,
        }
        for r in rows
    ]


def list_misses(
    db: Session,
    account: Account,
    resolved: Optional[bool],
    page: int,
    page_size: int,
) -> dict:
    """字典未命中队列（E-3；仅本账户档案）。"""
    q = select(DictionaryMiss).where(
        DictionaryMiss.subject_id.in_(
            select(Subject.id).where(Subject.account_id == account.id)
        )
    )
    if resolved is not None:
        q = q.where(DictionaryMiss.resolved.is_(resolved))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (
        db.execute(
            q.order_by(DictionaryMiss.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": m.id,
            "subjectId": m.subject_id,
            "documentId": m.document_id,
            "rawName": m.raw_name,
            "rawValue": m.raw_value,
            "rawUnit": m.raw_unit,
            "resolved": m.resolved,
            "resolvedToCode": m.resolved_to_code,
            "createdAt": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]
    return make_page(items, page, page_size, int(total))


def resolve_miss(
    db: Session,
    account: Account,
    miss_id: str,
    code: Optional[str],
    new_code: Optional[str],
    request=None,
) -> dict:
    """反馈归并到字典：code=并入既有指标（追加别名）；newCode=新建指标。"""
    miss = select_owned(db, account, DictionaryMiss, miss_id)

    if bool(code) == bool(new_code):
        raise ValidationError_("必须且只能提供 code 或 newCode 之一")

    before = {"rawName": miss.raw_name, "resolved": miss.resolved}

    if code:
        target = db.scalar(
            select(IndicatorDictionary).where(IndicatorDictionary.code == code)
        )
        if target is None:
            raise NotFoundError(f"指标码 {code} 不存在")
        aliases = list(target.aliases or [])
        if miss.raw_name and miss.raw_name not in aliases and miss.raw_name != target.name_cn:
            aliases.append(miss.raw_name)
            target.aliases = aliases
        miss.resolved = True
        miss.resolved_to_code = target.code
        after = {"mergedInto": target.code}
    else:
        exists = db.scalar(
            select(IndicatorDictionary).where(IndicatorDictionary.code == new_code)
        )
        if exists is not None:
            raise ConflictError(message=f"指标码 {new_code} 已存在，请改用 code 归并")
        entry = IndicatorDictionary(
            id=new_id(),
            code=new_code,
            name_cn=miss.raw_name,
            category="其他",
            unit=miss.raw_unit,
            aliases=[miss.raw_name] if miss.raw_name else [],
            is_top_trend=False,
            active=True,
        )
        db.add(entry)
        miss.resolved = True
        miss.resolved_to_code = new_code
        after = {"created": new_code}

    audit_record(
        db,
        action="dictionary.resolve",
        resource_type="dictionary_miss",
        resource_id=miss.id,
        account_id=account.id,
        subject_id=miss.subject_id,
        before=before,
        after=after,
        request=request,
    )
    db.commit()
    db.refresh(miss)
    return {
        "id": miss.id,
        "resolved": miss.resolved,
        "resolvedToCode": miss.resolved_to_code,
    }
