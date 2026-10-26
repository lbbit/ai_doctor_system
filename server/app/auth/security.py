"""密码哈希与 JWT 签发/校验（F-01；§8.5 JWT 配置）。"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.common.errors import TokenExpiredError, UnauthenticatedError
from app.config.settings import settings


def hash_password(plain: str) -> str:
    """bcrypt 密码哈希。"""
    from passlib.hash import bcrypt

    return bcrypt.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码（失败不抛异常）。"""
    from passlib.hash import bcrypt

    try:
        return bcrypt.verify(plain, hashed)
    except ValueError:
        return False


def create_access_token(account_id: str, expires_minutes: Optional[int] = None) -> str:
    """签发 JWT（默认 7d，§8.5）。"""
    now = datetime.now(timezone.utc)
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    payload = {
        "sub": account_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "iss": "ai-doctor",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict[str, Any]:
    """解码 JWT；过期 → TOKEN_EXPIRED；其它无效 → UNAUTHENTICATED（AC-1.3）。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except JWTError as exc:
        raise UnauthenticatedError() from exc
