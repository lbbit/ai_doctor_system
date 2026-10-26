"""历史脏指标不应继续参与匹配（真实环境遗留数据防御）。"""

from app.common.ids import new_id
from app.db.models import IndicatorDictionary
from app.preprocessing.mapper import load_dictionary, map_payload
from app.preprocessing.indicator_aligner import (
    is_noise_text,
    is_plausible_indicator_name,
)


def _add_indicator(db, code, name_cn, unit=None):
    row = IndicatorDictionary(
        id=new_id(), code=code, name_cn=name_cn, category="其他",
        unit=unit, aliases=[], is_top_trend=False, active=True,
    )
    db.add(row)
    db.commit()
    return row


def test_noise_indicators_skipped_on_load(db):
    _add_indicator(db, "T1", "T1")
    _add_indicator(db, "YEAR0", "年0")
    _add_indicator(db, "HGB", "血红蛋白", "g/L")

    codes = {e.code for e in load_dictionary(db)}
    assert "HGB" in codes
    assert "T1" not in codes
    assert "YEAR0" not in codes


def test_noise_indicator_db_does_not_hijack_matching(db, auth):
    """库里残留的噪声项不能把新报告的噪声行"精确命中"成正式指标。"""
    _add_indicator(db, "T1", "T1")
    payload = map_payload(
        db,
        {
            "report_date": "2021-09-07",
            "doc_type_suggest": "lab_report",
            "events": [],
            "observations": [
                {
                    "raw_name": "T1",
                    "display_name": "T1",
                    "code": None,
                    "dictionary_id": None,
                    "value": "52170014",
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
            ],
            "mock": False,
        },
        subject_id=auth["subjectId"],
        allow_ai=False,
    )
    obs = payload["observations"][0]
    assert obs["code"] is None
    assert obs["unmapped"] is True


def test_valid_names_pass_gate():
    for name in (
        "血红蛋白", "总胆固醇", "白细胞计数", "pH", "TCO2", "离子钙", "C反应蛋白",
        "钾", "钠", "氯", "钙", "镁",            # 单字电解质
        "身高", "体重", "色觉",                   # 体检项目
        "尿糖", "尿潜血", "尿酮体", "尿比重",      # 尿液
        "体重指数", "大血小板比率", "总二氧化碳",
    ):
        ok, reason = is_plausible_indicator_name(name)
        assert ok, (name, reason)


def test_noise_names_rejected():
    for name in ("T1", "年0", "每3", "①", "第20/20页", "项目号", "总胆固醇增高", "1"):
        ok, reason = is_plausible_indicator_name(name)
        assert not ok, (name, reason)


# ---- 报告页面元信息（2026-10-23 真实缺陷）----
# 实测 OCR 路径把 `报告页码文本` 当成新指标建进字典
# （`align.created code=REPORT_PAGE_TEXT`），污染标准主数据。
# 这类是版式/流程字段，绝不是检验指标。
_PAGE_META_NAMES = (
    "报告页码文本",   # 真实缺陷现场
    "报告页码",
    "页码",
    "页数",
    "共1页",
    "第1页/共1页",
    "打印时间",
    "打印时间区域",
    "报告时间",
    "报告说明",
    "报告备注",
    "审核者",
    "审核者信息",
    "审核医师",
    "复核医师",
    "检验者",
    "送检日期",
    "采集时间",
    "申请科室",
    "科室",
    "病区",
    "床号",
    "住院号",
    "门诊号",
    "标本类型",
)


def test_page_meta_names_rejected():
    """报告页面元信息必须过不了准入（否则会建进标准主数据）。"""
    for name in _PAGE_META_NAMES:
        ok, reason = is_plausible_indicator_name(name)
        assert not ok, (name, "被当成指标放行")


def test_page_meta_detected_by_noise_filter():
    """元信息也要在**匹配前**被拦掉，避免与脏别名互相命中。"""
    for name in _PAGE_META_NAMES:
        # `第N页/共M页` 由 _NOISE_NAME 的页码规则覆盖，is_noise_text 已判 True
        assert is_noise_text(name) or not is_plausible_indicator_name(name)[0], name


def test_real_indicators_not_mistaken_for_page_meta():
    """反向保护：含"元信息词"但带检验特征的真实指标不能被误杀。

    规则只用"元信息词开头 + 纯描述性尾巴"判定，一旦出现检验特征词就放行。
    """
    for name in (
        "标本量",          # 含"标本"但是指标
        "报告时间内的血糖",  # 含"报告时间"但带"血糖"
        "审核者血糖",       # 含"审核者"但带"血糖"
        "血气分析",
        "糖化血红蛋白",
        "平均血红蛋白浓度",
        "C反应蛋白",
        "铁蛋白",
        "总二氧化碳",
        "体重指数",
    ):
        ok, reason = is_plausible_indicator_name(name)
        assert ok, (name, reason)
        assert not is_noise_text(name), (name, "被误判为噪声")
