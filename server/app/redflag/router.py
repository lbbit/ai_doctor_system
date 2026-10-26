"""红旗规则路由（F-11）：/api/redflag/*（§4.2 36-39）；命中落库 helper。"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import get_current_account
from app.common.errors import ConflictError, NotFoundError
from app.common.ids import new_id
from app.common.responses import ok_response
from app.db.models import Account, RedFlagHit, RedFlagRule
from app.db.session import get_db
from app.redflag import loader
from app.redflag.engine import match_rules
from app.redflag.schemas import RuleCreate, RuleOut, RuleTestRequest, RuleUpdate

logger = logging.getLogger("app.redflag")
router = APIRouter(prefix="/api/redflag", tags=["redflag"])


def rule_out(r: RedFlagRule) -> dict:
    return {
        "id": r.id,
        "code": r.code,
        "name": r.name,
        "category": r.category,
        "keywords": list(r.keywords or []),
        "keywordLogic": r.keyword_logic,
        "contextLogic": r.context_logic,
        "severity": r.severity,
        "message": r.message,
        "action": r.action,
        "enabled": r.enabled,
        "version": r.version,
    }


def record_hits(
    db: Session,
    *,
    account: Account,
    subject_id: str,
    conversation_id: Optional[str],
    message_id: Optional[str],
    hits: List[dict],
    request=None,
) -> List[dict]:
    """命中落库 red_flag_hits + 审计 action=redflag.hit（AC-11.2/A-1）。"""
    persisted = []
    for hit in hits:
        row = RedFlagHit(
            id=new_id(),
            rule_id=hit["ruleId"],
            message_id=message_id,
            conversation_id=conversation_id,
            subject_id=subject_id,
            account_id=account.id,
            matched_terms=hit.get("matchedTerms") or [],
        )
        db.add(row)
        audit_record(
            db,
            action="redflag.hit",
            resource_type="rule",
            resource_id=hit["ruleId"],
            account_id=account.id,
            subject_id=subject_id,
            after={"code": hit["code"], "severity": hit["severity"],
                   "messageId": message_id},
            request=request,
        )
        persisted.append({**hit, "hitId": row.id})
    return persisted


@router.get("/rules")
def list_rules(
    request: Request,
    enabled: Optional[bool] = None,
    account: Account = Depends(get_current_account),  # noqa: ARG001
    db: Session = Depends(get_db),
):
    """规则列表（管理页）。"""
    q = select(RedFlagRule)
    if enabled is not None:
        q = q.where(RedFlagRule.enabled.is_(enabled))
    rows = db.execute(q.order_by(RedFlagRule.category, RedFlagRule.code)).scalars()
    return ok_response([rule_out(r) for r in rows], request)


@router.post("/rules")
def create_rule(
    data: RuleCreate,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """新建规则（热更新）。"""
    exists = db.scalar(select(RedFlagRule).where(RedFlagRule.code == data.code))
    if exists:
        raise ConflictError(message=f"规则码 {data.code} 已存在")
    rule = RedFlagRule(
        id=new_id(),
        code=data.code,
        name=data.name,
        category=data.category,
        keywords=data.keywords,
        keyword_logic=data.keywordLogic,
        context_logic=data.contextLogic,
        severity=data.severity,
        message=data.message,
        action=data.action,
        enabled=data.enabled,
        version=1,
    )
    db.add(rule)
    audit_record(
        db,
        action="rule.create",
        resource_type="rule",
        resource_id=rule.id,
        account_id=account.id,
        after={"code": rule.code, "severity": rule.severity},
        request=request,
    )
    db.commit()
    db.refresh(rule)
    loader.invalidate()
    return ok_response(rule_out(rule), request)


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: str,
    data: RuleUpdate,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """修改/启停（version+1，AC-11.3 不发版生效）。"""
    rule = db.get(RedFlagRule, rule_id)
    if rule is None:
        raise NotFoundError("规则不存在")
    before = rule_out(rule)
    payload = data.model_dump(exclude_unset=True, by_alias=True)
    mapping = {
        "name": "name", "category": "category", "keywords": "keywords",
        "keywordLogic": "keyword_logic", "contextLogic": "context_logic",
        "severity": "severity", "message": "message", "action": "action",
        "enabled": "enabled",
    }
    changed = False
    for api_key, attr in mapping.items():
        if api_key in payload:
            setattr(rule, attr, payload[api_key])
            changed = True
    if changed:
        rule.version += 1  # 热更新版本推进
    audit_record(
        db,
        action="rule.update",
        resource_type="rule",
        resource_id=rule.id,
        account_id=account.id,
        before=before,
        after=rule_out(rule),
        request=request,
    )
    db.commit()
    db.refresh(rule)
    loader.invalidate()
    return ok_response(rule_out(rule), request)


@router.post("/rules/test")
def test_rules(
    data: RuleTestRequest,
    request: Request,
    account: Account = Depends(get_current_account),  # noqa: ARG001
    db: Session = Depends(get_db),
):
    """规则试跑（文本 → 命中预览，不落库）。"""
    rules = loader.get_rules()
    if not rules:  # 冷启动兜底（未跑 seed 或缓存为空）
        rules = list(db.execute(select(RedFlagRule)).scalars())
    hits = match_rules(rules, data.text)
    return ok_response(
        {"hits": [{"code": h["code"], "message": h["message"],
                   "severity": h["severity"]} for h in hits]},
        request,
    )
