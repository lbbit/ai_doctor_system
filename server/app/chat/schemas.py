"""问诊 schema（F-09/F-10/F-12）。"""

from typing import List, Optional

from app.common.schemas import CamelModel


class ConversationCreate(CamelModel):
    # 可空：缺失时由 require_subject 抛 SUBJECT_REQUIRED（B-1，而非 pydantic 422）
    subjectId: Optional[str] = None
    title: Optional[str] = None


class ConversationOut(CamelModel):
    id: str
    subjectId: str
    title: Optional[str] = None
    status: str = "active"
    lastMessageAt: Optional[str] = None


class MessageOut(CamelModel):
    id: str
    role: str
    content: str
    createdAt: Optional[str] = None
    redFlagHitId: Optional[str] = None
    errorState: Optional[str] = None


class DraftOut(CamelModel):
    id: str
    messageId: str
    subjectId: str
    draftType: str
    payload: dict
    status: str
    confirmedAt: Optional[str] = None
    discardedAt: Optional[str] = None


class SendMessageRequest(CamelModel):
    content: str


class TraceOut(CamelModel):
    traceId: str
    layers: dict
    refs: List[dict]


class DraftConfirmRequest(CamelModel):
    payload: Optional[dict] = None
