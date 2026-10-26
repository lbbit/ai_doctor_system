"""问诊编排主流程（§5.2/§6.7）：脱敏 → 组装 L0–L2 → 红旗预检 → LLM（重试 1 次）
→ 引用校验 + Trace → 红旗卡片 → 草稿抽取 → SSE 事件。

失败路径（E-5）：messages(error_state=timeout)，**不写 Trace**。
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Dict, Generator, List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.chat import citations as citations_mod
from app.chat.context_builder import build_context, render_system_context
from app.chat.draft_parser import maybe_extract_draft
from app.chat.titler import generate_title
from app.agent.planner import plan_tools, rule_based_plan
from app.agent.tools import execute_tools
from app.common.errors import LLMTimeoutError, LLMUnavailableError, RateLimitedError
from app.config import resolver
from app.common.ids import new_id
from app.config.settings import settings
from app.db.models import (
    Account,
    Conversation,
    MedicalEvent,
    Message,
    Observation,
    RedFlagHit,
    Subject,
)
from app.redflag import loader as redflag_loader
from app.redflag.engine import extract_obs_values, match_rules
from app.redflag.router import record_hits
from app.sanitizer.pipeline import sanitize_for_llm, sanitize_with_subject

logger = logging.getLogger("app.orchestration")

HISTORY_LIMIT = 20
# 新建会话的占位标题；回答完成后会被 LLM 生成的语义标题替换（§16）
NEW_CONVERSATION_TITLE = "新会话"
_DRAFT_INTENT = re.compile(
    r"(记一下|帮我记|先记|录一下|录入|补充档案|建档|记入|帮我录入|登记|记上)"
)


def _rate_limit(account_id: str) -> None:
    """Redis 限流（RATE_LIMIT_CHAT_PER_MIN；Redis 故障放行）。"""
    try:
        from app.preprocessing.queue import get_redis

        r = get_redis()
        key = f"rate:chat:{account_id}:{int(time.time() // 60)}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, 90)
        if count > settings.rate_limit_chat_per_min:
            raise RateLimitedError()
    except RateLimitedError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("rate_limit.skip redis unavailable")
        return


def _latest_obs_values(db: Session, subject_id: str) -> Dict[str, float]:
    """红旗 obs 条件用：每 code 最新数值（确定性，A-6）。"""
    rows = db.execute(
        select(Observation)
        .where(
            Observation.subject_id == subject_id,
            Observation.deleted_at.is_(None),
            Observation.code.is_not(None),
            Observation.value_num.is_not(None),
        )
        .order_by(Observation.occurred_at.desc())
        .limit(100)
    ).scalars()
    values: Dict[str, float] = {}
    for row in rows:
        if row.code not in values and row.value_num is not None:
            values[row.code] = float(row.value_num)
    return values


def _history(db: Session, conversation_id: str) -> List[dict]:
    rows = (
        db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.content != "",
            )
            .order_by(Message.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
        .scalars()
        .all()
    )
    rows = list(reversed(rows))
    return [{"role": m.role, "content": m.content} for m in rows]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str, size: int = 60) -> List[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _iter_tokens_with_heartbeat(
    llm,
    messages: List[dict],
    *,
    heartbeat_seconds: float = 4.0,
) -> Generator[tuple[str, str], None, None]:
    """在线程中跑 LLM 流，主线程按超时产出心跳事件。

    为什么需要：实测首 token 延迟可达 10s+（长上下文 + 检索资料注入），
    期间若无任何事件，用户会认为"卡死"。生成器本身是单线程顺序执行的，
    无法在等待阻塞调用时"同时"发事件，因此把 LLM 流放到工作线程、
    主线程用队列超时取数据。

    产出 ``(kind, payload)``：
    - ``("token", text)``：增量文本
    - ``("heartbeat", elapsed_label)``：等待心跳
    - 结束时正常返回；LLM 异常会在拿到异常项时重新抛出。
    """
    import queue
    import threading

    q: "queue.Queue[tuple[str, object]]" = queue.Queue()
    sentinel_done = object()

    def _worker() -> None:
        try:
            for piece in llm.chat_stream(messages, temperature=0.3):
                q.put(("token", piece))
        except BaseException as exc:  # noqa: BLE001 - 全部转交主线程处理
            q.put(("error", exc))
        finally:
            q.put(("done", sentinel_done))

    thread = threading.Thread(target=_worker, name="llm-stream", daemon=True)
    started = time.perf_counter()
    thread.start()
    last_emit = started
    while True:
        timeout = max(0.05, heartbeat_seconds - (time.perf_counter() - last_emit))
        try:
            kind, payload = q.get(timeout=timeout)
        except queue.Empty:
            elapsed = int(time.perf_counter() - started)
            yield ("heartbeat", f"仍在生成中（已等待 {elapsed}s）")
            last_emit = time.perf_counter()
            continue
        if kind == "done":
            return
        if kind == "error":
            raise payload  # type: ignore[misc]
        last_emit = time.perf_counter()
        yield ("token", str(payload))


def send_message_events(
    db: Session,
    account: Account,
    conversation: Conversation,
    content: str,
    request=None,
) -> Generator[str, None, None]:
    """SSE 事件生成器（meta → delta* → citations → [redflag] → [draft] → done/error）。"""
    _rate_limit(account.id)
    subject = db.get(Subject, conversation.subject_id)
    if subject is None or subject.account_id != account.id:
        from app.common.errors import NotFoundError

        raise NotFoundError("档案对象不存在")

    # ---- 落用户消息 + 占位助手消息（红旗 FK 需要 message 行）----
    user_msg = Message(id=new_id(), conversation_id=conversation.id,
                       role="user", content=content)
    assistant_id = new_id()
    assistant_msg = Message(id=assistant_id, conversation_id=conversation.id,
                            role="assistant", content="")
    db.add(user_msg)
    db.add(assistant_msg)
    # 首轮先用「新会话」占位，回答后由 LLM 生成语义化短标题（§16）。
    # 记录占位标记用于幂等：已生成过语义标题的会话不再重复调用模型。
    title_is_placeholder = False
    if not conversation.title or conversation.title == NEW_CONVERSATION_TITLE:
        # 用 Core UPDATE 落库：ORM 身份映射在多段 commit 后可能持有过期状态，
        # 历史缺陷：此处仅赋值未持久化，导致首轮标题实际为 NULL。
        db.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(title=NEW_CONVERSATION_TITLE)
        )
        conversation.title = NEW_CONVERSATION_TITLE
        title_is_placeholder = True
    started = time.perf_counter()
    db.commit()
    db.refresh(user_msg)

    yield _sse("meta", {
        "conversationId": conversation.id,
        "userMessageId": user_msg.id,
        "assistantMessageId": assistant_id,
        "subjectId": subject.id,
        "subjectName": subject.name,
        "subjectAvatar": subject.avatar,
    })

    # ---- 红旗预检（A-1：规则引擎，模型无关；命中先落库+审计）----
    hits: List[dict] = []
    try:
        rules = redflag_loader.get_rules()
        obs_values = _latest_obs_values(db, subject.id)
        hits = match_rules(rules, content, obs_values)
        if hits:
            hits = record_hits(
                db,
                account=account,
                subject_id=subject.id,
                conversation_id=conversation.id,
                message_id=assistant_id,
                hits=hits,
                request=request,
            )
            db.commit()
            from app.common.logging import EV_REDFLAG_HIT

            logger.info("redflag.hit count=%s codes=%s",
                        len(hits), [h["code"] for h in hits])
    except Exception:  # noqa: BLE001
        logger.exception("redflag.precheck_error")

    # ---- 分层组装 + 出域脱敏（§5.2 顺序）----
    yield _sse("status", {"stage": "thinking", "detail": "正在分析问题"})
    try:
        ctx = build_context(db, account, subject, content)
        system_text = render_system_context(ctx)
        safe_user = sanitize_with_subject(db, account, subject, content)
        ctx.sanitizer_summary = safe_user.summary
        safe_system = sanitize_with_subject(db, account, subject, system_text).text
        history = _history(db, conversation.id)
        safe_history = [
            {"role": m["role"],
             "content": sanitize_with_subject(db, account, subject, m["content"]).text}
            for m in history
            if m["content"]
        ]
    except Exception:  # noqa: BLE001
        logger.exception("context.build_error")
        yield _sse("error", {"code": "INTERNAL", "message": "上下文组装失败，请重试"})
        return

    messages = [{"role": "system", "content": safe_system}]
    messages += safe_history
    messages.append({"role": "user", "content": safe_user.text})

    # ---- LLM 客户端（问诊场景：能力优先，见 §6.9 分场景配置）----
    from app.llm.client import get_llm_client
    from app.common.logging import EV_LLM_CALL

    llm = get_llm_client("chat", db=db)

    # ---- Agent 工具轮（§6.10）----
    # 前置一次规划：决定是否联网检索；失败一律降级为“不调工具”，不阻断问诊。
    agent_meta = {"usedTools": [], "toolErrors": []}
    agent_enabled = resolver.get_bool(db, "agent.enabled", True)
    if agent_enabled:
        try:
            # 先用确定性规则收敛（省一次 ~6s 的模型往返，§19 性能优化）
            plan = rule_based_plan(content)
            if plan is None:
                yield _sse("status", {"stage": "planning", "detail": "判断是否需要外部资料"})
                # 规划是低难度判断 → 用 extraction（成本与延迟双低）
                plan = plan_tools(
                    get_llm_client("extraction", db=db),
                    content,
                    max_calls=resolver.get_int(db, "agent.max_tool_calls", 3),
                    enabled=agent_enabled,
                    # 隐私（R0 T005）：规划内容出域前必须脱敏
                    sanitizer=lambda text: sanitize_with_subject(
                        db, account, subject, text
                    ).text,
                )
            else:
                logger.info("agent.plan.rule reason=%s", plan.reason)
            if not plan.empty:
                yield _sse(
                    "status",
                    {
                        "stage": "tool_running",
                        "detail": "正在检索外部资料",
                        "tool": plan.calls[0].name if plan.calls else "",
                    },
                )
                bundle = execute_tools(
                    plan.calls,
                    query=content,
                    timeout_seconds=resolver.get_int(db, "agent.tool_timeout_seconds", 8),
                    max_results=resolver.get_int(db, "agent.max_results", 5),
                    sanitizer=lambda text: sanitize_with_subject(
                        db, account, subject, text
                    ).text,
                    max_calls=resolver.get_int(db, "agent.max_tool_calls", 3),
                )
                agent_meta["usedTools"] = bundle.used_tools
                agent_meta["toolErrors"] = bundle.tool_errors
                yield _sse(
                    "status", {"stage": "tool_done", "detail": "正在整理检索结果"}
                )
            else:
                # 即使不检索，临床思维 skill 仍可注入，提升回答结构稳定性。
                bundle = execute_tools([], query=content)
            tool_block = bundle.render_for_prompt()
            if tool_block:
                messages = [
                    {"role": "system", "content": safe_system + "\n\n" + tool_block}
                ] + safe_history + [{"role": "user", "content": safe_user.text}]
                logger.info(
                    "agent.tools used=%s errors=%s",
                    agent_meta["usedTools"],
                    agent_meta["toolErrors"],
                )
        except Exception:  # noqa: BLE001 - Agent 失败必须可降级
            logger.exception("agent.pipeline_error")
            agent_meta["toolErrors"] = agent_meta.get("toolErrors") or ["pipeline_error"]

    latency_ms = 0
    # ---- 真流式生成（§19）：边收边发 delta，引用标记整段缓冲判定 ----
    from app.chat.stream_utils import CitationBuffer

    pool_labels = {c.label for c in (ctx.citation_pool or [])}
    cite_buf = CitationBuffer(pool_labels)
    raw_parts: List[str] = []       # LLM 原始输出（用于最终清洗与落库）
    streamed = False
    first_token_ms: Optional[int] = None
    answer = ""
    try:
        t0 = time.perf_counter()
        yield _sse("status", {"stage": "generating", "detail": "正在生成回答"})
        for kind, payload in _iter_tokens_with_heartbeat(llm, messages):
            if kind == "heartbeat":
                yield _sse("status", {"stage": "heartbeat", "detail": payload})
                continue
            token = payload
            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - t0) * 1000)
            streamed = True
            raw_parts.append(token)
            for chunk in cite_buf.feed(token):
                yield _sse("delta", {"content": chunk})
        for chunk in cite_buf.flush():
            yield _sse("delta", {"content": chunk})
        answer = "".join(raw_parts)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "llm.stream.completed streamed=%s firstTokenMs=%s latencyMs=%s",
            streamed,
            first_token_ms,
            latency_ms,
        )
        # 流式返回 0 内容：部分网关在长上下文/检索资料注入后会空返回，
        # 此时必须降级用非流式重试一次，否则用户拿到"空回答 + 错误"。
        if not answer.strip():
            logger.warning("llm.stream.empty fallback=blocking")
            yield _sse("status", {"stage": "generating", "detail": "正在重新生成回答"})
            answer = llm.chat(messages, temperature=0.3)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if answer and answer.strip():
                # 一次性补发（已无流式优势，但至少给出答案）
                streamed = False
                for chunk in cite_buf.feed(answer):
                    yield _sse("delta", {"content": chunk})
                for chunk in cite_buf.flush():
                    yield _sse("delta", {"content": chunk})
                logger.info("llm.stream.fallback_ok chars=%s", len(answer))
            else:
                raise LLMUnavailableError("AI 未能生成回答，请重试")
    except (LLMTimeoutError, LLMUnavailableError) as exc:
        # 已有部分内容：保留并落库，避免用户已看到的内容丢失
        if streamed and raw_parts:
            answer = "".join(raw_parts)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "llm.stream.interrupted keptChars=%s reason=%s",
                len(answer),
                type(exc).__name__,
            )
        else:
            assistant_msg.error_state = (
                "timeout" if isinstance(exc, LLMTimeoutError) else "unavailable"
            )
            assistant_msg.content = ""
            assistant_msg.latency_ms = latency_ms or int(
                (time.perf_counter() - started) * 1000
            )
            db.commit()
            # E-11：失败也必现红旗卡片
            for hit in hits:
                yield _sse("redflag", {
                    "code": hit["code"], "severity": hit["severity"],
                    "message": hit["message"], "hitId": hit.get("hitId"),
                })
            yield _sse("error", {"code": exc.code, "message": exc.message})
            return

    # ---- 引用清洗 + Trace（A-3：清单外剥除；失败无 Trace）----
    # 说明：流式阶段 CitationBuffer 已按同一规则放行/剥除，
    # 这里对全文再跑一次以产出 refs 并保证落库内容与展示完全一致。
    cleaned, refs = citations_mod.extract_citations(answer, ctx.citation_pool)
    trace = citations_mod.persist_trace(
        db,
        message_id=assistant_id,
        subject_id=subject.id,
        ctx=ctx,
        refs=refs,
    )
    assistant_msg.content = cleaned or answer
    assistant_msg.llm_model = llm.model
    assistant_msg.latency_ms = latency_ms
    if hits:
        assistant_msg.red_flag_hit_id = hits[0].get("hitId")
    db.commit()

    # ---- 事件流：delta 已在流式阶段发送，这里只补 citations ----
    yield _sse("citations", {
        "traceId": trace.id,
        "refs": refs,
        "sanitizer": ctx.sanitizer_summary,
    })

    # ---- 会话标题语义化（§16；仅首轮、失败降级、成本优先）----
    if title_is_placeholder:
        try:
            new_title = generate_title(
                get_llm_client("extraction", db=db),
                content,
                assistant_msg.content,
                # 隐私（R0 T005）：标题生成是出域调用，提问/回答都需脱敏
                sanitizer=lambda text: sanitize_with_subject(
                    db, account, subject, text
                ).text,
            )
            if new_title:
                # 用 Core UPDATE 直接落库：ORM 身份映射在多段 commit 后可能持有
                # 过期状态，直接写列值可确保标题一定持久化。
                db.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation.id)
                    .values(title=new_title)
                )
                db.commit()
                conversation.title = new_title
                logger.info("chat.title generated len=%s", len(new_title))
        except Exception:  # noqa: BLE001 - 标题失败不得影响问诊
            logger.exception("chat.title_skip")

    # E-11：规则引擎独立插入转诊卡片（模型拒绝配合也必现）
    for hit in hits:
        yield _sse("redflag", {
            "code": hit["code"], "severity": hit["severity"],
            "message": hit["message"], "hitId": hit.get("hitId"),
        })

    # ---- 对话式草稿（F-12；意图命中才尝试，LLM 不可用则跳过）----
    if _DRAFT_INTENT.search(content) or re.search(
        r"(血压|血糖|心率|体温|血压值)\s*[:：]?\s*\d", content
    ):
        try:
            draft = maybe_extract_draft(
                db, account, subject, user_msg, content, safe_user.text
            )
            if draft is not None:
                db.commit()
                yield _sse("draft", {
                    "id": draft.id,
                    "messageId": draft.message_id,
                    "subjectId": draft.subject_id,
                    "subjectName": subject.name,
                    "subjectAvatar": subject.avatar,
                    "draftType": draft.draft_type,
                    "payload": draft.payload,
                    "status": draft.status,
                })
        except Exception:  # noqa: BLE001
            logger.exception("draft.extract_skip")

    yield _sse("done", {
        "messageId": assistant_id,
        "traceId": trace.id,
        "latencyMs": latency_ms,
        "agent": agent_meta,
        "streamed": streamed,
        "firstTokenMs": first_token_ms,
    })


def openai_chunk(event: str, data: dict) -> str:
    """OpenAI 兼容流的 delta 包装（/v1/chat/completions，§4.3）。"""
    delta: dict = {"content": ""}
    if event == "delta":
        delta = {"content": data.get("content", "")}
    elif event == "citations":
        delta = {
            "_trace_id": data.get("traceId"),
            "_citations": [
                {
                    "i": r["refIndex"],
                    "label": r["label"],
                    "document_id": r.get("documentId"),
                }
                for r in data.get("refs", [])
            ],
        }
    elif event == "redflag":
        delta = {"_red_flag": {"code": data.get("code"), "message": data.get("message")}}
    elif event == "meta":
        delta = {"_conversation_id": data.get("conversationId"),
                 "_message_id": data.get("assistantMessageId")}
    elif event == "error":
        delta = {"_error": data}
    chunk = {
        "id": new_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": data.get("model") or settings.llm_model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
