"""OCR 兜底解析器：OCR 文本行 → 指标；引擎缺失时优雅跳过。"""

from app.preprocessing.parsers.ocr_parser import OcrParser, ocr_text_to_observations

OCR_TEXT = """
苏州爱康卓悦  健康体检报告
全国统一报告解读专线 400 081 8899
检查日期：2020.09.06
身高 179.5 cm
体重指数 32.4 18.5--23.99 ↑
舒张压 55 mmHg ↓ 60--89
白细胞计数 4.4 10^9/L 3.5--9.5
血小板计数 69.0 10^9/L 125--350 ↓
"""


def test_ocr_lines_to_observations():
    obs = ocr_text_to_observations(OCR_TEXT, report_date="2020-09-06")
    by_name = {o["raw_name"]: o for o in obs}
    assert "身高" in by_name
    assert by_name["身高"]["value"] == 179.5
    assert "血小板计数" in by_name
    assert by_name["血小板计数"]["abnormal_flag"] == "L"
    assert (by_name["血小板计数"]["ref_low"], by_name["血小板计数"]["ref_high"]) == (125, 350)
    # 联系方式噪声不得成为指标
    assert not any("专线" in n for n in by_name)


def test_ocr_observations_carry_report_date_and_confidence():
    obs = ocr_text_to_observations(OCR_TEXT, report_date="2020-09-06")
    assert all(o["occurred_at"] == "2020-09-06" for o in obs)
    assert all(0.5 <= o["confidence"] <= 0.8 for o in obs)


def test_ocr_parser_reports_unavailable_engine():
    parser = OcrParser()
    parser._probed = True
    parser._provider = None
    result = parser.parse({"pages": [{"page_no": 1, "image_bytes": b"x", "mime": "image/png"}]})
    assert result["success"] is False
    assert "OCR" in (result["error"] or "")
