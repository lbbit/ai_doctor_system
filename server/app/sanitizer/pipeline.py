"""出域脱敏编排（§6.2；A-4）：所有出域 LLM 调用前执行（抽取/问诊/对话草稿）。"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import Account, Subject
from app.sanitizer.patterns import Replaced, sanitize_text

logger = logging.getLogger("app.sanitizer")


@dataclass
class SanitizedResult:
    text: str
    replaced: List[Replaced] = field(default_factory=list)

    @property
    def summary(self) -> list:
        """替换摘要（不含明文，写入 Trace 脱敏摘要）。"""
        return [{"type": r.type, "repl": r.repl} for r in self.replaced]


def subject_pii_bundle(
    db: Session, account: Account, subject: Optional[Subject]
) -> tuple[list[str], dict, Optional[str]]:
    """当前 Subject + 同 Account 全部 Subject 名单（姓名精确匹配用）+ 生日。"""
    rows = db.execute(
        select(Subject).where(
            Subject.account_id == account.id,
            Subject.deleted_at.is_(None),
        )
    ).scalars()
    names = [s.name for s in rows if s.name]
    relation_map = {s.name: s.relation for s in rows if s.name}
    birthday = subject.birthday.isoformat() if (subject and subject.birthday) else None
    return names, relation_map, birthday


def sanitize_for_llm(
    text: str,
    *,
    names: Optional[List[str]] = None,
    relation_map: Optional[dict] = None,
    birthday_iso: Optional[str] = None,
) -> SanitizedResult:
    """出域前脱敏；开关关闭（测试/极少数部署）原样返回。"""
    if not text:
        return SanitizedResult(text or "", [])
    if not settings.pii_masking_enabled:
        return SanitizedResult(text, [])
    sanitized, replaced = sanitize_text(
        text,
        names=names,
        relation_map=relation_map,
        birthday_iso=birthday_iso,
    )
    if replaced:
        logger.info(
            "sanitizer.applied types=%s count=%s",
            [r.type for r in replaced], len(replaced),
        )
    return SanitizedResult(sanitized, replaced)


def sanitize_with_subject(
    db: Session,
    account: Account,
    subject: Optional[Subject],
    text: str,
) -> SanitizedResult:
    """便捷入口：自动带入档案 PII 上下文。"""
    names, relation_map, birthday = subject_pii_bundle(db, account, subject)
    return sanitize_for_llm(
        text, names=names, relation_map=relation_map, birthday_iso=birthday
    )
