"""重复抽取/重试不得撞唯一键（真实环境实测缺陷）。

复现：对同一 PDF 连续执行两次 process_job（重新抽取）时，
旧实现用 ORM `db.delete()` 删除 document_pages，其 DELETE 会排在 INSERT 之后，
触发 uq_document_pages_document_id_page_no（PostgreSQL 报 UniqueViolation）。
"""

from app.common.ids import new_id
from app.db.models import DocumentPage, ExtractionJob
from app.preprocessing.pipeline import process_job
from tests.conftest import make_pdf_with_text


def _upload(client, auth, content, name, mime):
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": (name, content, mime)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["document"]


def test_second_extraction_replaces_pages(db, client, auth, fake_storage):
    pdf = make_pdf_with_text("2026-03-12 Report\nGLU 5.2 mmol/L 3.9-6.1\nALT 45 U/L 9-50")
    doc = _upload(client, auth, pdf, "lab.pdf", "application/pdf")
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    first_pages = (
        db.query(DocumentPage).filter_by(document_id=doc["id"]).order_by(
            DocumentPage.page_no
        ).all()
    )
    assert len(first_pages) == 1

    # 模拟重新抽取（同文档再跑一次）
    from app.db.models import Document

    document = db.get(Document, doc["id"])
    document.status = "extracting"
    retry_job = ExtractionJob(
        id=new_id(), document_id=doc["id"], state="queued", attempt=1
    )
    db.add(retry_job)
    db.commit()
    process_job(db, retry_job.id)

    db.expire_all()
    second_pages = db.query(DocumentPage).filter_by(document_id=doc["id"]).all()
    assert len(second_pages) == 1
    assert second_pages[0].page_no == 1
    assert db.get(Document, doc["id"]).status == "awaiting_review"
