"""指标字典播种（幂等）。

真实环境缺陷（2026-10-21）：容器启动只跑 alembic 迁移，从未执行种子脚本，
导致 `indicator_dictionary` 为 0 行 —— 所有指标都显示“字典未命中”，
趋势 TOP20 也无法工作。

现在：
- 容器入口在迁移后调用 `ensure_seeded()`（幂等，按 code upsert）；
- 抽取前若发现字典为空，也会自动补齐一次（自愈，避免数据环境差异）。
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.ids import new_id
from app.data.indicator_dictionary import TOP50
from app.db.models import IndicatorDictionary, IndicatorRefRange

logger = logging.getLogger("app.dictionary")


def upsert(db: Session) -> dict:
    """按 code upsert（幂等）；返回统计供启动日志与测试断言。"""
    created = updated = 0
    for code, name_cn, category, unit, aliases, top, low, high, text_range in TOP50:
        row = db.scalar(
            select(IndicatorDictionary).where(IndicatorDictionary.code == code)
        )
        if row is None:
            row = IndicatorDictionary(
                id=new_id(), code=code, name_cn=name_cn, category=category,
                unit=unit or None, aliases=list(aliases), is_top_trend=top, active=True,
            )
            db.add(row)
            db.flush()
            created += 1
        else:
            row.name_cn = name_cn
            row.category = category
            row.unit = unit or None
            # 别名**合并**而非覆盖：
            # - 迁移 0005 会把历史 `*_ABS` 上不重复的别名（如「…绝对值」）并入
            #   规范条目；若这里直接覆盖成 TOP50 的列表，迁移成果会被回冲掉
            #   （真实缺陷，2026-10-23：迁移后别名合并没生效）。
            # - 人工补充的别名同样不能被启动播种清掉。
            merged = list(dict.fromkeys([*(row.aliases or []), *aliases]))
            row.aliases = merged
            row.is_top_trend = top
            row.active = True
            updated += 1

        existing_range = db.scalar(
            select(IndicatorRefRange).where(
                IndicatorRefRange.dictionary_id == row.id,
                IndicatorRefRange.version == 1,
            )
        )
        if existing_range is None and (low is not None or high is not None or text_range):
            db.add(
                IndicatorRefRange(
                    id=new_id(), dictionary_id=row.id, version=1,
                    low_value=low, high_value=high, text_range=text_range,
                    applies_to={},
                )
            )
    db.commit()
    return {
        "total": len(TOP50),
        "created": created,
        "updated": updated,
        "topTrend": len([row for row in TOP50 if row[5]]),
    }


def ensure_seeded(db: Optional[Session] = None) -> dict:
    """幂等确保字典非空；返回 {"alreadyPresent", "created", ...}。

    并发安全：api 与 worker 会同时启动并各跑一次播种，可能撞 uq(code)。
    冲突时回滚重读（对方已插入），并重试一次；仍失败则降级返回空统计，
    由 mapper 的自愈逻辑在首次抽取时补齐。
    """
    own = db is None
    session = db
    if own:
        from app.db.session import SessionLocal

        session = SessionLocal()
    try:
        existing = session.scalar(select(IndicatorDictionary.id).limit(1))
        if existing is not None:
            # 已有数据：仍执行 upsert 以同步医学定稿的最新字段（幂等）
            stats = upsert(session)
            stats["alreadyPresent"] = True
            return stats
        for attempt in range(2):
            try:
                stats = upsert(session)
                stats["alreadyPresent"] = existing is not None
                if stats["created"]:
                    logger.info(
                        "dictionary.seeded created=%s total=%s",
                        stats["created"], stats["total"],
                    )
                return stats
            except IntegrityError:
                session.rollback()
                logger.info("dictionary.seed_conflict attempt=%s (concurrent seed)", attempt + 1)
        return {
            "total": len(TOP50),
            "created": 0,
            "updated": 0,
            "topTrend": len([row for row in TOP50 if row[5]]),
            "alreadyPresent": True,
        }
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("dictionary.seed_failed")
        raise
    finally:
        if own:
            session.close()
