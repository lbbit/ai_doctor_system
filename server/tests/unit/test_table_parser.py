"""表格解析器（pdfplumber）：真实体检报告表头/行结构 → 指标。

这是链路首选：确定性、零 token、字段边界清楚。测试用真实报告表头形状，
并覆盖“建议表不要被当成指标表”“描述型表格降置信度”等边界。
"""

from app.preprocessing.parsers.table_parser import (
    extract_pdf_tables,
    table_rows_to_observations,
)

LAB_TABLE = [
    [
        "检查项目",
        "测量结果",
        "",
        "单位",
        "异常描述",
        "正常参考值",
    ],
    ["身高", "179.5", "", "cm", "", ""],
    ["体重", "104.3", "", "Kg", "", ""],
    ["体重指数", "32.4", "", "", "↑", "18.5 -- 23.99"],
    ["收缩压", "116", "", "mmHg", "", "90 -- 139"],
    ["舒张压", "55", "", "mmHg", "↓", "60 -- 89"],
    ["白细胞计数", "4.4", "", "10^9/L", "", "3.5 -- 9.5"],
    ["总胆固醇", "5.94", "", "mmol/L", "增高", "3.0 -- 5.2"],
]

ADVICE_TABLE = [
    ["序号", "发现的异常结果", "复查建议（最终以临床医师意见为准）"],
    ["1", "肝脂肪浸润", "每年复查一次超声、肝功能。"],
]

FINDING_TABLE = [
    ["检查项目", "检查所见", "", "单位"],
    ["心率（次/分）", "68", "", "次/分"],
    ["心律", "齐", "", ""],
]


def test_lab_table_rows_parsed_with_ref_and_flag():
    obs = table_rows_to_observations([LAB_TABLE], report_date="2020-09-06")
    by_name = {o["raw_name"]: o for o in obs}

    assert by_name["身高"]["value"] == 179.5
    assert by_name["身高"]["unit"] == "cm"

    bmi = by_name["体重指数"]
    assert bmi["value"] == 32.4
    assert (bmi["ref_low"], bmi["ref_high"]) == (18.5, 23.99)
    assert bmi["abnormal_flag"] == "H"

    dbp = by_name["舒张压"]
    assert dbp["abnormal_flag"] == "L"
    assert (dbp["ref_low"], dbp["ref_high"]) == (60, 89)

    tc = by_name["总胆固醇"]
    assert tc["abnormal_flag"] == "H"  # 文字“增高”
    assert tc["unit"] == "mmol/L"
    assert tc["confidence"] >= 0.9  # 数值项高置信

    assert all(o["occurred_at"] == "2020-09-06" for o in obs)


def test_advice_table_not_treated_as_indicators():
    obs = table_rows_to_observations([ADVICE_TABLE], report_date="2020-09-06")
    assert obs == []


def test_finding_table_kept_but_lower_confidence():
    obs = table_rows_to_observations([FINDING_TABLE], report_date="2020-09-06")
    by_name = {o["raw_name"]: o for o in obs}
    assert "心律" in by_name
    assert by_name["心律"]["value"] == "齐"
    assert by_name["心律"]["confidence"] < 0.9


def test_duplicate_rows_deduped():
    obs = table_rows_to_observations([LAB_TABLE, LAB_TABLE], report_date="2020-09-06")
    names = [o["raw_name"] for o in obs]
    assert len(names) == len(set(names))


def test_header_noise_filtered():
    table = [
        ["检查项目", "测量结果", "单位", "正常参考值"],
        ["全国统一报告解读专线", "400", "", ""],
        ["空腹血糖", "5.2", "mmol/L", "3.9 -- 6.1"],
    ]
    obs = table_rows_to_observations([table], report_date="2020-09-06")
    assert [o["raw_name"] for o in obs] == ["空腹血糖"]

def test_real_report_pdf_extracts_many_indicators():
    """真实报告回归：2020-09.pdf 应抽出 ≥60 项指标且含关键血常规。"""
    import pathlib

    pdf_path = pathlib.Path(__file__).resolve().parents[3] / (
        "docs/testinfo/检查单/lbb/2020-09.pdf"
    )
    if not pdf_path.exists():
        import pytest

        pytest.skip("真实样本不存在（docs/testinfo 未纳入版本控制）")
    tables = extract_pdf_tables(pdf_path.read_bytes())
    assert tables, "应检测到表格"
    obs = table_rows_to_observations(tables, report_date="2020-09-06")
    assert len(obs) >= 60, len(obs)
    by_name = {o["raw_name"]: o for o in obs}
    for key in ("体重指数", "收缩压", "白细胞计数", "血小板计数", "总胆固醇"):
        assert key in by_name, key
    assert by_name["血小板计数"]["abnormal_flag"] == "L"
    assert by_name["体重指数"]["value"] == 32.4
