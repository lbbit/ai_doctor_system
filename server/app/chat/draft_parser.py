"""对话式草稿抽取与落库（F-12/§6.5）：相对时间解析 + LLM 双字段抽取。

确认/丢弃逻辑见 chat/service.py（HITL：先草稿后入库，A-8）。
"""

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.common.ids import new_id
from app.db.models import Account, Message, MessageDraft, Subject

logger = logging.getLogger("app.draft")


def parse_relative_date(
    expr: Optional[str], base: Optional[date] = None
) -> Tuple[date, str]:
    """相对时间表达 → (date, precision)；失败返回 (当日, 'unknown')（AC-12.4）。"""
    base = base or date.today()
    if not expr:
        return base, "unknown"
    text = str(expr).strip()

    if text in ("今天", "今日", "刚才", "现在"):
        return base, "day"
    if text in ("昨天", "昨日"):
        return base - timedelta(days=1), "day"
    if text in ("前天",):
        return base - timedelta(days=2), "day"

    # 上周三 / 上周五 / 前天下午...
    m = re.match(r"上个?(?:周|星期)([一二三四五六日天])", text)
    if m:
        weekday = "一二三四五六日天".index(m.group(1)) % 7
        this_week_start = base - timedelta(days=base.weekday())
        last_week_start = this_week_start - timedelta(days=7)
        return last_week_start + timedelta(days=weekday), "day"

    # 2026年3月12日 / 2026-03-12 / 2026/3/12 / 3月12日
    m = re.match(r"(\d{4})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "day"
        except ValueError:
            pass
    m = re.match(r"(\d{1,2})\s*月\s*(\d{1,2})", text)
    if m:
        try:
            return date(base.year, int(m.group(1)), int(m.group(2))), "day"
        except ValueError:
            pass
    # 2026年3月 / 2026-03 → 月精度（当月 1 日）
    m = re.match(r"(\d{4})\s*[年\-/\.]\s*(\d{1,2})\s*月?$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1), "month"
        except ValueError:
            pass
    # 纯年份
    m = re.match(r"(\d{4})\s*年?$", text)
    if m:
        try:
            return date(int(m.group(1)), 1, 1), "year"
        except ValueError:
            pass
    return base, "unknown"


def _norm_draft(raw: dict) -> Optional[dict]:
    """校验/归一化 LLM 草稿 JSON（chat_draft schema 结构）。"""
    if not isinstance(raw, dict) or not raw.get("has_draft"):
        return None
    draft = raw.get("draft")
    if not isinstance(draft, dict):
        return None

    event = draft.get("event")
    out_event = None
    if isinstance(event, dict) and (event.get("title") or event.get("narrative")):
        expr = event.get("occurred_at_expression")
        occurred, precision = parse_relative_date(expr)
        etype = str(event.get("type") or "self_reported")
        if etype not in ("visit", "hospitalization", "surgery", "checkup",
                         "medication", "vaccine", "self_reported", "other"):
            etype = "self_reported"
        out_event = {
            "type": etype,
            "title": (event.get("title") or "").strip() or "自述记录",
            "narrative": (event.get("narrative") or "").strip() or None,
            "diagnosis": (event.get("diagnosis") or "").strip() or None,
            "department": (event.get("department") or "").strip() or None,
            "occurredAt": occurred.isoformat(),
            "precision": precision,
            "expression": expr,
            "confidence": _clamp(event.get("confidence"), 0.6),
        }

    observations = []
    for item in draft.get("observations") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("display_name") or "").strip()
        if not name:
            continue
        expr = item.get("occurred_at_expression")
        occurred, precision = parse_relative_date(expr)
        observations.append(
            {
                "displayName": name[:128],
                "value": item.get("value"),
                "unit": item.get("unit"),
                "refLow": _num(item.get("ref_low")),
                "refHigh": _num(item.get("ref_high")),
                "refText": item.get("ref_text"),
                "abnormalFlag": (
                    str(item["abnormal_flag"]).upper()
                    if item.get("abnormal_flag") in ("N", "L", "H", "A") else None
                ),
                "occurredAt": occurred.isoformat(),
                "precision": precision,
                "expression": expr,
                "confidence": _clamp(item.get("confidence"), 0.5),
            }
        )

    if not out_event and not observations:
        return None
    if out_event and observations:
        draft_type = "both"
    elif out_event:
        draft_type = "event"
    else:
        draft_type = "observation"
    return {
        "event": out_event,
        "observations": observations,
        "_draftType": draft_type,
    }


def _clamp(v, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def maybe_extract_draft(
    db: Session,
    account: Account,
    subject: Subject,
    message: Message,
    raw_text: str,
    safe_text: str,
) -> Optional[MessageDraft]:
    """意图命中时抽取草稿并落 message_drafts(status=pending)（A-8：不入时间线）。"""
    from app.llm.client import get_llm_client
    from app.llm.extraction import _parse_json_content, render_prompt

    llm = get_llm_client("extraction")
    if not llm.available():
        return None

    prompt = render_prompt("extract_draft.md")
    raw = llm.chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": safe_text},
        ],
        json_mode=True,
        temperature=0,
    )
    try:
        data = _parse_json_content(raw)
    except ValueError:
        logger.warning("draft.parse_failed")
        return None

    normalized = _norm_draft(data)
    if normalized is None:
        return None

    draft_type = normalized.pop("_draftType")
    draft = MessageDraft(
        id=new_id(),
        message_id=message.id,
        subject_id=subject.id,
        draft_type=draft_type,
        payload=normalized,
        status="pending",
    )
    db.add(draft)
    db.flush()
    logger.info("draft.created id=%s type=%s", draft.id, draft_type)
    return draft
