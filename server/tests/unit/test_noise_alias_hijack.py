"""历史脏别名不得命中（实测：AGE 的别名里混进了「年0」）。

真实库排查发现：早期 AI 新建标准指标时把「年0」作为别名写进了 AGE，
导致后续每份报告的排版碎片「年0」都被精确命中成年龄。
修复方式：匹配**之前**先做噪声拦截（`is_noise_text`），不依赖别名清洗。
"""

from app.common.ids import new_id
from app.db.models import IndicatorDictionary
from app.preprocessing.indicator_aligner import is_noise_text
from app.preprocessing.mapper import map_payload


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


def test_noise_text_detection():
    for noise in ("T1", "年0", "每3", "①", "第20/20页", "总胆固醇增高"):
        assert is_noise_text(noise), noise
    for ok in ("年龄", "身高", "钾", "血红蛋白", "pH", "TCO2"):
        assert not is_noise_text(ok), ok


def test_legacy_noise_alias_does_not_hijack(db, auth):
    """库里 AGE 带着脏别名「年0」时，噪声行仍不得被命中。"""
    row = IndicatorDictionary(
        id=new_id(), code="AGE", name_cn="年龄", category="其他",
        unit="岁", aliases=["年0", "年龄"], is_top_trend=False, active=True,
    )
    db.add(row)
    db.commit()

    payload = map_payload(
        db,
        {
            "report_date": "2021-09-07",
            "doc_type_suggest": "lab_report",
            "events": [],
            "observations": [_obs("年0", 9)],
            "mock": False,
        },
        subject_id=auth["subjectId"],
        allow_ai=False,
    )
    obs = payload["observations"][0]
    assert obs["code"] is None
    assert obs["unmapped"] is True
    assert obs["align_rejected"]["reason"].startswith("排版噪声")


def test_legit_age_still_matches(db, auth):
    """真正的「年龄」仍应正常命中 AGE。"""
    row = IndicatorDictionary(
        id=new_id(), code="AGE", name_cn="年龄", category="其他",
        unit="岁", aliases=["年龄"], is_top_trend=False, active=True,
    )
    db.add(row)
    db.commit()
    payload = map_payload(
        db,
        {
            "report_date": "2021-09-07",
            "doc_type_suggest": "lab_report",
            "events": [],
            "observations": [_obs("年龄", 9)],
            "mock": False,
        },
        subject_id=auth["subjectId"],
        allow_ai=False,
    )
    assert payload["observations"][0]["code"] == "AGE"
