"""引用标记生成 + Trace 落库（F-10/A-3；§6.4）。"""

import logging
import re
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat.context_builder import AssembledContext, Citation
from app.common.errors import NotFoundError
from app.common.ids import new_id
from app.db.models import Account, Trace, TraceRef

logger = logging.getLogger("app.citations")

# [依据：标签] / [依据1：标签] / [依据：标签]（全半角冒号兼容）
_CITE_RE = re.compile(r"\[依据\s*(\d*)\s*[:：]\s*([^\]]{1,200})\]")


def extract_citations(
    answer: str, pool: List[Citation]
) -> Tuple[str, List[dict]]:
    """解析并清洗引用标记（§6.4）：
    - 清单外引用**直接剥除**（A-3）；
    - 有效标记按出现顺序分配 ref_index（1..n），正文标记保留原标签。
    返回 (清洗后正文, refs=[{refIndex, refType, refId, label, documentId}])。
    """
    label_to_cite = {c.label: c for c in pool}
    refs: List[dict] = []

    def _repl(m: re.Match) -> str:
        label = m.group(2).strip()
        cite = label_to_cite.get(label)
        if cite is None:
            # 兜底：前缀/包含匹配一次
            cite = next(
                (c for c in pool if c.label in label or label in c.label), None
            )
        if cite is None:
            logger.info("citation.stripped label=%s", label[:80])
            return ""  # 清单外引用剥除（A-3）
        refs.append(
            {
                "refIndex": len(refs) + 1,
                "refType": cite.ref_type,
                "refId": cite.ref_id,
                "label": cite.label,
                "documentId": cite.document_id,
            }
        )
        return m.group(0)  # 保留原标记（渲染按出现序号映射 ref_index）

    cleaned = _CITE_RE.sub(_repl, answer)
    # 去除剥除后残留的空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, refs


def persist_trace(
    db: Session,
    *,
    message_id: str,
    subject_id: str,
    ctx: AssembledContext,
    refs: List[dict],
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> Trace:
    """每次成功回答恰好 1 条 Trace（UNIQUE(message_id)）+ trace_refs 逐行。"""
    context_layers = {}
    for layer, items in (ctx.layers or {}).items():
        if layer == "L1":
            context_layers[layer] = [
                {"code": i.get("code"), "points": len(i.get("series", []))}
                for i in items
            ]
        elif layer == "L2":
            context_layers[layer] = [i.get("id") for i in items]
        elif layer == "L4":
            context_layers[layer] = [i.get("id") for i in items]
        else:
            context_layers[layer] = items
    context_layers["sanitizer"] = ctx.sanitizer_summary

    trace = Trace(
        id=new_id(),
        message_id=message_id,
        subject_id=subject_id,
        context_layers=context_layers,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    db.add(trace)
    db.flush()
    for ref in refs:
        db.add(
            TraceRef(
                id=new_id(),
                trace_id=trace.id,
                ref_index=ref["refIndex"],
                ref_type=ref["refType"],
                ref_id=ref["refId"],
                label=ref["label"],
            )
        )
    return trace


def get_trace_for_message(
    db: Session, account: Account, message_id: str
) -> dict:
    """查看依据（AC-10.3）：跨 Account → 404（K4）。"""
    from app.db.models import Conversation, Message, Document

    message = db.get(Message, message_id)
    if message is None:
        raise NotFoundError("消息不存在")
    conv = db.get(Conversation, message.conversation_id)
    if conv is None or conv.account_id != account.id:
        raise NotFoundError("消息不存在")

    trace = db.scalar(select(Trace).where(Trace.message_id == message_id))
    if trace is None:
        return {"traceId": None, "layers": {}, "refs": []}

    trace_refs = (
        db.execute(
            select(TraceRef)
            .where(TraceRef.trace_id == trace.id)
            .order_by(TraceRef.ref_index)
        )
        .scalars()
        .all()
    )
    from datetime import datetime, timedelta

    from app.config.settings import settings
    from app.documents.storage import get_storage_client

    storage = get_storage_client()
    out_refs = []
    for r in trace_refs:
        url = None
        if r.ref_type == "document":
            doc = db.get(Document, r.ref_id)
            if doc is not None and doc.account_id == account.id:
                try:
                    url = storage.presign_get(doc.file_key)
                except Exception:  # noqa: BLE001
                    url = None
        elif r.ref_type == "event":
            from app.db.models import MedicalEvent

            ev = db.get(MedicalEvent, r.ref_id)
            if ev is not None and ev.document_id:
                doc = db.get(Document, ev.document_id)
                if doc is not None:
                    try:
                        url = storage.presign_get(doc.file_key)
                    except Exception:  # noqa: BLE001
                        url = None
        elif r.ref_type == "observation":
            from app.db.models import Observation

            ob = db.get(Observation, r.ref_id)
            if ob is not None and ob.source_document_id:
                doc = db.get(Document, ob.source_document_id)
                if doc is not None:
                    try:
                        url = storage.presign_get(doc.file_key)
                    except Exception:  # noqa: BLE001
                        url = None
        out_refs.append(
            {
                "refIndex": r.ref_index,
                "refType": r.ref_type,
                "refId": r.ref_id,
                "label": r.label,
                "url": url,
                "expiresInSeconds": settings.sign_url_expire_seconds if url else None,
            }
        )
    return {
        "traceId": trace.id,
        "layers": trace.context_layers or {},
        "refs": out_refs,
    }
