"""Auth 路由（F-01）：/api/auth/*（§4.2 1-4）。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.deps import get_current_account
from app.auth.schemas import LoginRequest, RegisterRequest
from app.db.models import Account
from app.db.session import get_db
from app.common.responses import ok_response

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
def register(data: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """注册并自动登录、建默认 Subject（AC-1.1）。"""
    return ok_response(service.register(db, data), request)


@router.post("/login")
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """登录；失败统一 401 INVALID_CREDENTIALS（AC-1.2）。"""
    return ok_response(service.login(db, data), request)


@router.post("/refresh")
def refresh(
    request: Request,
    account: Account = Depends(get_current_account),
):
    """刷新 token。"""
    return ok_response(service.refresh(account), request)


@router.get("/me")
def me(
    request: Request,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """当前账户 + subjects[] + lastSubjectId。"""
    return ok_response(service.me(db, account), request)
