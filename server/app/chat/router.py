"""问诊路由（F-09/F-10/F-12）：/api/chat/* + /v1/chat/completions（§4.2 27-35）。"""

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.auth.deps import get_current_account
from app.chat import orchestration, service
from app.chat.citations import get_trace_for_message
from app.chat.schemas import ConversationCreate, DraftConfirmRequest, SendMessageRequest
from app.common.errors import AppError, SubjectRequiredError
from app.common.ids import new_id
from app.common.responses import ok_response
from app.config.settings import settings
from app.db.models import Account, Conversation
from app.db.session import SessionLocal, get_db

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx 关闭缓冲
}


# ---- 会话 ----


@router.get("/api/chat/conversations")
def list_conversations(
    request: Request,
    status: Optional[str] = "active",
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """会话列表。"""
    return ok_response(service.list_conversations(db, account, status), request)


@router.post("/api/chat/conversations")
def create_conversation(
    data: ConversationCreate,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """新建会话（绑定 subjectId，A-7）。"""
    conv = service.create_conversation(
        db, account, data.subjectId, data.title, request
    )
    return ok_response(conv, request)


@router.get("/api/chat/conversations/{conv_id}")
def get_conversation(
    conv_id: str,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """会话详情 + 消息 + 草稿。"""
    return ok_response(service.get_conversation_detail(db, account, conv_id), request)


@router.post("/api/chat/conversations/{conv_id}/messages")
def send_message(
    conv_id: str,
    data: SendMessageRequest,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """发送消息 → 流式回答（SSE：meta|delta|citations|redflag|draft|done|error）。"""
    conv = service.get_owned_conversation(db, account, conv_id)
    if conv.status != "active":
        from app.common.errors import StateInvalidError

        raise StateInvalidError("会话已归档，请新建会话")

    def _gen():
        try:
            yield from orchestration.send_message_events(
                db, account, conv, data.content, request
            )
        except AppError as exc:
            yield (
                f"event: error\ndata: "
                f"{json.dumps({'code': exc.code, 'message': exc.message}, ensure_ascii=False)}\n\n"
            )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("app.chat").exception("sse.error conv=%s", conv_id)
            yield (
                "event: error\ndata: "
                + json.dumps(
                    {"code": "INTERNAL", "message": "服务异常，请重试"},
                    ensure_ascii=False,
                )
                + "\n\n"
            )

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/chat/conversations/{conv_id}/close")
def close_conversation(
    conv_id: str,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """归档会话（状态机）。"""
    return ok_response(service.close_conversation(db, account, conv_id, request), request)


# ---- 溯源 / 草稿 ----


@router.get("/api/chat/messages/{message_id}/trace")
def get_trace(
    message_id: str,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """查看依据（AC-10.3；refs 含带时效原件 url）。"""
    return ok_response(get_trace_for_message(db, account, message_id), request)


@router.post("/api/chat/drafts/{draft_id}/confirm")
def confirm_draft(
    draft_id: str,
    data: DraftConfirmRequest,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """草稿确认入库（AC-12.2；payload 为编辑后值则以编辑值为准）。"""
    result = service.confirm_draft(
        db, account, draft_id, data.payload, request
    )
    return ok_response(result, request)


@router.post("/api/chat/drafts/{draft_id}/discard")
def discard_draft(
    draft_id: str,
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """丢弃草稿（AC-12.3：无孤儿数据）。"""
    return ok_response(service.discard_draft(db, account, draft_id, request), request)



# ---- OpenAI 兼容（§4.3）----


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str = ""


class OpenAIRequest(BaseModel):
    """OpenAI 形态 + 私有扩展（`_subject_id` / `_conversation_id` 走 extra 字段）。"""

    model_config = ConfigDict(extra="allow")

    model: str = "primary"
    messages: list[OpenAIMessage] = []
    stream: bool = False

    @property
    def subject_id(self) -> Optional[str]:
        return (self.model_extra or {}).get("_subject_id")

    @property
    def conversation_id(self) -> Optional[str]:
        return (self.model_extra or {}).get("_conversation_id")


def _openai_error(message: str, err_type: str, code: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": code}},
    )


def _iter_sse(sse: str) -> tuple[str, dict] | None:
    if sse.startswith("event: ") and "\ndata: " in sse:
        event = sse.split("\n", 1)[0][len("event: "):].strip()
        data = json.loads(sse.split("\ndata: ", 1)[1].strip())
        return event, data
    return None


@router.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI 兼容端点（§4.3）：内部同一编排（脱敏→组装→LLM→Trace→红旗）。"""
    db = SessionLocal()
    stream_owns_db = False
    try:
        # --- 鉴权（Bearer JWT；OpenAI 错误形态）---
        account = get_current_account(request, db, request.headers.get("authorization"))

        try:
            body = await request.json()
            payload = OpenAIRequest.model_validate(body or {})
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            return _openai_error(str(first.get("msg", "invalid")),
                                 "invalid_request_error", "VALIDATION_ERROR", 422)
        except Exception:  # noqa: BLE001
            return _openai_error("请求体必须是合法 JSON",
                                 "invalid_request_error", "BAD_REQUEST", 400)

        # B-1：缺 _subject_id → 422（OpenAI 错误形态）
        subject_id = payload.subject_id
        if not subject_id:
            return _openai_error(
                "写入必须选择档案对象（缺少 _subject_id）",
                "invalid_request_error", "SUBJECT_REQUIRED", 422,
            )

        last_user = next(
            (m.content for m in reversed(payload.messages) if m.role == "user"), ""
        )
        if not last_user:
            return _openai_error("messages 中缺少 user 消息",
                                 "invalid_request_error", "BAD_REQUEST", 400)

        from app.auth.deps import require_subject
        from app.chat.service import create_conversation, get_owned_conversation

        require_subject(db, account, subject_id)  # 越权 → 404（K4）
        if payload.conversation_id:
            conv = get_owned_conversation(db, account, payload.conversation_id)
        else:
            conv_row = create_conversation(db, account, subject_id, None)
            conv = db.get(Conversation, conv_row["id"])

        if payload.stream:
            stream_owns_db = True  # db 由流生成器负责关闭

            def _stream():
                from app.chat.orchestration import openai_chunk

                try:
                    for sse in orchestration.send_message_events(
                        db, account, conv, last_user, request
                    ):
                        parsed = _iter_sse(sse)
                        if not parsed:
                            continue
                        event, data = parsed
                        if event == "error":
                            yield (
                                "data: "
                                + json.dumps(
                                    {"error": {"message": data.get("message"),
                                               "type": "server_error",
                                               "code": data.get("code")}},
                                    ensure_ascii=False,
                                )
                                + "\n\n"
                            )
                            yield "data: [DONE]\n\n"
                            return
                        yield openai_chunk(event, data)
                    yield "data: [DONE]\n\n"
                finally:
                    db.close()

            return StreamingResponse(
                _stream(), media_type="text/event-stream", headers=SSE_HEADERS
            )

        # --- 非流式：聚合事件 → OpenAI completion ---
        full_text = ""
        trace_id = None
        refs = []
        red_flag = None
        for sse in orchestration.send_message_events(
            db, account, conv, last_user, request
        ):
            parsed = _iter_sse(sse)
            if not parsed:
                continue
            event, data = parsed
            if event == "delta":
                full_text += data.get("content", "")
            elif event == "citations":
                trace_id = data.get("traceId")
                refs = [
                    {"i": r["refIndex"], "label": r["label"],
                     "document_id": r.get("documentId")}
                    for r in data.get("refs", [])
                ]
            elif event == "redflag":
                red_flag = {"code": data.get("code"), "message": data.get("message")}
            elif event == "error":
                return _openai_error(
                    data.get("message", "error"),
                    "server_error",
                    data.get("code", "INTERNAL"),
                    504 if data.get("code") == "LLM_TIMEOUT" else 502,
                )
        message_obj = {"role": "assistant", "content": full_text}
        if trace_id:
            message_obj["_trace_id"] = trace_id
        if refs:
            message_obj["_citations"] = refs
        if red_flag:
            message_obj["_red_flag"] = red_flag
        return JSONResponse(
            content={
                "id": new_id(),
                "object": "chat.completion",
                "created": int(time.time()),
                "model": payload.model,
                "choices": [{
                    "index": 0,
                    "message": message_obj,
                    "finish_reason": "stop",
                }],
                "_conversation_id": conv.id,
            }
        )
    except AppError as exc:
        return _openai_error(
            exc.message,
            "server_error" if exc.http_status >= 500 else "invalid_request_error",
            exc.code,
            exc.http_status,
        )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("app.chat").exception("v1.chat.error")
        return _openai_error("服务器内部错误", "server_error", "INTERNAL", 500)
    finally:
        if not stream_owns_db:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
