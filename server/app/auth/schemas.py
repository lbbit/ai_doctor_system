"""Auth 请求/响应 schema（F-01；字段 camelCase）。"""

from typing import List, Optional

from pydantic import Field, field_validator

from app.common.schemas import CamelModel


class RegisterRequest(CamelModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=64)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1] or v.startswith("@"):
            raise ValueError("邮箱格式不正确")
        return v


class LoginRequest(CamelModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class AccountOut(CamelModel):
    id: str
    email: str
    displayName: Optional[str] = None
    status: str = "active"


class RegisterResponse(CamelModel):
    token: str
    account: AccountOut
    defaultSubject: dict


class LoginResponse(CamelModel):
    token: str
    account: AccountOut
    lastSubjectId: Optional[str] = None


class RefreshResponse(CamelModel):
    token: str


class MeResponse(CamelModel):
    account: AccountOut
    subjects: List[dict]
    lastSubjectId: Optional[str] = None
