"""Subject 管理 schema（F-02/F-03）。"""

from datetime import date
from typing import List, Optional

from pydantic import Field, field_validator

from app.common.schemas import CamelModel

RELATIONS = (
    "本人", "父亲", "母亲", "祖父母", "外祖父母", "配偶", "儿子", "女儿", "其他",
)


class SubjectCreate(CamelModel):
    name: str = Field(min_length=1, max_length=32)
    relation: str = "本人"
    birthday: Optional[date] = None
    gender: Optional[str] = None
    color: Optional[str] = None
    avatar: Optional[str] = None

    @field_validator("relation")
    @classmethod
    def _relation(cls, v: str) -> str:
        if v not in RELATIONS:
            raise ValueError(f"relation 必须是 {'/'.join(RELATIONS)} 之一")
        return v

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("male", "female", "unknown"):
            raise ValueError("gender 必须是 male/female/unknown")
        return v


class SubjectUpdate(CamelModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=32)
    relation: Optional[str] = None
    birthday: Optional[date] = None
    gender: Optional[str] = None
    color: Optional[str] = None
    avatar: Optional[str] = None

    @field_validator("relation")
    @classmethod
    def _relation(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in RELATIONS:
            raise ValueError(f"relation 必须是 {'/'.join(RELATIONS)} 之一")
        return v


class SubjectOut(CamelModel):
    id: str
    name: str
    relation: str
    color: Optional[str] = None
    avatar: Optional[str] = None
    isDefault: bool = False
    birthday: Optional[str] = None
    gender: Optional[str] = None


class LastSelectedRequest(CamelModel):
    subjectId: str


class SubjectDeleteResult(CamelModel):
    archivedConversations: int = 0
    softDeletedFiles: int = 0


class SubjectStats(CamelModel):
    eventCount: int = 0
    observationCount: int = 0
    documentCount: int = 0
    lastRecordedAt: Optional[str] = None
