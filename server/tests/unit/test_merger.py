"""表格 + 视觉融合回归（真实样本 2021_0907.pdf 表格覆盖不足缺陷）。"""

from app.preprocessing.merger import merge_payloads, table_coverage_sufficient


def _obs(name, value, unit=None, ref_low=None, ref_high=None, flag=None):
    return {
        "raw_name": name,
        "value": value,
        "unit": unit,
        "ref_low": ref_low,
        "ref_high": ref_high,
        "ref_text": None,
        "abnormal_flag": flag,
        "occurred_at": "2021-09-07",
        "confidence": 0.9,
    }


def test_coverage_threshold():
    assert table_coverage_sufficient(116, 15) is True       # 2020 实测：7.7 项/页
    assert table_coverage_sufficient(55, 21) is False       # 2021 实测：2.6 项/页
    assert table_coverage_sufficient(0, 10) is False
    assert table_coverage_sufficient(3, 1) is True


def test_merge_prefers_table_and_fills_gaps():
    table = {
        "report_date": "2021-09-07",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": [
            _obs("白细胞计数", 4.7, "10^9/L", 3.5, 9.5, "N"),
            _obs("血红蛋白", 128.0, "g/L"),
        ],
    }
    vlm = {
        "report_date": "2021-09-07",
        "doc_type_suggest": "lab_report",
        "events": [{"type": "checkup", "title": "健康体检", "occurred_at": "2021-09-07"}],
        "observations": [
            _obs("白细胞计数", 4.7, "10^9/L", 3.5, 9.5, "N"),
            _obs("血红蛋白", 128.0, "g/L", 130.0, 175.0, "L"),   # 补参考区间与标记
            _obs("总胆固醇", 5.94, "mmol/L", 3.0, 5.2, "H"),      # 表格漏掉的
        ],
    }

    merged = merge_payloads(table, vlm)
    by_name = {o["raw_name"]: o for o in merged["observations"]}

    assert len(merged["observations"]) == 3
    assert merged["supplemented"] == 1
    # 表格值保留，缺失的参考区间由视觉补齐
    assert by_name["血红蛋白"]["ref_low"] == 130.0
    assert by_name["血红蛋白"]["abnormal_flag"] == "L"
    assert "总胆固醇" in by_name
    assert len(merged["events"]) == 1
    assert merged["report_date"] == "2021-09-07"


def test_merge_without_secondary_returns_primary():
    table = {"observations": [_obs("身高", 180.5, "cm")], "events": []}
    merged = merge_payloads(table, None)
    assert merged["observations"][0]["raw_name"] == "身高"
    assert "merged_from" not in merged


def test_merge_same_name_different_unit_kept_separately():
    table = {"observations": [_obs("白细胞计数", 4.4, "10^9/L")], "events": []}
    vlm = {"observations": [_obs("白细胞计数", 4400, "/uL")], "events": []}
    merged = merge_payloads(table, vlm)
    assert len(merged["observations"]) == 2
