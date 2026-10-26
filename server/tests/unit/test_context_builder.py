"""上下文组装单测（AC-9.2、§6.1 分层、L3 恒空、A-3 引用池约束）。"""

from datetime import date, timedelta

from app.chat.context_builder import build_context, render_system_context
from app.common.ids import new_id
from app.db.models import (
    Account,
    Document,
    IndicatorDictionary,
    MedicalEvent,
    Observation,
    Profile,
    Subject,
)


def _seed(db):
    account = Account(id=new_id(), email="ctx@example.com", password_hash="x")
    subject = Subject(
        id=new_id(), account_id=account.id, name="王五",
        relation="母亲", birthday=date(1955, 5, 1),
    )
    db.add_all([account, subject])
    db.flush()
    db.add(Profile(subject_id=subject.id, allergies="青霉素",
                   chronic_diseases="高血压", medications="氨氯地平"))
    d = IndicatorDictionary(
        id=new_id(), code="GLU_FASTING", name_cn="空腹血糖",
        category="生化", unit="mmol/L", aliases=["血糖"],
        is_top_trend=True, active=True,
    )
    doc = Document(
        id=new_id(), account_id=account.id, subject_id=subject.id,
        kind="PDF", status="confirmed", file_key="k", file_name="血糖报告.pdf",
        mime_type="application/pdf", size_bytes=10, content_hash="h1",
    )
    db.add_all([d, doc])
    db.flush()
    today = date.today()
    for i, val in enumerate([5.5, 6.4, 7.1]):
        db.add(
            Observation(
                id=new_id(), account_id=account.id, subject_id=subject.id,
                dictionary_id=d.id, code="GLU_FASTING", display_name="空腹血糖",
                value_num=val, unit="mmol/L", occurred_at=today - timedelta(days=30 * i),
                source_document_id=doc.id, is_confirmed=True,
            )
        )
    db.add(
        MedicalEvent(
            id=new_id(), account_id=account.id, subject_id=subject.id,
            event_type="visit", occurred_at=today, title="门诊复查",
            event_source="document", document_id=doc.id,
        )
    )
    db.commit()
    return account, subject, doc


def test_layer_build(db):
    account, subject, doc = _seed(db)
    ctx = build_context(db, account, subject, "血糖最近怎么样")
    assert ctx.layers["L0"], "L0 必须每轮必带"
    assert "青霉素" in ctx.layers["L0"][0]["text"]
    assert ctx.layers["L1"], "话题命中血糖应有 L1 序列"
    assert ctx.layers["L1"][0]["code"] == "GLU_FASTING"
    assert ctx.layers["L1"][0]["series"], "序列点存在"
    assert ctx.layers["L1"][0]["changeRatePct"] is not None  # A-6 SQL 计算
    assert ctx.layers["L2"], "近30天事件应进 L2"
    assert ctx.layers["L3"] == []  # P0 恒空（P1 pgvector）


def test_citation_pool_requires_document(db):
    account, subject, doc = _seed(db)
    ctx = build_context(db, account, subject, "血糖")
    assert ctx.citation_pool, "有原件回链的实体入池"
    assert all(c.document_id for c in ctx.citation_pool)  # A-3

    # 无 document_id 的事件不入池
    orphan = MedicalEvent(
        id=new_id(), account_id=account.id, subject_id=subject.id,
        event_type="self_reported", occurred_at=date.today(),
        title="自述", event_source="chat", document_id=None,
    )
    db.add(orphan)
    db.commit()
    ctx2 = build_context(db, account, subject, "自述")
    assert all(c.ref_id != orphan.id for c in ctx2.citation_pool)


def test_render_contains_sections(db):
    account, subject, doc = _seed(db)
    ctx = build_context(db, account, subject, "血糖趋势")
    text = render_system_context(ctx)
    assert "L0" in text and "L1" in text and "L2" in text
    assert "依据：" in text or "可引用清单" in text
    assert "禁止" in text or "不能" in text  # 定位边界 A-5 存在
