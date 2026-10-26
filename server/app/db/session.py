"""engine / SessionLocal / get_db（同步 session，P0 足够）。"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings

# SQLite 兼容（本地无 PG 时跑测试）：转发外键 + 同步 driver
_connect_args: dict = {}
_url = settings.database_url
if _url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
elif _url.startswith("postgresql"):
    # 依赖不可达时快速失败（探活/启动不挂起）
    _connect_args = {"connect_timeout": 3}

engine = create_engine(
    _url,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级 Session，异常自动 rollback。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session() -> Session:
    """非请求上下文（worker/脚本）手动管理的 Session。"""
    return SessionLocal()
