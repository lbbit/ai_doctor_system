"""分层上下文组装器（§6.1；AC-9.2）：L0/L1/L2(/L4)，L3 留接口恒空（P1 pgvector）。

引用池：仅**有原件回链（document_id）**的实体可入池（A-3 无原件 ID 不准出现）。
数值序列一律 SQL 聚合（A-6）。
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    Document,
    IndicatorDictionary,
    MedicalEvent,
    Observation,
    Profile,
    Subject,
)

logger = logging.getLogger("app.context")

# token 预算（§6.1，中文按 ~1.5 字符/token 估算字符上限）
CHARS_PER_TOKEN = 1.5
BUDGET = {"L0": 400, "L1": 1200, "L2": 1500, "L3": 0, "L4": 300}


def _chars(layer: str) -> int:
    return int(BUDGET[layer] * CHARS_PER_TOKEN)


@dataclass
class Citation:
    ref_type: str       # observation | event | document
    ref_id: str
    label: str
    document_id: Optional[str] = None


@dataclass
class AssembledContext:
    layers: Dict[str, list] = field(default_factory=dict)
    citation_pool: List[Citation] = field(default_factory=list)
    token_budget: Dict[str, int] = field(default_factory=lambda: dict(BUDGET))
    sanitizer_summary: list = field(default_factory=list)


def _age_bracket(birthday: Optional[date]) -> str:
    if not birthday:
        return "未知"
    today = date.today()
    age = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    lower = (age // 10) * 10
    return f"{lower}-{lower + 9}岁" if age >= 20 and lower > 0 else f"{age}岁"


def _topic_keywords(text: str) -> List[str]:
    """从用户问题提取中文片段话题词（2-8 字连续中文）。"""
    return re.findall(r"[一-龥]{2,8}", text or "")


def _match_topic_codes(db: Session, text: str) -> List[str]:
    """话题 → 指标 code（aliases/name 匹配，§6.1 L1）。"""
    keywords = _topic_keywords(text)
    if not keywords:
        return []
    rows = db.execute(
        select(IndicatorDictionary).where(IndicatorDictionary.active.is_(True))
    ).scalars()
    codes: List[str] = []
    for row in rows:
        candidates = [row.name_cn, row.code, *(row.aliases or [])]
        for kw in keywords:
            if any(kw in c or c in kw for c in candidates if c):
                codes.append(row.code)
                break
    return codes


def _recent_fallback_codes(db: Session, subject_id: str) -> List[str]:
    """无交集：最近 3 个高频 code（§6.1 L1 兜底）。"""
    from sqlalchemy import func

    rows = db.execute(
        select(Observation.code, func.count(Observation.id).label("n"))
        .where(
            Observation.subject_id == subject_id,
            Observation.deleted_at.is_(None),
            Observation.code.is_not(None),
        )
        .group_by(Observation.code)
        .order_by(func.count(Observation.id).desc())
        .limit(3)
    ).all()
    return [r[0] for r in rows]


def _l1_series(db: Session, subject_id: str, code: str) -> dict:
    """单 code 近 10 点序列 + 变化率（确定性计算，A-6）。"""
    rows = list(
        db.execute(
            select(Observation)
            .where(
                Observation.subject_id == subject_id,
                Observation.code == code,
                Observation.deleted_at.is_(None),
            )
            .order_by(Observation.occurred_at.desc())
            .limit(10)
        ).scalars()
    )
    rows.reverse()
    if not rows:
        return {}
    points = [
        {
            "date": r.occurred_at.isoformat(),
            "value": float(r.value_num) if r.value_num is not None else (r.value_text or ""),
            "flag": r.abnormal_flag,
            "unit": r.unit,
            "doc": r.source_document_id,
            "id": r.id,
            "name": r.display_name,
        }
        for r in rows
    ]
    change_rate = None
    nums = [p["value"] for p in points if isinstance(p["value"], (int, float))]
    if len(nums) >= 2 and nums[0] not in (0, None):
        change_rate = round((nums[-1] - nums[0]) / abs(nums[0]) * 100, 1)
    return {
        "code": code,
        "name": rows[-1].display_name,
        "unit": rows[-1].unit,
        "series": points,
        "changeRatePct": change_rate,
        "abnormal": [p for p in points if p["flag"] in ("H", "L", "A")],
    }


def build_context(
    db: Session,
    account: Account,
    subject: Subject,
    user_text: str,
) -> AssembledContext:
    """组装 L0/L1/L2/L4（L3 恒空接口，P1 接 pgvector topK）。"""
    ctx = AssembledContext()

    # ---- L0 静态（profiles 直取，每轮必带）----
    profile = db.get(Profile, subject.subject_id if hasattr(subject, "subject_id") else subject.id)
    l0 = {
        "年龄段": _age_bracket(subject.birthday),
        "性别": subject.gender or "未知",
        "关系": subject.relation,
        "过敏史": (profile.allergies if profile else "") or "未填写",
        "慢病": (profile.chronic_diseases if profile else "") or "未填写",
        "用药中": (profile.medications if profile else "") or "未填写",
        "家族史": (profile.family_history if profile else "") or "未填写",
    }
    l0_text = "；".join(f"{k}={v}" for k, v in l0.items())
    ctx.layers["L0"] = [{"text": l0_text[: _chars("L0")]}]

    # ---- L1 时序（SQL 聚合序列 + 变化率）----
    codes = _match_topic_codes(db, user_text) or _recent_fallback_codes(db, subject.id)
    l1_items = []
    for code in codes[:6]:
        series = _l1_series(db, subject.id, code)
        if series:
            l1_items.append(series)
        if sum(len(str(i)) for i in l1_items) > _chars("L1"):
            break
    ctx.layers["L1"] = l1_items

    # ---- L2 事件（近 30 天全部 + 历史同类；就近 8 条，倒序）----
    since = date.today() - timedelta(days=30)
    q = (
        select(MedicalEvent)
        .where(
            MedicalEvent.subject_id == subject.id,
            MedicalEvent.account_id == account.id,
            MedicalEvent.deleted_at.is_(None),
        )
        .order_by(MedicalEvent.occurred_at.desc())
    )
    keywords = _topic_keywords(user_text)
    recent_or_related = q.where(
        (MedicalEvent.occurred_at >= since) | _ilike_match(keywords)
    ).limit(8)
    events = list(db.execute(recent_or_related).scalars())
    ctx.layers["L2"] = [
        {
            "id": e.id,
            "type": e.event_type,
            "date": e.occurred_at.isoformat(),
            "title": e.title,
            "diagnosis": e.diagnosis,
            "narrative": (e.narrative or "")[:200],
            "documentId": e.document_id,
        }
        for e in events
    ]

    # ---- L3 语义：P0 恒空（§6.1 接口返回 []，P1 接 pgvector embedding topK）----
    ctx.layers["L3"] = []

    # ---- L4 证据（引用实体的原件回链元数据，仅 label+日期不传内容）----
    doc_ids: List[str] = []
    for item in l1_items:
        for p in item.get("series", []):
            if p.get("doc") and p["doc"] not in doc_ids:
                doc_ids.append(p["doc"])
    for e in events:
        if e.document_id and e.document_id not in doc_ids:
            doc_ids.append(e.document_id)
    l4_items = []
    if doc_ids:
        docs = db.execute(
            select(Document).where(
                Document.id.in_(doc_ids[:20]),
                Document.account_id == account.id,
                Document.deleted_at.is_(None),
            )
        ).scalars()
        for d in docs:
            l4_items.append(
                {
                    "id": d.id,
                    "fileName": d.file_name,
                    "docType": d.doc_type,
                    "date": d.created_at.date().isoformat() if d.created_at else None,
                    "status": d.status,
                }
            )
    ctx.layers["L4"] = l4_items

    # ---- 引用池（只收有 document_id 的实体，A-3）----
    pool: List[Citation] = []
    seen = set()
    for d in l4_items:
        key = ("document", d["id"])
        if key in seen:
            continue
        seen.add(key)
        pool.append(
            Citation(
                ref_type="document",
                ref_id=d["id"],
                label=f"{d['date'] or ''} {d['fileName']}".strip(),
                document_id=d["id"],
            )
        )
    for e in events:
        if not e.document_id:
            continue  # 无原件不出引用（A-3）
        key = ("event", e.id)
        if key in seen:
            continue
        seen.add(key)
        pool.append(
            Citation(
                ref_type="event",
                ref_id=e.id,
                label=f"{e.occurred_at.isoformat()} {e.title or '健康事件'}",
                document_id=e.document_id,
            )
        )
    for item in l1_items:
        for p in item.get("series", []):
            if not p.get("doc"):
                continue
            key = ("observation", p["id"])
            if key in seen:
                continue
            seen.add(key)
            pool.append(
                Citation(
                    ref_type="observation",
                    ref_id=p["id"],
                    label=f"{p['date']} {p['name']}",
                    document_id=p["doc"],
                )
            )
    ctx.citation_pool = pool[:40]
    return ctx


def _ilike_match(keywords: List[str]):
    """历史同类：标题/诊断包含任一话题词。"""
    from sqlalchemy import or_

    if not keywords:
        return MedicalEvent.id.is_(None)  # 永假，配合 recent 分支
    conds = []
    for kw in keywords[:5]:
        conds.append(MedicalEvent.title.ilike(f"%{kw}%"))
        conds.append(MedicalEvent.diagnosis.ilike(f"%{kw}%"))
        conds.append(MedicalEvent.narrative.ilike(f"%{kw}%"))
    return or_(*conds)


def render_system_context(ctx: AssembledContext) -> str:
    """组包顺序（§6.1）：免责与定位已在 system 模板；L0 → L1(markdown 表) → L2 → L4 清单。"""
    from app.llm.extraction import render_prompt

    parts = [render_prompt("consult_system.md")]

    l0 = ctx.layers.get("L0") or []
    if l0:
        parts.append(f"\n## L0 档案对象静态信息\n{l0[0]['text']}")

    l1 = ctx.layers.get("L1") or []
    if l1:
        parts.append("\n## L1 指标时序（SQL 聚合，禁止自行重算）")
        for item in l1:
            table = "| 日期 | 数值 | 单位 | 标记 |\n|---|---|---|---|\n"
            for p in item.get("series", []):
                table += f"| {p['date']} | {p['value']} | {p.get('unit') or ''} | {p.get('flag') or ''} |\n"
            rate = item.get("changeRatePct")
            parts.append(
                f"\n### {item['name']}（{item['code']}）\n{table}"
                f"区间变化率: {rate}%（首末点计算）"
            )
    else:
        parts.append("\n## L1 指标时序\n（档案中暂无指标数据）")

    l2 = ctx.layers.get("L2") or []
    if l2:
        parts.append("\n## L2 事件时间线（近30天+同类历史）")
        for e in l2:
            parts.append(
                f"- {e['date']} [{e['type']}] {e['title']}"
                + (f"（诊断：{e['diagnosis']}）" if e.get("diagnosis") else "")
            )
    else:
        parts.append("\n## L2 事件时间线\n（档案中暂无事件记录）")

    l4 = ctx.citation_pool
    if l4:
        parts.append("\n## L4 可引用清单（只能引用以下编号对应标签）")
        for i, c in enumerate(l4, start=1):
            parts.append(f"[{i}] {c.label}（文档ID: {c.document_id}，类型: {c.ref_type}）")
        parts.append("\n输出引用格式：[依据：<清单标签>]，标签必须与清单完全一致。")
    else:
        parts.append("\n## L4 可引用清单\n（无可引用原件）")

    return "\n".join(parts)
