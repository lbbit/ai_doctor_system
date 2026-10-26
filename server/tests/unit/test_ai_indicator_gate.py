"""AI 新建标准指标的准入门槛（真实样本暴露：AI 把噪声行也建成了指标）。

实测（2021_0907.pdf）：表格解析后未命中项交给 AI，模型把
`T1`、`年0`、`每3`、`糖类抗原`（值 19 其实是「19-9」被截断）也判为"新指标"并落库。
这些是排版碎片，不是检验项目，必须由**代码**拦截，不能依赖提示词。

原则（AGENTS.md AI 底座第 3/8 条）：AI 只做判断，准入门槛由代码决定。
"""

import pytest
from sqlalchemy import func, select

from app.db.models import DerivedRecord, IndicatorDictionary
from app.preprocessing.indicator_aligner import _create_standard_indicator, AlignDecision
from app.preprocessing.mapper import map_payload


def _obs(name, value, unit=None, conf=0.92):
    return {
        "raw_name": name,
        "display_name": name,
        "code": None,
        "dictionary_id": None,
        "value": value,
        "unit": unit,
        "ref_low": None,
        "ref_high": None,
        "ref_text": None,
        "abnormal_flag": None,
        "occurred_at": "2021-09-07",
        "confidence": conf,
        "bbox": None,
        "status": "proposed",
    }


def _payload(*obs):
    return {
        "report_date": "2021-09-07",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": list(obs),
        "mock": False,
    }


@pytest.mark.parametrize(
    "noise_name",
    [
        "T1",
        "年0",
        "每3",
        "1",
        "①",
        "第20/20页",
        "项目号",
        "备注",
        "报告阅读说明",
        "总胆固醇增高",  # 是结论措辞而非指标名
    ],
)
def test_noise_names_never_become_indicators(db, auth, noise_name):
    """噪声/碎片名不得被 AI 建成标准指标，应保留原文进反馈队列。"""

    class NoiseLLM:
        def available(self):
            return True

        def chat(self, messages, **kwargs):
            return (
                '{"matched": false, "code": "NOISE_X", "standard_name": "%s", '
                '"standard_unit": null, "category": "其他", '
                '"confidence": 0.95, "reason": "llm says new"}' % noise_name
            )

    payload = map_payload(
        db,
        _payload(_obs(noise_name, 1.0)),
        subject_id=auth["subjectId"],
        llm=NoiseLLM(),
        doc_type="lab_report",
    )
    obs = payload["observations"][0]
    assert obs["code"] is None, f"{noise_name} 不应被建成标准指标"
    assert obs["unmapped"] is True
    assert obs.get("align_rejected"), "应记录被拒绝的原因"


def test_create_standard_indicator_rejects_noise():
    """直接调用建库函数也必须拦截。"""
    decision = AlignDecision(
        action="ai_new", code="X1", display_name="T1", unit=None, category="其他",
        aliases=["T1"], confidence=0.95,
    )
    assert _create_standard_indicator(
        None, decision, raw_name="T1", raw_unit=None, raw_value=1.0
    ) is None


def test_legit_indicator_still_created(db, auth):
    """正常新指标（如「同型半胱氨酸」）仍应能被创建，不能一刀切挡住。"""

    class GoodLLM:
        def available(self):
            return True

        def chat(self, messages, **kwargs):
            return (
                '{"matched": false, "code": "HCY", "standard_name": "同型半胱氨酸", '
                '"standard_unit": "umol/L", "category": "生化", '
                '"aliases": ["同型半胱氨酸测定"], "confidence": 0.92, '
                '"reason": "词表未覆盖"}'
            )

    payload = map_payload(
        db,
        _payload(_obs("同型半胱氨酸测定", 12.0, "umol/L")),
        subject_id=auth["subjectId"],
        llm=GoodLLM(),
        doc_type="lab_report",
    )
    obs = payload["observations"][0]
    assert obs["code"] == "HCY"
    assert obs["display_name"] == "同型半胱氨酸"


def test_conclusion_wording_not_created(db, auth):
    """「总胆固醇增高」是结论措辞，应回到既有指标或被拒绝，不能新建同名指标。"""

    class ConclusionLLM:
        def available(self):
            return True

        def chat(self, messages, **kwargs):
            return (
                '{"matched": false, "code": "TC_HIGH", "standard_name": "总胆固醇增高", '
                '"standard_unit": "mmol/L", "category": "生化", "confidence": 0.9, '
                '"reason": "new"}'
            )

    payload = map_payload(
        db,
        _payload(_obs("总胆固醇增高", 5.9, "mmol/L")),
        subject_id=auth["subjectId"],
        llm=ConclusionLLM(),
        doc_type="lab_report",
    )
    obs = payload["observations"][0]
    assert obs["code"] != "TC_HIGH"
