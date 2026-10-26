"""融合去重（真实样本 2021_0907.pdf 实测：144 项中 11 组重复）。

根因：融合发生在字典映射**之前**，去重键是 (raw_name, unit)；
但同一指标在表格里叫「体重指数」、在视觉结果里叫「身体质量指数」，
映射后都是 BMI，却因为先融合后映射而无法归并，用户在确认卡片看到重复项。
另外视觉补漏会带入「阳性结果汇总」页只有结论没有数值的行，
与表格里已有数值的同名项重复（其中一条 value=None）。
"""

from app.preprocessing.merger import dedupe_observations


def _obs(name, value, unit=None, code=None, conf=0.9, flag=None, date="2021-09-07"):
    return {
        "raw_name": name,
        "display_name": name,
        "code": code,
        "value": value,
        "unit": unit,
        "ref_low": None,
        "ref_high": None,
        "ref_text": None,
        "abnormal_flag": flag,
        "occurred_at": date,
        "confidence": conf,
        "bbox": None,
    }


def test_dedupe_by_code_prefers_valued_entry():
    rows = [
        _obs("血红蛋白", None, "g/L", code="HGB", conf=0.85, flag="L"),
        _obs("血红蛋白", 125.0, "g/L", code="HGB", conf=0.92, flag="L"),
    ]
    out, dropped = dedupe_observations(rows)
    assert len(out) == 1
    assert out[0]["value"] == 125.0
    assert dropped == 1


def test_dedupe_by_code_across_different_raw_names():
    rows = [
        _obs("体重指数", 36.0, "kg/m²", code="BMI", conf=0.92),
        _obs("身体质量指数", 36.0, "kg/m²", code="BMI", conf=0.92),
    ]
    out, dropped = dedupe_observations(rows)
    assert len(out) == 1
    assert dropped == 1


def test_dedupe_keeps_different_units_separate():
    rows = [
        _obs("白细胞计数", 4.4, "10^9/L", code="WBC"),
        _obs("白细胞计数", 4400, "/uL", code="WBC"),
    ]
    out, dropped = dedupe_observations(rows)
    assert len(out) == 2
    assert dropped == 0


def test_dedupe_keeps_different_dates_separate():
    """不同检查日期是趋势数据点，不能合并。"""
    rows = [
        _obs("总胆固醇", 5.94, "mmol/L", code="TC", date="2021-09-07"),
        _obs("总胆固醇", 4.67, "mmol/L", code="TC", date="2020-09-06"),
    ]
    out, dropped = dedupe_observations(rows)
    assert len(out) == 2
    assert dropped == 0


def test_dedupe_without_code_uses_normalized_name():
    rows = [
        _obs("皮肤", "未见明显异常", None, code=None),
        _obs("皮肤 ", "未见明显异常", None, code=None),
    ]
    out, dropped = dedupe_observations(rows)
    assert len(out) == 1
    assert dropped == 1


def test_dedupe_keeps_both_when_values_conflict_and_no_date_diff():
    """同日同名同单位但数值不同：保留高置信项，丢弃低置信项（并计数）。"""
    rows = [
        _obs("总胆固醇", 4.67, "mmol/L", code="TC", conf=0.7),
        _obs("总胆固醇", 5.94, "mmol/L", code="TC", conf=0.95),
    ]
    out, dropped = dedupe_observations(rows)
    assert len(out) == 1
    assert out[0]["value"] == 5.94
    assert dropped == 1
