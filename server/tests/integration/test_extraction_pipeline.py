"""抽取链集成测试（离线降级链 vlm→rule→mock、E-1 失败路径、端到端确认闭环）。"""

from app.common.ids import new_id
from app.db.models import DerivedRecord, ExtractionJob, MedicalEvent, Observation
from app.preprocessing.pipeline import process_job
from tests.conftest import make_pdf_with_text, make_png_bytes


def _upload(client, auth, content, name, mime):
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, content, mime)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


def test_extraction_produces_markdown_asset_and_anchors(
    db, client, auth, fake_storage
):
    """抽取完成后应产出**版本化 Markdown 副本**与页级锚点（R1 T103/T104）。

    这是"输入文档要存档、也要处理成可读副本"的核心落地：
    - 副本登记为 `markdown` 角色资产，版本号自增；
    - 内容含 front matter 与页锚点；
    - 页块（来源锚点）落库，可定位到页；
    - 副本生成失败**不阻断**抽取（派生产物可重建）。
    """
    from app.db.models import Document, DocumentAsset, DocumentBlock
    from app.documents import assets as asset_service

    content = make_pdf_with_text(
        "2026-03-12 Checkup Report\nGLU 5.2 mmol/L 3.9-6.1\nALT 45 U/L 9-50"
    )
    doc = _upload(client, auth, content, "lab.pdf", "application/pdf")
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()

    process_job(db, job.id)

    db.expire_all()
    # Markdown 资产已登记
    assets = (
        db.query(DocumentAsset)
        .filter_by(document_id=doc["id"], role="markdown")
        .all()
    )
    assert assets, "抽取后应产出 Markdown 副本资产"
    assert assets[0].version == 1
    assert assets[0].generator.startswith("markdown/")

    # 内容可读且带锚点
    asset, data = asset_service.read_asset(
        db, db.get(Document, doc["id"]), "markdown"
    )
    text = data.decode("utf-8")
    assert text.startswith("---\n")
    assert "documentId:" in text
    assert "<!-- source:page=" in text

    # 页块锚点已落库
    blocks = db.query(DocumentBlock).filter_by(document_id=doc["id"]).all()
    assert blocks, "应落页级来源锚点"
    assert blocks[0].page_no >= 1
    # 无可靠坐标时只给页级锚点（不伪造 bbox）
    assert blocks[0].anchor_status in ("page_only", "region")
    if blocks[0].anchor_status == "page_only":
        assert blocks[0].x is None

    # 文档状态仍是待确认（副本生成不改变处理状态）
    doc_row = db.get(Document, doc["id"])
    assert doc_row.processing_status == "awaiting_review"


def test_markdown_asset_version_increments_on_reextract(
    db, client, auth, fake_storage
):
    """重新抽取产生**新版本**副本，旧版保留（可对比）。"""
    from app.db.models import Document, DocumentAsset

    content = make_pdf_with_text("2026-03-12 Report\nGLU 5.2 mmol/L 3.9-6.1")
    doc = _upload(client, auth, content, "lab.pdf", "application/pdf")
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    resp = client.post(
        f"/api/documents/{doc['id']}/derived-record/reextract",
        headers=auth["headers"],
    )
    assert resp.status_code == 200, resp.text
    new_job_id = resp.json()["data"]["job"]["id"]
    process_job(db, new_job_id)

    db.expire_all()
    assets = (
        db.query(DocumentAsset)
        .filter_by(document_id=doc["id"], role="markdown")
        .order_by(DocumentAsset.version)
        .all()
    )
    assert len(assets) >= 2, "重抽应新增版本副本"
    assert [a.version for a in assets] == sorted(a.version for a in assets)


def test_pdf_rule_parse_offline(db, client, auth, fake_storage):
    """离线（LLM 不可用）：PDF 文本 → RuleParser 抽取 → awaiting_review → 确认入库闭环。"""
    content = make_pdf_with_text(
        "2026-03-12 Checkup Report\nGLU 5.2 mmol/L 3.9-6.1\nALT 45 U/L 9-50"
    )
    doc = _upload(client, auth, content, "lab.pdf", "application/pdf")
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()

    process_job(db, job.id)

    db.expire_all()
    row = db.query(type(job)).filter_by(document_id=doc["id"]).first()
    assert row.state == "succeeded"
    assert row.parser == "rule"
    from app.db.models import Document

    doc_row = db.get(Document, doc["id"])
    assert doc_row.status == "awaiting_review"
    assert doc_row.doc_type in ("lab_report", "other")

    dr = db.query(DerivedRecord).filter_by(document_id=doc["id"]).one()
    assert dr.version == 1
    assert dr.payload["report_date"] == "2026-03-12"  # AC-5.3 报告内日期
    obs = dr.payload["observations"]
    assert any(o["raw_name"] == "GLU" and o["value"] == 5.2 for o in obs)
    # payload events 为空 → 只需 obs 决策；确认时合成承载事件（AC-6.2）
    decisions = [{"fieldRef": f"obs:{i}", "action": "confirm"}
                 for i in range(len(obs))]
    resp = client.post(
        f"/api/documents/{doc['id']}/derived-record/confirm",
        headers=auth["headers"],
        json={"version": 1, "decisions": decisions},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["documentStatus"] == "confirmed"

    # 时间线可见（AC-6.2 → 时间线即时可见）
    tl = client.get(f"/api/timeline/events?subjectId={auth['subjectId']}",
                    headers=auth["headers"])
    assert tl.status_code == 200
    groups = tl.json()["data"]["groups"]
    assert groups and groups[0]["items"]


def test_image_mock_fallback_offline(db, client, auth):
    """离线图片：vlm 不可用 → rule 无文本失败 → mock 产出占位草稿（演示闭环）。"""
    doc = _upload(client, auth, make_png_bytes(), "scan.png", "image/png")
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    dr = db.query(DerivedRecord).filter_by(document_id=doc["id"]).one()
    assert dr.parser == "mock"
    assert dr.payload["mock"] is True
    from app.db.models import Document

    assert db.get(Document, doc["id"]).status == "awaiting_review"

    # mock 草稿可确认（编辑后入库）→ 时间线出现明确标注的占位事件
    resp = client.post(
        f"/api/documents/{doc['id']}/derived-record/confirm",
        headers=auth["headers"],
        json={"version": 1, "decisions": [
            {"fieldRef": "event:0", "action": "correct",
             "value": {"title": "（演示）上传的报告", "narrative": "离线占位记录"}},
        ]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["documentStatus"] == "confirmed"


def test_corrupt_file_marks_failed(db, client, auth):
    """E-1 解析失败：doc→failed + failure_reason + job failed。"""
    from app.db.models import Document

    bad_pdf = b"%PDF-1.4 this-is-not-a-real-pdf"
    doc = _upload(client, auth, bad_pdf, "broken.pdf", "application/pdf")
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    doc_row = db.get(Document, doc["id"])
    assert doc_row.status == "failed"
    assert doc_row.failure_reason
    job_row = db.get(ExtractionJob, job.id)
    assert job_row.state == "failed"
