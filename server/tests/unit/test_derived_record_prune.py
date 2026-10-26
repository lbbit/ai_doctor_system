"""草稿版本保留策略：`derived_records` 每次重抽新增一行，必须防止无限堆积。

背景（2026-10-23）：`pipeline` 以 `max(version)+1` 写新草稿行是**有意的**
（审计要能追溯每次抽取结果），但反复重抽会一直加行——实测单份已到 7 版，
每版还带最多 200KB `source_text`。需要"保留最近 N 版"。
"""

import pytest
from sqlalchemy import select

from app.common.ids import new_id
from app.db.models import DerivedRecord
from app.preprocessing.pipeline import (
    DEFAULT_KEEP_VERSIONS,
    prune_derived_records,
)


def _mk(db, document_id, subject_id, version, *, confirmed=False, text="x"):
    row = DerivedRecord(
        id=new_id(),
        document_id=document_id,
        subject_id=subject_id,
        version=version,
        payload={"observations": []},
        parser="vlm",
        source_text=text,
        created_by="system",
    )
    if confirmed:
        from datetime import datetime, timezone

        row.confirmed_at = datetime.now(timezone.utc)
    db.add(row)
    return row


def _versions(db, document_id):
    return sorted(
        db.scalars(
            select(DerivedRecord.version).where(
                DerivedRecord.document_id == document_id
            )
        ).all()
    )


def test_keeps_latest_n_versions(db, auth):
    doc_id = "doc-prune-1"
    for v in range(1, 9):          # 8 版
        _mk(db, doc_id, auth["subjectId"], v)
    db.commit()

    removed = prune_derived_records(db, doc_id, keep=3)
    db.commit()

    assert removed == 5
    assert _versions(db, doc_id) == [6, 7, 8]   # 保留最新 3 版


def test_no_op_when_within_limit(db, auth):
    doc_id = "doc-prune-2"
    for v in (1, 2, 3):
        _mk(db, doc_id, auth["subjectId"], v)
    db.commit()

    assert prune_derived_records(db, doc_id, keep=DEFAULT_KEEP_VERSIONS) == 0
    assert _versions(db, doc_id) == [1, 2, 3]


def test_never_deletes_confirmed_versions(db, auth):
    """已确认版本是 HITL 审计线索，即使超出保留窗口也不能删。"""
    doc_id = "doc-prune-3"
    _mk(db, doc_id, auth["subjectId"], 1, confirmed=True)   # 早期确认版
    for v in range(2, 8):
        _mk(db, doc_id, auth["subjectId"], v)
    db.commit()

    prune_derived_records(db, doc_id, keep=2)
    db.commit()

    left = _versions(db, doc_id)
    assert 1 in left, left            # 已确认版保留
    assert left == [1, 6, 7], left    # 最新 2 版 + 确认版


def test_keep_zero_is_noop(db, auth):
    """`keep=0` 视为不清理，避免误传把历史全删。"""
    doc_id = "doc-prune-4"
    for v in range(1, 5):
        _mk(db, doc_id, auth["subjectId"], v)
    db.commit()

    assert prune_derived_records(db, doc_id, keep=0) == 0
    assert _versions(db, doc_id) == [1, 2, 3, 4]


def test_does_not_touch_other_documents(db, auth):
    keep_doc, other_doc = "doc-prune-5a", "doc-prune-5b"
    for v in range(1, 6):
        _mk(db, keep_doc, auth["subjectId"], v)
        _mk(db, other_doc, auth["subjectId"], v)
    db.commit()

    prune_derived_records(db, keep_doc, keep=2)
    db.commit()

    assert _versions(db, keep_doc) == [4, 5]
    assert _versions(db, other_doc) == [1, 2, 3, 4, 5]   # 另一份不受影响


def test_idempotent(db, auth):
    doc_id = "doc-prune-6"
    for v in range(1, 6):
        _mk(db, doc_id, auth["subjectId"], v)
    db.commit()

    first = prune_derived_records(db, doc_id, keep=2)
    db.commit()
    second = prune_derived_records(db, doc_id, keep=2)
    db.commit()

    assert first == 3
    assert second == 0
    assert _versions(db, doc_id) == [4, 5]
