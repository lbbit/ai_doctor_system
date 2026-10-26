"""草稿不包含排版噪声（用户不该逐条拒绝「T1/年0/每3」）。

真实体验缺陷（2026-10-21 E2E 实测）：
确认卡片里出现 `T1`、`年0`、`每3` 等表格排版碎片，虽然已被标记
`align_rejected`，但仍要求用户逐条"拒绝"才能完成入库——这不是用户的责任。
"""

from app.common.ids import new_id
from app.db.models import DerivedRecord, ExtractionJob
from app.preprocessing.pipeline import process_job
from tests.conftest import make_pdf_with_text


def _obs(name, value):
    return {
        "raw_name": name,
        "display_name": name,
        "code": None,
        "dictionary_id": None,
        "value": value,
        "unit": None,
        "ref_low": None,
        "ref_high": None,
        "ref_text": None,
        "abnormal_flag": None,
        "occurred_at": "2021-09-07",
        "confidence": 0.9,
        "bbox": None,
        "status": "proposed",
    }


def test_pipeline_filters_layout_noise_from_draft(db, client, auth, fake_storage, monkeypatch):
    """抽取产出的草稿里不得出现排版噪声。"""
    import app.preprocessing.parsers as parsers_mod

    class FakeParser:
        name = "table"

        def available(self):
            return True

        def parse(self, inp):
            return {
                "parser_name": "table",
                "report_date": "2021-09-07",
                "doc_type": "lab_report",
                "events": [],
                "observations": [
                    _obs("血红蛋白", 125.0),
                    _obs("T1", "52170014"),
                    _obs("年0", 9),
                    _obs("每3", "-6"),
                ],
                "source_text": "",
                "success": True,
                "error": None,
            }

    monkeypatch.setitem(parsers_mod.PARSERS, "table", FakeParser())

    pdf = make_pdf_with_text("2021-09-07 Report\nHGB 125 g/L")
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("lab.pdf", pdf, "application/pdf")},
    )
    doc = resp.json()["data"]["document"]
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    dr = db.query(DerivedRecord).filter_by(document_id=doc["id"]).one()
    names = [o["raw_name"] for o in dr.payload["observations"]]
    assert names == ["血红蛋白"], names
    assert dr.payload.get("noise_filtered") == 3


def test_pipeline_keeps_legit_short_names(db, client, auth, fake_storage, monkeypatch):
    """合法短名（钾/钙/身高）不能被噪声过滤误伤。"""
    import app.preprocessing.parsers as parsers_mod

    class FakeParser:
        name = "table"

        def available(self):
            return True

        def parse(self, inp):
            return {
                "parser_name": "table",
                "report_date": "2021-09-07",
                "doc_type": "lab_report",
                "events": [],
                "observations": [
                    _obs("钾", 4.2),
                    _obs("钙", 2.35),
                    _obs("身高", 180.5),
                ],
                "source_text": "",
                "success": True,
                "error": None,
            }

    monkeypatch.setitem(parsers_mod.PARSERS, "table", FakeParser())

    pdf = make_pdf_with_text("2021-09-07 Report\nK 4.2")
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("lab2.pdf", pdf, "application/pdf")},
    )
    doc = resp.json()["data"]["document"]
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    dr = db.query(DerivedRecord).filter_by(document_id=doc["id"]).one()
    names = {o["raw_name"] for o in dr.payload["observations"]}
    assert {"钾", "钙", "身高"} <= names
