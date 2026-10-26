"""真实报告重复项回归（2021_0907.pdf 实测：144 项中 11 组重复）。

用户可见症状：确认卡片里「体重指数 36」和「体重指数 32.4」并列，
「总胆固醇 4.67（正常）」和「总胆固醇 5.94（偏高）」并列，无法判断该信哪个。

根因有两层：
1. 融合发生在字典映射之前 → 别名（体重指数/身体质量指数）无法归并；
2. 视觉补漏会带入「阳性结果汇总」页只有结论没有数值的行。
"""

import pathlib

import pytest
from sqlalchemy import select

from app.db.models import DerivedRecord
from app.preprocessing.merger import dedupe_observations
from app.preprocessing.parsers.table_parser import (
    extract_pdf_tables,
    table_rows_to_observations,
)


REAL_PDF = pathlib.Path(__file__).resolve().parents[3] / (
    "docs/testinfo/检查单/lbb/2021_0907.pdf"
)


def _key(obs):
    return (obs.get("raw_name"), obs.get("unit"), obs.get("occurred_at"))


def test_real_table_rows_have_no_duplicates():
    if not REAL_PDF.exists():
        pytest.skip("真实样本不存在（docs/testinfo 未纳入版本控制）")
    tables = extract_pdf_tables(REAL_PDF.read_bytes())
    obs = table_rows_to_observations(tables, report_date="2021-09-07")
    # 该样本含两张「历年对比」表（2020/2021 两期），会贡献 5 组同名不同值；
    # 正确做法是跳过它们，因此这里同时断言数量与无重复。
    assert len(obs) >= 45
    keys = [_key(o) for o in obs]
    assert len(keys) == len(set(keys)), "表格解析本身不应产生同行重复"


def test_dedupe_collapses_real_conflicting_pairs():
    """真实冲突对：同日、同名、同单位但数值不同 → 只保留信息更完整的一条。"""
    rows = [
        {
            "raw_name": "总胆固醇", "display_name": "总胆固醇", "code": "TC",
            "value": 4.67, "unit": "mmol/L", "ref_low": 3.0, "ref_high": 5.2,
            "abnormal_flag": "N", "occurred_at": "2021-09-07", "confidence": 0.92,
        },
        {
            "raw_name": "总胆固醇", "display_name": "总胆固醇", "code": "TC",
            "value": 5.94, "unit": "mmol/L", "ref_low": 3.0, "ref_high": 5.2,
            "abnormal_flag": "H", "occurred_at": "2021-09-07", "confidence": 0.98,
        },
    ]
    out, dropped = dedupe_observations(rows)
    assert dropped == 1
    assert len(out) == 1


def test_pipeline_dedupes_before_user_sees_draft(db, client, auth, fake_storage):
    """pipeline 产出的草稿里不得再有同日同名同单位重复项。"""
    from app.preprocessing.pipeline import process_job
    from tests.conftest import make_pdf_with_text

    pdf = make_pdf_with_text(
        "2026-03-12 Report\nGLU 5.2 mmol/L 3.9-6.1\nALT 45 U/L 9-50\nTC 5.9 mmol/L 3.0-5.2"
    )
    resp = client.post(
        "/api/documents",
        headers=auth["headers"],
        data={"subjectId": auth["subjectId"]},
        files={"file": ("lab.pdf", pdf, "application/pdf")},
    )
    doc = resp.json()["data"]["document"]
    from app.db.models import ExtractionJob

    job = db.query(ExtractionJob).filter_by(document_id=doc["id"]).one()
    process_job(db, job.id)

    db.expire_all()
    dr = db.scalar(
        select(DerivedRecord).where(DerivedRecord.document_id == doc["id"])
    )
    obs = dr.payload["observations"]
    keys = [
        (o.get("code") or o.get("raw_name"), o.get("unit"), o.get("occurred_at"))
        for o in obs
    ]
    assert len(keys) == len(set(keys)), "草稿不应包含重复指标"
