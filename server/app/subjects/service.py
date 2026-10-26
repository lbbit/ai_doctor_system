"""Subject CRUD / last-selected / 级联删除 / 档案概览（F-02/F-03；§10 权限模型）。"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record as audit_record
from app.auth.deps import require_subject
from app.common.errors import ConflictError, NotFoundError
from app.common.ids import new_id
from app.db.models import (
    Account,
    Conversation,
    Document,
    LastSubjectPreference,
    MedicalEvent,
    Observation,
    Profile,
    Subject,
)
from app.subjects.schemas import SubjectCreate, SubjectUpdate

logger = logging.getLogger("app.subjects")


def list_subjects(db: Session, account: Account) -> List[Subject]:
    return list(
        db.execute(
            select(Subject)
            .where(Subject.account_id == account.id, Subject.deleted_at.is_(None))
            .order_by(Subject.is_default.desc(), Subject.created_at.asc())
        )
        .scalars()
    )


def get_owned_subject(db: Session, account: Account, subject_id: str) -> Subject:
    """归属校验：不存在/非本人/已删 → 404（K4）。"""
    return require_subject(db, account, subject_id)


def create_subject(
    db: Session, account: Account, data: SubjectCreate, request=None
) -> Subject:
    """新建 Subject（AC-2.1），同名冲突 409。"""
    exists = db.scalar(
        select(Subject).where(
            Subject.account_id == account.id,
            Subject.name == data.name,
            Subject.deleted_at.is_(None),
        )
    )
    if exists:
        raise ConflictError(message="同名档案已存在")

    subject = Subject(
        id=new_id(),
        account_id=account.id,
        name=data.name,
        relation=data.relation,
        birthday=data.birthday,
        gender=data.gender,
        color=data.color or "#10B981",
        avatar=data.avatar or "👤",
        is_default=False,
    )
    db.add(subject)
    db.flush()
    db.add(Profile(subject_id=subject.id, extra={}))
    audit_record(
        db,
        action="subject.create",
        resource_type="subject",
        resource_id=subject.id,
        account_id=account.id,
        subject_id=subject.id,
        after={"name": subject.name, "relation": subject.relation},
        request=request,
    )
    db.commit()
    db.refresh(subject)
    logger.info("subject.create accountId=%s subjectId=%s", account.id, subject.id)
    return subject


def update_subject(
    db: Session, account: Account, subject_id: str, data: SubjectUpdate, request=None
) -> Subject:
    """编辑 Subject（审计前后值，AC-14.1）。"""
    subject = get_owned_subject(db, account, subject_id)
    before = {
        "name": subject.name,
        "relation": subject.relation,
        "birthday": subject.birthday.isoformat() if subject.birthday else None,
        "gender": subject.gender,
        "color": subject.color,
        "avatar": subject.avatar,
    }
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(subject, key, value)
    db.flush()
    after = {
        "name": subject.name,
        "relation": subject.relation,
        "birthday": subject.birthday.isoformat() if subject.birthday else None,
        "gender": subject.gender,
        "color": subject.color,
        "avatar": subject.avatar,
    }
    audit_record(
        db,
        action="subject.update",
        resource_type="subject",
        resource_id=subject.id,
        account_id=account.id,
        subject_id=subject.id,
        before=before,
        after=after,
        request=request,
    )
    db.commit()
    db.refresh(subject)
    return subject


def delete_subject(
    db: Session, account: Account, subject_id: str, request=None
) -> dict:
    """删除（级联：会话归档、文件软删、事件/观测软删、审计保留）。"""
    subject = get_owned_subject(db, account, subject_id)
    now = datetime.now(timezone.utc)

    # 会话归档（状态机 active → archived）
    conversations = list(
        db.execute(
            select(Conversation).where(
                Conversation.subject_id == subject.id,
                Conversation.status == "active",
                Conversation.deleted_at.is_(None),
            )
        ).scalars()
    )
    for conv in conversations:
        conv.status = "archived"
        conv.archived_at = now

    # 文件软删（B-3：原件不物理删除）
    documents = list(
        db.execute(
            select(Document).where(
                Document.subject_id == subject.id,
                Document.deleted_at.is_(None),
            )
        ).scalars()
    )
    for doc in documents:
        doc.deleted_at = now

    # 事件/观测软删（该档案数据不再对外可见）
    for ev in db.execute(
        select(MedicalEvent).where(
            MedicalEvent.subject_id == subject.id,
            MedicalEvent.deleted_at.is_(None),
        )
    ).scalars():
        ev.deleted_at = now
    for ob in db.execute(
        select(Observation).where(
            Observation.subject_id == subject.id,
            Observation.deleted_at.is_(None),
        )
    ).scalars():
        ob.deleted_at = now

    # 档案本身软删
    subject.deleted_at = now

    # last-selected 若指向本档则清空
    pref = db.get(LastSubjectPreference, account.id)
    if pref and pref.subject_id == subject.id:
        db.delete(pref)

    audit_record(
        db,
        action="subject.delete",
        resource_type="subject",
        resource_id=subject.id,
        account_id=account.id,
        subject_id=subject.id,
        before={"name": subject.name},
        after={"deleted": True},
        request=request,
    )
    db.commit()
    return {
        "archivedConversations": len(conversations),
        "softDeletedFiles": len(documents),
    }


def set_last_selected(db: Session, account: Account, subject_id: str) -> str:
    """记忆上次选档（AC-2.3）。"""
    require_subject(db, account, subject_id)
    pref = db.get(LastSubjectPreference, account.id)
    if pref is None:
        pref = LastSubjectPreference(account_id=account.id, subject_id=subject_id)
        db.add(pref)
    else:
        pref.subject_id = subject_id
    db.commit()
    return subject_id


def subject_stats(db: Session, account: Account, subject_id: str) -> dict:
    """档案概览（§4.2 #43）。"""
    subject = get_owned_subject(db, account, subject_id)
    event_count = (
        db.scalar(
            select(func.count(MedicalEvent.id)).where(
                MedicalEvent.subject_id == subject.id,
                MedicalEvent.deleted_at.is_(None),
            )
        )
        or 0
    )
    observation_count = (
        db.scalar(
            select(func.count(Observation.id)).where(
                Observation.subject_id == subject.id,
                Observation.deleted_at.is_(None),
            )
        )
        or 0
    )
    document_count = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.subject_id == subject.id,
                Document.deleted_at.is_(None),
            )
        )
        or 0
    )
    last_recorded_at = db.scalar(
        select(func.max(Observation.recorded_at)).where(
            Observation.subject_id == subject.id,
            Observation.deleted_at.is_(None),
        )
    )
    return {
        "eventCount": int(event_count),
        "observationCount": int(observation_count),
        "documentCount": int(document_count),
        "lastRecordedAt": last_recorded_at.isoformat() if last_recorded_at else None,
    }


def subject_out(s: Subject) -> dict:
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
