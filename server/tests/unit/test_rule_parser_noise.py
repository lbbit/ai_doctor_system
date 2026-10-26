"""解析质量回归：页眉/页脚噪声（专线电话、网址、版权）不得成为指标。

复现缺陷：真实体检 PDF 的页眉 `全国统一报告解读专线:400 081 8899`
被规则解析器当成 `观察指标=400`，导致草稿全是无意义条目（2026-10-21 实测）。
"""

from app.preprocessing.parsers.rule_parser import rule_extract


SAMPLE = """健康体检报告 体检号: 84-20200906-267
苏州爱康卓悦（文化艺术中心）
全国统一报告解读专线:400 081 8899
客服电话 400-081-8899
网址 www.ikang.com
爱康国宾健康体检管理集团有限公司 版权所有
检查日期：2020.09.06
空腹血糖 5.2 mmol/L 3.9 -- 6.1
体重指数 32.4 ↑ 18.5 -- 23.99
舒张压 55 mmHg ↓ 60 -- 89
总胆固醇 6.21 mmol/L 2.8-5.2 H
"""


def test_header_noise_not_extracted():
    payload = rule_extract(SAMPLE, "lab_report")
    names = [o["raw_name"] for o in payload["observations"]]
    assert not any("专线" in n for n in names), names
    assert not any("热线" in n for n in names), names
    assert not any("客服" in n for n in names), names
    assert not any("网址" in n or "www" in n.lower() for n in names), names
    assert not any("版权" in n for n in names), names


def test_real_indicators_kept_with_double_dash_range():
    payload = rule_extract(SAMPLE, "lab_report")
    by_name = {o["raw_name"]: o for o in payload["observations"]}
    assert "空腹血糖" in by_name
    glu = by_name["空腹血糖"]
    assert glu["value"] == 5.2
    assert glu["unit"] == "mmol/L"
    assert (glu["ref_low"], glu["ref_high"]) == (3.9, 6.1)
    assert glu["abnormal_flag"] == "N"

    bmi = by_name["体重指数"]
    assert bmi["value"] == 32.4
    assert (bmi["ref_low"], bmi["ref_high"]) == (18.5, 23.99)
    assert bmi["abnormal_flag"] == "H"

    dbp = by_name["舒张压"]
    assert dbp["abnormal_flag"] == "L"

    tc = by_name["总胆固醇"]
    assert (tc["ref_low"], tc["ref_high"]) == (2.8, 5.2)
    assert tc["abnormal_flag"] == "H"


def test_report_date_from_keyword():
    payload = rule_extract(SAMPLE, "lab_report")
    assert payload["report_date"] == "2020-09-06"
