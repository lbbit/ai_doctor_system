"""红旗规则 schema（F-11）。"""

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from app.common.schemas import CamelModel

EVENT_TYPES_NOTE = "keywords 为子串或正则串；context_logic 结构示例见 §6.3"


class RuleCreate(CamelModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=64)
    category: str = "成人急症"
    keywords: List[str] = Field(min_length=1, max_length=50)
    keywordLogic: str = "any"
    contextLogic: Optional[Dict[str, Any]] = None
    severity: str = "high"
    message: str = Field(min_length=4, max_length=500)
    action: str = "refer_now"
    enabled: bool = True

    @field_validator("keywordLogic")
    @classmethod
    def _logic(cls, v: str) -> str:
        if v not in ("any", "all"):
            raise ValueError("keywordLogic 必须是 any/all")
        return v

    @field_validator("severity")
    @classmethod
    def _sev(cls, v: str) -> str:
        if v not in ("high", "medium"):
            raise ValueError("severity 必须是 high/medium")
        return v


class RuleUpdate(CamelModel):
    name: Optional[str] = None
    category: Optional[str] = None
    keywords: Optional[List[str]] = None
    keywordLogic: Optional[str] = None
    contextLogic: Optional[Dict[str, Any]] = None
    severity: Optional[str] = None
    message: Optional[str] = None
    action: Optional[str] = None
    enabled: Optional[bool] = None


class RuleOut(CamelModel):
    id: str
    code: str
    name: str
    category: str
    keywords: List[str]
    keywordLogic: str
    contextLogic: Optional[dict] = None
    severity: str
    message: str
    action: str
    enabled: bool
    version: int


class RuleTestRequest(CamelModel):
    text: str = Field(min_length=1, max_length=5000)
