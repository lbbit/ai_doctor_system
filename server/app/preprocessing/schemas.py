"""HITL 确认 schema（F-06；§4.2 #17-19）。

fieldRef 约定：
  - `event:{index}` —— payload.events[index]（整事件决策）
  - `obs:{index}`   —— payload.observations[index]（整字段决策，字段级状态机）
"""

from typing import Any, List, Literal, Optional

from pydantic import Field, field_validator

from app.common.schemas import CamelModel

Action = Literal["confirm", "correct", "reject"]


class DecisionIn(CamelModel):
    fieldRef: str = Field(min_length=1, max_length=64)
    action: Action
    value: Optional[Any] = None      # action=correct 时的编辑值
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("fieldRef")
    @classmethod
    def _ref(cls, v: str) -> str:
        if not (v.startswith("event:") or v.startswith("obs:")):
            raise ValueError("fieldRef 必须形如 event:{i} 或 obs:{i}")
        return v


class ConfirmRequest(CamelModel):
    version: int = Field(ge=1, description="DerivedRecord 版本（乐观并发控制）")
    decisions: List[DecisionIn] = Field(min_length=1)


class DerivedRecordOut(CamelModel):
    id: str
    version: int
    payload: dict
    parser: str
    confidenceSummary: Optional[dict] = None


class ConfirmResult(CamelModel):
    documentStatus: str
    events: List[dict]
    observations: List[dict]
    auditId: Optional[str] = None
