"""幂等与乐观锁单测（B-4/E-7：同文档同版本唯一、重复字段入库拦截）。"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.common.ids import new_id
from app.db.models import (
    Account,
    Document,
    DerivedRecord,
    Observation,
    Subject,
)


def _seed_doc(db):
    account = Account(id=new_id(), email="i@example.com", password_hash="x")
    subject = Subject(id=new_id(), account_id=account.id, name="本人", relation="本人")
    db.add_all([account, subject])
    db.flush()
    doc = Document(
        id=new_id(), account_id=account.id, subject_id=subject.id,
        kind="PDF", status="awaiting_review", file_key="k", file_name="f.pdf",
        mime_type="application/pdf", size_bytes=1, content_hash=new_id(),
    )
    db.add(doc)
    db.commit()
    return account, subject, doc


def test_dr_unique_document_version(db):
    account, subject, doc = _seed_doc(db)
    db.add(
        DerivedRecord(
            id=new_id(), document_id=doc.id, subject_id=subject.id,
            version=1, payload={"observations": []}, parser="rule",
        )
    )
    db.commit()
    db.add(
        DerivedRecord(
            id=new_id(), document_id=doc.id, subject_id=subject.id,
            version=1, payload={"observations": []}, parser="rule",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_obs_unique_doc_field_ref(db):
    """B-4：同一文档同一字段来源不重复入库（AC-6.4 幂等）。"""
    from datetime import date

    account, subject, doc = _seed_doc(db)
    db.add(
        Observation(
            id=new_id(), account_id=account.id, subject_id=subject.id,
            display_name="血糖", value_num=5.5, occurred_at=date(2026, 3, 12),
            source_document_id=doc.id, source_field_ref="obs:0",
            is_confirmed=True,
        )
    )
    db.commit()
    db.add(
        Observation(
            id=new_id(), account_id=account.id, subject_id=subject.id,
            display_name="血糖", value_num=6.6, occurred_at=date(2026, 3, 12),
            source_document_id=doc.id, source_field_ref="obs:0",
            is_confirmed=True,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_event_draft_unique(db):
    from app.db.models import MedicalEvent
    from datetime import date

    account, subject, doc = _seed_doc(db)
    db.add(
        MedicalEvent(
            id=new_id(), account_id=account.id, subject_id=subject.id,
            event_type="checkup", occurred_at=date.today(),
            document_id=doc.id, draft_id="dr-1",
        )
    )
    db.commit()
    db.add(
        MedicalEvent(
            id=new_id(), account_id=account.id, subject_id=subject.id,
            event_type="checkup", occurred_at=date.today(),
            document_id=doc.id, draft_id="dr-1",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
