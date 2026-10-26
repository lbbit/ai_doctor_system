"""融合链路集成测试：表格覆盖不足时，必须以表格为基准并保留其数值。

真实缺陷（2026-10-21，2021_0907.pdf）：表格只抽到 55 项，视觉抽到 142 项；
早期实现直接采用视觉结果，导致表格里更准的参考区间/单位被丢弃，
甚至整份结果退化为“只有名字、没有数值”的条目。
"""

import pytest

from app.common.ids import new_id
from app.db.models import DerivedRecord, ExtractionJob
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


def test_table_wins_when_visual_also_succeeds(db, client, auth, monkeypatch):
    """`table_first` 策略下：表格为基准融合，表格数值不得被覆盖。

    注意：默认策略已改为 `vlm_first`（用户要求视觉优先），本用例验证的是
    **回滚策略**的行为，所以必须显式声明 `extract.parser_strategy=table_first`
    ——不能依赖默认值，否则策略切换会把这条用例变成假失败。
    """
    from app.config import resolver

    monkeypatch.setattr(
        resolver, "get_str",
        lambda db_, key, default=None, **kw: (
            "table_first" if key == "extract.parser_strategy" else default
        ),
    )

    from app.preprocessing.parsers import table_parser, vlm_parser

    table_obs = [
        {
            "raw_name": "血红蛋白",
            "value": 128.0,
            "unit": "g/L",
            "ref_low": 130.0,
            "ref_high": 175.0,
            "ref_text": None,
            "abnormal_flag": "L",
            "occurred_at": "2026-03-12",
            "confidence": 0.92,
            "bbox": None,
        },
        {
            "raw_name": "白细胞计数",
            "value": 4.4,
            "unit": "10^9/L",
            "ref_low": 3.5,
            "ref_high": 9.5,
            "ref_text": None,
            "abnormal_flag": "N",
            "occurred_at": "2026-03-12",
            "confidence": 0.92,
            "bbox": None,
        },
    ]
    vlm_obs = [
        # 视觉把血红蛋白读成“无值”，若覆盖表格就会丢失数值
        {
            "raw_name": "血红蛋白",
            "value": None,
            "unit": "g/L",
            "ref_low": None,
            "ref_high": None,
            "ref_text": None,
            "abnormal_flag": "L",
            "occurred_at": "2026-03-12",
            "confidence": 0.9,
            "bbox": None,
        },
        {
            "raw_name": "总胆固醇",
            "value": 5.94,
            "unit": "mmol/L",
            "ref_low": 3.0,
            "ref_high": 5.2,
            "ref_text": None,
            "abnormal_flag": "H",
            "occurred_at": "2026-03-12",
            "confidence": 0.95,
            "bbox": None,
        },
    ]

    class FakeTable:
        name = "table"

        def available(self):
            return True

        def parse(self, inp):
            return {
                "parser_name": "table",
                "report_date": "2026-03-12",
                "doc_type": "lab_report",
                "events": [],
                "observations": table_obs,
                "source_text": "",
                "success": True,
                "error": None,
            }

    class FakeVlm:
        name = "vlm"

        def available(self):
            return True

        def parse(self, inp):
            return {
                "parser_name": "vlm",
                "report_date": "2026-03-12",
                "doc_type": "lab_report",
                "events": [{"type": "checkup", "title": "年度体检",
                            "occurred_at": "2026-03-12", "confidence": 0.9}],
                "observations": vlm_obs,
                "source_text": "",
                "success": True,
                "error": None,
            }

    monkeypatch.setattr(
        table_parser, "TableParser", lambda: FakeTable()
    )
    monkeypatch.setattr(vlm_parser, "VlmParser", lambda: FakeVlm())

    import app.preprocessing.parsers as parsers_mod

    monkeypatch.setitem(parsers_mod.PARSERS, "table", FakeTable())
    monkeypatch.setitem(parsers_mod.PARSERS, "vlm", FakeVlm())

    pdf = make_pdf_with_text("2026-03-12 Report\nGLU 5.2 mmol/L 3.9-6.1")
    doc = _upload(client, auth, pdf, "mix.pdf", "application/pdf")
    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    dr = db.query(DerivedRecord).filter_by(document_id=doc["id"]).one()
    by_name = {o["raw_name"]: o for o in dr.payload["observations"]}

    # 表格的数值被保留（未被视觉的空值覆盖）
    assert by_name["血红蛋白"]["value"] == 128.0
    assert by_name["白细胞计数"]["value"] == 4.4
    # 视觉补上了表格漏掉的项
    assert by_name["总胆固醇"]["value"] == 5.94
    assert dr.payload.get("supplemented") == 1
    assert set(dr.payload.get("parsers_used") or []) == {"table", "vlm"}


def test_merge_fills_blank_value_from_secondary():
    """数值兜底：primary 漏读（value=None）时，必须用 secondary 的数值补上。

    真实风险：`vlm_first` 下 VLM 把某项读成"无值"，而表格解析拿到了真实数值。
    若合并只"保留 primary"，数值就会**永久丢失**（历史上 2021_0907.pdf 出现过
    "只有名字、没有数值"的条目）。这条用例锁住该缺陷。
    """
    from app.preprocessing.merger import merge_payloads

    primary = {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [
            {"raw_name": "血红蛋白", "value": None, "unit": "g/L"},
            {"raw_name": "白细胞计数", "value": "", "unit": "10^9/L"},
            {"raw_name": "血小板计数", "value": 0, "unit": "10^9/L"},
        ],
        "mock": False,
    }
    secondary = {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [
            {"raw_name": "血红蛋白", "value": 128.0, "unit": "g/L"},
            {"raw_name": "白细胞计数", "value": 4.4, "unit": "10^9/L"},
            {"raw_name": "血小板计数", "value": 231, "unit": "10^9/L"},
        ],
        "mock": False,
    }

    merged = merge_payloads(primary, secondary)
    by_name = {o["raw_name"]: o for o in merged["observations"]}

    # 空值被补上，并留痕可审计
    assert by_name["血红蛋白"]["value"] == 128.0
    assert by_name["血红蛋白"]["value_filled_from"] == "vlm"
    assert by_name["白细胞计数"]["value"] == 4.4
    # 0 是合法数值，不能被当成"空"覆盖掉
    assert by_name["血小板计数"]["value"] == 0
    assert "value_filled_from" not in by_name["血小板计数"]
