"""会话/消息 CRUD、归档状态机、草稿确认/丢弃（F-09/F-12；A-7/A-8）。"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import require_subject, select_owned
from app.common.errors import NotFoundError, StateInvalidError
from app.common.ids import new_id
from app.common.pagination import make_page
from app.db.models import (
    Account,
    Conversation,
    MedicalEvent,
    Message,
    MessageDraft,
    Observation,
    Subject,
)

logger = logging.getLogger("app.chat.service")

# 新建会话的占位标题；首轮回答后由 LLM 替换为语义标题（§16）
NEW_CONVERSATION_TITLE = "新会话"


def _conv_out(db: Session, conv: Conversation) -> dict:
    last_at = db.scalar(
        select(func.max(Message.created_at)).where(
            Message.conversation_id == conv.id
        )
    )
    return {
        "id": conv.id,
        "subjectId": conv.subject_id,
        "title": conv.title,
        "status": conv.status,
        "lastMessageAt": last_at.isoformat() if last_at else (
            conv.created_at.isoformat() if conv.created_at else None
        ),
    }


def list_conversations(
    db: Session, account: Account, status: Optional[str] = "active"
) -> List[dict]:
    """会话列表（?status=active；AC-2.2 仅本 Subject 数据经前端过滤传参）。"""
    q = select(Conversation).where(
        Conversation.account_id == account.id,
        Conversation.deleted_at.is_(None),
    )
    if status:
        q = q.where(Conversation.status == status)
    rows = db.execute(q.order_by(Conversation.updated_at.desc())).scalars()
    return [_conv_out(db, c) for c in rows]


def create_conversation(
    db: Session, account: Account, subject_id: str, title: Optional[str], request=None
) -> dict:
    """新建会话（绑定 subjectId，A-7）。"""
    subject = require_subject(db, account, subject_id)
    conv = Conversation(
        id=new_id(),
        account_id=account.id,
        subject_id=subject.id,
        # 占位标题：首轮回答后由 LLM 生成语义化短标题（§16）
        title=title or NEW_CONVERSATION_TITLE,
        status="active",
    )
    db.add(conv)
    audit_record(
        db,
        action="conversation.create",
        resource_type="conversation",
        resource_id=conv.id,
        account_id=account.id,
        subject_id=subject.id,
        after={"title": title},
        request=request,
    )
    db.commit()
    db.refresh(conv)
    return _conv_out(db, conv)


def get_owned_conversation(
    db: Session, account: Account, conv_id: str
) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.account_id != account.id or conv.deleted_at is not None:
        raise NotFoundError("会话不存在")
    return conv


def get_conversation_detail(db: Session, account: Account, conv_id: str) -> dict:
    """会话详情 + 消息 + 草稿。"""
    conv = get_owned_conversation(db, account, conv_id)
    messages = list(
        db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.asc())
        )
        .scalars()
    )
    msg_ids = [m.id for m in messages]
    drafts: List[MessageDraft] = []
    if msg_ids:
        drafts = list(
            db.execute(
                select(MessageDraft)
                .where(MessageDraft.message_id.in_(msg_ids))
                .order_by(MessageDraft.created_at.asc())
            ).scalars()
        )
    return {
        "conversation": _conv_out(db, conv),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "createdAt": m.created_at.isoformat() if m.created_at else None,
                "redFlagHitId": m.red_flag_hit_id,
                "errorState": m.error_state,
                "llmModel": m.llm_model,
            }
            for m in messages
        ],
        "drafts": [_draft_out(d) for d in drafts],
    }


def _draft_out(d: MessageDraft) -> dict:
    return {
        "id": d.id,
        "messageId": d.message_id,
        "subjectId": d.subject_id,
        "draftType": d.draft_type,
        "payload": d.payload,
        "status": d.status,
        "confirmedAt": d.confirmed_at.isoformat() if d.confirmed_at else None,
        "discardedAt": d.discarded_at.isoformat() if d.discarded_at else None,
        "resultEventId": d.result_event_id,
        "resultObservationIds": list(d.result_observation_ids or []),
    }


def close_conversation(
    db: Session, account: Account, conv_id: str, request=None
) -> dict:
    """归档会话（状态机 active → archived）。"""
    conv = get_owned_conversation(db, account, conv_id)
    if conv.status == "archived":
        return _conv_out(db, conv)
    conv.status = "archived"
    conv.archived_at = datetime.now(timezone.utc)
    audit_record(
        db,
        action="conversation.close",
        resource_type="conversation",
        resource_id=conv.id,
        account_id=account.id,
        subject_id=conv.subject_id,
        before={"status": "active"},
        after={"status": "archived"},
        request=request,
    )
    db.commit()
    db.refresh(conv)
    return _conv_out(db, conv)


def _get_owned_draft(db: Session, account: Account, draft_id: str) -> MessageDraft:
    draft = db.get(MessageDraft, draft_id)
    if draft is None:
        raise NotFoundError("草稿不存在")
    message = db.get(Message, draft.message_id)
    conv = db.get(Conversation, message.conversation_id) if message else None
    subject = db.get(Subject, draft.subject_id)
    if (
        conv is None
        or conv.account_id != account.id
        or subject is None
        or subject.account_id != account.id
    ):
        raise NotFoundError("草稿不存在")  # 跨 Account → 404（K4）
    return draft


def confirm_draft(
    db: Session,
    account: Account,
    draft_id: str,
    payload_override: Optional[dict],
    request=None,
) -> dict:
    """草稿确认入库（AC-12.2；编辑后以编辑值为准；二次确认 409）。"""
    draft = _get_owned_draft(db, account, draft_id)
    if draft.status != "pending":
        raise StateInvalidError("该草稿已处理（确认/丢弃仅可执行一次）")

    payload = payload_override if isinstance(payload_override, dict) else draft.payload
    event_spec = payload.get("event")
    obs_specs = payload.get("observations") or []

    from datetime import date as _date

    event_id = None
    if isinstance(event_spec, dict) and (
        event_spec.get("title") or event_spec.get("narrative")
    ):
        from app.chat.draft_parser import parse_relative_date

        occurred_raw = event_spec.get("occurredAt")
        try:
            occurred = _date.fromisoformat(str(occurred_raw)[:10])
            precision = event_spec.get("precision") or "day"
        except ValueError:
            occurred, precision = parse_relative_date(event_spec.get("expression"))
        etype = event_spec.get("type") or "self_reported"
        if etype not in ("visit", "hospitalization", "surgery", "checkup",
                         "medication", "vaccine", "self_reported", "other"):
            etype = "self_reported"
        event = MedicalEvent(
            id=new_id(),
            account_id=account.id,
            subject_id=draft.subject_id,
            event_type=etype,
            occurred_at=occurred,
            occurred_at_precision=precision if precision in
            ("day", "month", "year", "unknown") else "unknown",
            recorded_at=datetime.now(timezone.utc),  # AC-12.4 recordedAt=now
            title=event_spec.get("title") or "对话记录",
            narrative=event_spec.get("narrative"),
            diagnosis=event_spec.get("diagnosis"),
            department=event_spec.get("department"),
            event_source="chat",
            document_id=None,
            draft_id=draft.id,
        )
        db.add(event)
        db.flush()
        event_id = event.id

    observation_ids: List[str] = []
    for i, spec in enumerate(obs_specs):
        if not isinstance(spec, dict) or not spec.get("displayName"):
            continue
        from app.chat.draft_parser import parse_relative_date

        occurred_raw = spec.get("occurredAt")
        try:
            occurred = _date.fromisoformat(str(occurred_raw)[:10])
            precision = spec.get("precision") or "day"
        except ValueError:
            occurred, precision = parse_relative_date(spec.get("expression"))
        value = spec.get("value")
        value_num = None
        value_text = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value_num = float(value)
        elif isinstance(value, str):
            try:
                value_num = float(value.strip())
            except ValueError:
                value_text = value
        elif value is not None:
            value_text = str(value)

        obs = Observation(
            id=new_id(),
            account_id=account.id,
            subject_id=draft.subject_id,
            event_id=event_id,
            dictionary_id=None,  # 对话草稿不做字典映射（P1 可复用 mapper）
            code=None,
            display_name=spec.get("displayName"),
            value_num=value_num,
            value_text=value_text,
            unit=spec.get("unit"),
            ref_low=_f(spec.get("refLow")),
            ref_high=_f(spec.get("refHigh")),
            ref_text=spec.get("refText"),
            abnormal_flag=spec.get("abnormalFlag"),
            occurred_at=occurred,
            recorded_at=datetime.now(timezone.utc),
            source_document_id=None,
            source_field_ref=f"draft:{draft.id}:{i}",
            confidence=_f(spec.get("confidence")),
            is_confirmed=True,
        )
        db.add(obs)
        observation_ids.append(obs.id)

    draft.status = "confirmed"
    draft.confirmed_at = datetime.now(timezone.utc)
    draft.payload = payload
    draft.result_event_id = event_id
    draft.result_observation_ids = observation_ids

    audit_record(
        db,
        action="draft.confirm",
        resource_type="draft",
        resource_id=draft.id,
        account_id=account.id,
        subject_id=draft.subject_id,
        before={"status": "pending"},
        after={
            "status": "confirmed",
            "eventId": event_id,
            "observationIds": observation_ids,
        },
        request=request,
    )
    db.commit()
    logger.info("draft.confirmed id=%s event=%s obs=%s",
                draft.id, event_id, len(observation_ids))
    return {"eventId": event_id, "observationIds": observation_ids}


def discard_draft(
    db: Session, account: Account, draft_id: str, request=None
) -> dict:
    """丢弃草稿（AC-12.3：无孤儿数据，仅状态转移）。"""
    draft = _get_owned_draft(db, account, draft_id)
    if draft.status != "pending":
        raise StateInvalidError("该草稿已处理（确认/丢弃仅可执行一次）")
    draft.status = "discarded"
    draft.discarded_at = datetime.now(timezone.utc)
    audit_record(
        db,
        action="draft.discard",
        resource_type="draft",
        resource_id=draft.id,
        account_id=account.id,
        subject_id=draft.subject_id,
        before={"status": "pending"},
        after={"status": "discarded"},
        request=request,
    )
    db.commit()
    return {"status": "discarded"}


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
