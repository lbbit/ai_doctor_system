"""跨报告归并验证：不同医院写法/单位 → 同一标准指标（真实需求验收）。

模拟两家医院对同一批指标的两种写法，确认入库后 code/单位一致、趋势可连。
"""

from sqlalchemy import select

from app.db.models import Observation
from app.preprocessing.mapper import map_payload


def _obs(name, value, unit, ref_low=None, ref_high=None):
    return {
        "raw_name": name,
        "display_name": name,
        "code": None,
        "dictionary_id": None,
        "value": value,
        "unit": unit,
        "ref_low": ref_low,
        "ref_high": ref_high,
        "ref_text": None,
        "abnormal_flag": None,
        "occurred_at": "2026-03-12",
        "confidence": 0.92,
        "bbox": None,
        "status": "proposed",
    }


def _payload(obs):
    return {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": obs,
        "mock": False,
    }


HOSPITAL_A = [
    _obs("血红蛋白", 125.0, "g/L", 130, 175),
    _obs("白细胞计数", 4.7, "10^9/L", 3.5, 9.5),
    _obs("空腹血糖", 5.09, "mmol/L", 3.9, 6.1),
    _obs("体重指数", 36.0, "kg/m²", 18.5, 23.99),
]

# 同一批指标：B 医院用缩写、旧称、不同单位
HOSPITAL_B = [
    _obs("HGB", 12.5, "g/dL", 13.0, 17.5),
    _obs("白细胞", 4700, "/uL", 3500, 9500),
    _obs("空腹血葡萄糖", 91.6, "mg/dL", 70, 110),
    _obs("体质指数", 36.0, "kg/m2", 18.5, 23.99),
]


def test_two_hospitals_align_to_same_codes(db, auth):
    a = map_payload(db, _payload(HOSPITAL_A), subject_id=auth["subjectId"])
    b = map_payload(db, _payload(HOSPITAL_B), subject_id=auth["subjectId"])

    codes_a = {o["raw_name"]: o["code"] for o in a["observations"]}
    codes_b = {o["raw_name"]: o["code"] for o in b["observations"]}

    assert codes_a["血红蛋白"] == codes_b["HGB"] == "HGB"
    assert codes_a["白细胞计数"] == codes_b["白细胞"] == "WBC"
    assert codes_a["空腹血糖"] == codes_b["空腹血葡萄糖"] == "GLU_FASTING"
    assert codes_a["体重指数"] == codes_b["体质指数"] == "BMI"


def test_two_hospitals_units_converge(db, auth):
    a = map_payload(db, _payload(HOSPITAL_A), subject_id=auth["subjectId"])
    b = map_payload(db, _payload(HOSPITAL_B), subject_id=auth["subjectId"])

    def find(payload, code):
        return next(o for o in payload["observations"] if o["code"] == code)

    hgb_a, hgb_b = find(a, "HGB"), find(b, "HGB")
    assert hgb_a["unit"] == hgb_b["unit"] == "g/L"
    assert hgb_a["value"] == hgb_b["value"] == 125.0

    wbc_a, wbc_b = find(a, "WBC"), find(b, "WBC")
    assert wbc_a["unit"] == wbc_b["unit"] == "10^9/L"
    assert wbc_b["value"] == 4.7

    glu_a, glu_b = find(a, "GLU_FASTING"), find(b, "GLU_FASTING")
    assert glu_a["unit"] == glu_b["unit"] == "mmol/L"
    assert abs(glu_b["value"] - 5.086) < 0.02  # 91.6 mg/dL → mmol/L


def test_abnormal_flag_consistent_after_conversion(db, auth):
    """换算后异常判定必须一致：12.5 g/dL 与 125 g/L 都是偏低。"""
    a = map_payload(db, _payload(HOSPITAL_A), subject_id=auth["subjectId"])
    b = map_payload(db, _payload(HOSPITAL_B), subject_id=auth["subjectId"])
    hgb_a = next(o for o in a["observations"] if o["code"] == "HGB")
    hgb_b = next(o for o in b["observations"] if o["code"] == "HGB")
    assert hgb_a["abnormal_flag"] == hgb_b["abnormal_flag"] == "L"


def test_ai_new_indicator_reused_by_second_hospital(db, auth):
    """AI 新建的标准指标，第二家医院再次导入时必须直接复用（不能又建一个）。"""
    from app.db.models import IndicatorDictionary

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def chat(self, messages, **kwargs):
            self.calls += 1
            return (
                '{"matched": false, "code": "HOMOCYSTEINE", '
                '"standard_name": "同型半胱氨酸", "standard_unit": "umol/L", '
                '"category": "生化", "aliases": ["同型半胱氨酸测定", "HCY"], '
                '"confidence": 0.92, "reason": "词表未覆盖"}'
            )

    llm = FakeLLM()
    before = db.scalar(select(Observation).count()) if False else None  # noqa: F841

    first = map_payload(
        db,
        _payload([_obs("同型半胱氨酸测定", 12.0, "umol/L")]),
        subject_id=auth["subjectId"],
        llm=llm,
        doc_type="lab_report",
    )
    assert first["observations"][0]["code"] == "HOMOCYSTEINE"
    assert first["observations"][0]["match_kind"] == "ai_new"

    # 第二次（另一家医院写法）应被别名命中，不调用 AI、不重复建指标
    second = map_payload(
        db,
        _payload([_obs("HCY", 11.0, "μmol/L")]),
        subject_id=auth["subjectId"],
        llm=llm,
        doc_type="lab_report",
    )
    obs = second["observations"][0]
    assert obs["code"] == "HOMOCYSTEINE"
    assert obs["match_kind"] == "exact"
    assert obs["unit"] == "umol/L"

    rows = db.scalars(
        select(IndicatorDictionary).where(IndicatorDictionary.code == "HOMOCYSTEINE")
    ).all()
    assert len(rows) == 1, "同一指标不得重复建标准项"
