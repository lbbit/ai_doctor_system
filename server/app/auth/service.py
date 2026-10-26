"""注册/登录/刷新逻辑（F-01；AC-1.1/1.2/1.3）。"""

import logging
import time
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.schemas import LoginRequest, RegisterRequest
from app.auth.security import create_access_token, hash_password, verify_password
from app.common.errors import ConflictError, InvalidCredentialsError, RateLimitedError
from app.common.ids import new_id
from app.config.permissions import assign_role_for_new_account, is_admin
from app.config.settings import settings
from app.db.models import Account, LastSubjectPreference, Profile, Subject

logger = logging.getLogger("app.auth")

# 注册默认建立的“本人”档（AC-1.1）
DEFAULT_SUBJECT_NAME = "本人"


def _get_by_email(db: Session, email: str) -> Optional[Account]:
    return db.scalar(
        select(Account).where(func.lower(Account.email) == email.lower())
    )


def register(db: Session, data: RegisterRequest) -> dict:
    """注册：建 Account + 默认 Subject(本人) + Profile，自动登录（AC-1.1）。"""
    if _get_by_email(db, data.email):
        raise ConflictError(message="该邮箱已注册")

    account = Account(
        id=new_id(),
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name,
        status="active",
        # 首个注册账户自动成为 admin（可管理全局系统配置，§6.9）
        role=assign_role_for_new_account(db),
    )
    db.add(account)
    db.flush()

    subject = Subject(
        id=new_id(),
        account_id=account.id,
        name=DEFAULT_SUBJECT_NAME,
        relation="本人",
        is_default=True,
        color="#3B82F6",
        avatar="🙂",
    )
    db.add(subject)
    db.flush()
    db.add(Profile(subject_id=subject.id, extra={}))

    token = create_access_token(account.id)
    audit_record(
        db,
        action="auth.register",
        resource_type="account",
        resource_id=account.id,
        account_id=account.id,
        after={"email": account.email},
    )
    db.commit()

    logger.info("auth.login mode=register accountId=%s", account.id)
    return {
        "token": token,
        "account": _account_out(account),
        "defaultSubject": _subject_out(subject),
    }


def login(db: Session, data: LoginRequest) -> dict:
    """登录：失败统一 401 INVALID_CREDENTIALS（AC-1.2，不泄露账号存在性）。"""
    account = _get_by_email(db, data.email)
    if account is None or not verify_password(data.password, account.password_hash):
        raise InvalidCredentialsError()
    if account.status != "active":
        raise InvalidCredentialsError()

    account.last_login_at = func.now()
    db.add(account)

    pref = db.get(LastSubjectPreference, account.id)
    token = create_access_token(account.id)
    audit_record(
        db,
        action="auth.login",
        resource_type="account",
        resource_id=account.id,
        account_id=account.id,
    )
    db.commit()

    logger.info("auth.login mode=password accountId=%s", account.id)
    return {
        "token": token,
        "account": _account_out(account),
        "lastSubjectId": pref.subject_id if pref else None,
    }


def refresh(account: Account) -> dict:
    """刷新 token（持有效 token 才能刷新）。"""
    return {"token": create_access_token(account.id)}


def me(db: Session, account: Account) -> dict:
    """当前账户 + 全部 Subject + 上次选中（AC-2.3）。"""
    subjects = (
        db.execute(
            select(Subject)
            .where(Subject.account_id == account.id, Subject.deleted_at.is_(None))
            .order_by(Subject.is_default.desc(), Subject.created_at.asc())
        )
        .scalars()
        .all()
    )
    pref = db.get(LastSubjectPreference, account.id)
    return {
        "account": _account_out(account),
        "subjects": [_subject_out(s) for s in subjects],
        "lastSubjectId": pref.subject_id if pref else None,
    }


def _account_out(a: Account, db: Optional[Session] = None) -> dict:
    return {
        "id": a.id,
        "email": a.email,
        "displayName": a.display_name,
        "status": a.status,
        "role": getattr(a, "role", "user"),
    }


def _subject_out(s: Subject) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "relation": s.relation,
        "color": s.color,
        "avatar": s.avatar,
        "isDefault": s.is_default,
        "birthday": s.birthday.isoformat() if s.birthday else None,
        "gender": s.gender,
    }
