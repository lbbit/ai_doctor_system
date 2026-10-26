"""鉴权依赖：get_current_account / require_subject / ownership→404（§4.1/§8.2）。"""

from typing import Optional, Type, TypeVar

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import decode_token
from app.common.errors import NotFoundError, SubjectRequiredError, UnauthenticatedError
from app.db.models import Account, Subject
from app.db.session import get_db

T = TypeVar("T")


def get_current_account(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> Account:
    """Bearer JWT → Account；缺失/无效 401 UNAUTHENTICATED；过期 401 TOKEN_EXPIRED（AC-1.3）。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthenticatedError()
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    account_id = payload.get("sub")
    if not account_id:
        raise UnauthenticatedError()
    account = db.get(Account, account_id)
    if account is None or account.status != "active":
        raise UnauthenticatedError()
    request.state.account_id = account.id
    return account


def require_subject(
    db: Session,
    account: Account,
    subject_id: Optional[str],
) -> Subject:
    """写入必须选档（B-1）：缺 subjectId → 422 SUBJECT_REQUIRED；
    非本人/不存在/已删 → 404 NOT_FOUND（K4 防 IDOR）。"""
    if not subject_id:
        raise SubjectRequiredError()
    subject = db.get(Subject, subject_id)
    if (
        subject is None
        or subject.account_id != account.id
        or subject.deleted_at is not None
    ):
        raise NotFoundError("档案对象不存在")
    return subject


def owned_or_404(account: Account, obj: Optional[T], subject_id: Optional[str] = None) -> T:
    """越权/不存在/已软删一律 404（K4/IDOR）。

    软删（`deleted_at` 非空）的对象一律视为不存在：档案删除、重复上传顶替等
    场景留下的行仍在库中，若按 ID 仍可读取详情、签名原件、确认入库，等于绕过
    生命周期策略。404 而非 403，与跨档语义一致，不泄露资源存在性。
    """
    if obj is None:
        raise NotFoundError()
    if getattr(obj, "deleted_at", None) is not None:
        raise NotFoundError()
    owner = getattr(obj, "account_id", None)
    if owner is not None and owner != account.id:
        raise NotFoundError()
    if subject_id is not None and getattr(obj, "subject_id", None) != subject_id:
        raise NotFoundError()
    return obj


def select_owned(
    db: Session,
    account: Account,
    model: Type[T],
    obj_id: str,
    subject_id: Optional[str] = None,
) -> T:
    """按 id 查询 + 归属校验（跨 Account → 404）。

    `subject_id` 非空时同时校验档案归属：同 Account 内不同 Subject 也必须隔离
    （PRD §3 K4 零串档、G3「一个 Account 管理多个 Subject，零串档」）。
    浏览器端会带上当前选中的档案，跨档访问一律 404，不泄露资源存在性。
    """
    obj = db.get(model, obj_id)
    return owned_or_404(account, obj, subject_id)


def resolve_subject_id(
    db: Session,
    account: Account,
    subject_id: Optional[str],
    *,
    required: bool = False,
) -> Optional[str]:
    """解析请求作用域的 Subject：显式传入 → 校验归属；未传 → 取默认/最近使用。

    读接口不带 subjectId 时按"账号默认档案"解释，避免前端漏传参数就放开全部档案。
    """
    if subject_id:
        subject = require_subject(db, account, subject_id)
        return subject.id

    from app.db.models import LastSubjectPreference

    pref = db.get(LastSubjectPreference, account.id)
    if pref is not None and pref.subject_id:
        candidate = db.get(Subject, pref.subject_id)
        if (
            candidate is not None
            and candidate.account_id == account.id
            and candidate.deleted_at is None
        ):
            return candidate.id

    default_subject = db.scalar(
        select(Subject).where(
            Subject.account_id == account.id,
            Subject.deleted_at.is_(None),
        ).order_by(Subject.is_default.desc(), Subject.created_at).limit(1)
    )
    if default_subject is not None:
        return default_subject.id
    if required:
        raise SubjectRequiredError()
    return None
