"""跨医院指标统一（AI 底座）：同名别名对齐、单位换算、未命中由 AI 判定/新建。

用户要求（2026-10-21）：
- 同一指标在不同医院检查单上命名/单位不同时，**不能**在多次导入后变成多个指标；
- 指标建立时就有标准名称与标准单位；解析时能对齐就直接对齐，确实没有才新建；
- AI 默认可用，这类"语义判断"应交给 AI，而不是死板匹配。
"""

import pytest
from sqlalchemy import func, select

from app.db.models import IndicatorDictionary, Observation
from app.preprocessing.mapper import load_dictionary_ensured, map_payload


def _payload(*obs):
    return {
        "report_date": "2026-03-12",
        "doc_type_suggest": "lab_report",
        "events": [],
        "observations": list(obs),
        "mock": False,
    }


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
        "occurred_at": "2026-03-12",
        "confidence": conf,
        "bbox": None,
        "status": "proposed",
    }


@pytest.mark.parametrize(
    "raw_name,expected_code",
    [
        ("血色素", "HGB"),
        ("血红蛋白量", "HGB"),
        ("Hb", "HGB"),
        ("红细胞比积", "HCT"),
        ("红细胞比容", "HCT"),
        ("空腹血葡萄糖", "GLU_FASTING"),
        ("体质指数", "BMI"),
        ("体重指数", "BMI"),
        ("白细胞计数", "WBC"),
        ("白血球", "WBC"),
        ("中性粒细胞%", "NEUT_PCT"),
        ("平均血红蛋白量", "MCH"),
        ("平均红细胞血红蛋白浓度", "MCHC"),
        ("谷丙转氨酶", "ALT"),
    ],
)
def test_alias_names_map_to_same_standard_code(db, auth, raw_name, expected_code):
    """跨医院写法各异的同一指标必须归到同一 code（否则趋势断裂）。"""
    payload = map_payload(
        db,
        _payload(_obs(raw_name, 1.0)),
        subject_id=auth["subjectId"],
    )
    obs = payload["observations"][0]
    assert obs["code"] == expected_code, (raw_name, obs["code"])
    assert obs["unmapped"] is False


def test_unit_converted_to_standard(db, auth):
    """不同单位自动换算到字典标准单位，并保留原始值/单位以便审计。"""
    payload = map_payload(
        db,
        _payload(
            _obs("血红蛋白", 12.5, "g/dL"),
            _obs("白细胞计数", 4400, "/uL"),
        ),
        subject_id=auth["subjectId"],
    )
    hgb, wbc = payload["observations"]
    assert hgb["unit"] == "g/L"
    assert hgb["value"] == pytest.approx(125.0)
    assert (hgb.get("raw_value"), hgb.get("raw_unit")) == (12.5, "g/dL")

    assert wbc["unit"] == "10^9/L"
    assert wbc["value"] == pytest.approx(4.4)


# ---- 白细胞五分类"百分比 / 计数"分族：单位消歧（安全阀） ----
# 真实缺陷（2026-10-22 实测 20250126 血常规）：报告写裸名"中性粒细胞"
# 配单位 10^9/L，字典只有 NEUT_PCT，导致计数值 2.22×10^9/L 被标成
# "中性粒细胞百分比"——22 项里 6 项 display_name 错。修法：字典拆
# PCT/CNT 两族，mapper 用单位落到正确族，且显式 %/# 写法不被覆盖。


@pytest.mark.parametrize(
    "raw_name,unit,expected_code",
    [
        # 裸名 + 单位 → 分族
        ("中性粒细胞", "10^9/L", "NEUT_CNT"),
        ("中性粒细胞", "%", "NEUT_PCT"),
        ("淋巴细胞", "10^9/L", "LYMPH_CNT"),
        ("淋巴细胞", "%", "LYMPH_PCT"),
        ("单核细胞", "10^9/L", "MONO_CNT"),
        ("嗜酸性细胞", "10^9/L", "EOS_CNT"),
        ("嗜碱性细胞", "%", "BASO_PCT"),
        # 显式后缀写法不受单位影响（安全阀不能反过来误导）
        ("中性粒细胞%", "10^9/L", "NEUT_PCT"),
        ("中性粒细胞#", "%", "NEUT_CNT"),
        ("中性粒细胞计数", "%", "NEUT_CNT"),
        ("中性粒细胞百分比", "10^9/L", "NEUT_PCT"),
    ],
)
def test_cell_diff_unit_disambiguation(db, auth, raw_name, unit, expected_code):
    """裸细胞名必须按单位落到 PCT/CNT；显式 %/# 写法以名字为准。"""
    payload = map_payload(
        db,
        _payload(_obs(raw_name, 2.22, unit)),
        subject_id=auth["subjectId"],
    )
    obs = payload["observations"][0]
    assert obs["code"] == expected_code, (raw_name, unit, obs["code"])


def test_cell_diff_count_value_keeps_count_name(db, auth):
    """回归防护：计数值绝不能被标成"百分比"（AGENTS.md 第 8 条安全阀）。"""
    payload = map_payload(
        db,
        _payload(
            _obs("中性粒细胞", 2.22, "10^9/L"),
            _obs("中性粒细胞百分比", 55.3, "%"),
        ),
        subject_id=auth["subjectId"],
    )
    count_obs, pct_obs = payload["observations"]
    assert "百分比" not in count_obs["display_name"]
    assert count_obs["unit"] == "10^9/L"
    assert "百分比" in pct_obs["display_name"]


@pytest.mark.parametrize(
    "raw_name,expected_code",
    [
        # `%` / `#` 后缀必须保住分族语义
        # （`normalize_indicator_name` 的 _PUNCT 会剥掉 %/#，曾导致
        #  「嗜酸性粒细胞%」匹配不到 EOS_PCT 而被 AI 另建成 EOS_ABS）
        ("中性粒细胞%", "NEUT_PCT"),
        ("淋巴细胞%", "LYMPH_PCT"),
        ("单核细胞%", "MONO_PCT"),
        ("嗜酸性粒细胞%", "EOS_PCT"),
        ("嗜碱性粒细胞%", "BASO_PCT"),
        ("嗜酸细胞%", "EOS_PCT"),
        ("中性粒细胞#", "NEUT_CNT"),
        ("淋巴细胞#", "LYMPH_CNT"),
        ("单核细胞#", "MONO_CNT"),
        ("嗜酸性粒细胞#", "EOS_CNT"),
        ("嗜碱性粒细胞#", "BASO_CNT"),
        # 括号写法
        ("中性粒细胞(%)", "NEUT_PCT"),
        ("中性粒细胞(#)", "NEUT_CNT"),
        # 全角百分号
        ("淋巴细胞％", "LYMPH_PCT"),
    ],
)
def test_percent_and_hash_suffix_keep_group(db, auth, raw_name, expected_code):
    """`%` 后缀必须落到 _PCT，`#` 必须落到 _CNT，不能因归一化被剥掉而失败。"""
    payload = map_payload(
        db,
        _payload(_obs(raw_name, 6.5, "%")),
        subject_id=auth["subjectId"],
    )
    obs = payload["observations"][0]
    assert obs["code"] == expected_code, (raw_name, obs["code"])
    assert obs["unmapped"] is False
    # 不能出现 AI 另建的非标准 code
    assert not str(obs["code"]).endswith("_ABS"), obs["code"]


def test_canonical_code_wins_over_legacy_abs(db, auth):
    """历史遗留的 `*_ABS` 与规范 `*_CNT` 同名时，必须选规范 code。

    真实缺陷（2026-10-23 实测）：早期字典没有五分类计数组，AI 另建了
    `NEUT_ABS`/`LYMPH_ABS`/`MONO_ABS`/`EOS_ABS`/`BASO_ABS`，别名与
    `*_CNT` 完全重复（`中性粒细胞#`、`中性粒细胞计数`）。于是「中性粒细胞#」
    精确命中了旧的 `NEUT_ABS` 而不是 `NEUT_CNT` —— 这正是 AGENTS.md 第 2 条
    禁止的"同一实体变成多个"。命中冲突时规范 code 必须优先。
    """
    from app.common.ids import new_id
    from app.db.models import IndicatorDictionary

    for code, name, anti_unit in (
        ("NEUT_ABS", "中性粒细胞计数", "10^9/L"),
        ("NEUT_CNT", "中性粒细胞计数", "10^9/L"),
    ):
        db.add(
            IndicatorDictionary(
                id=new_id(), code=code, name_cn=name, category="血液",
                unit=anti_unit, aliases=["中性粒细胞#", "中性粒细胞计数"],
                is_top_trend=False, active=True,
            )
        )
    db.commit()

    payload = map_payload(
        db,
        _payload(_obs("中性粒细胞#", 2.22, "10^9/L")),
        subject_id=auth["subjectId"],
    )
    obs = payload["observations"][0]
    assert obs["code"] == "NEUT_CNT", obs["code"]


def test_cell_diff_unknown_unit_not_guessed(db, auth):
    """单位缺失/未知时不猜分族，保持原精确匹配行为（可回落 AI 对齐）。"""
    payload = map_payload(
        db,
        _payload(_obs("中性粒细胞", 2.22, None)),
        subject_id=auth["subjectId"],
    )
    obs = payload["observations"][0]
    # 不应被静默判定成 PCT 或 CNT 中的某一个"确定"结果
    assert obs["code"] not in ("NEUT_PCT", "NEUT_CNT") or obs["unmapped"] is True


# ---- 标本类型安全阀：镜检（尿/便）vs 血液定量 ----
# 真实缺陷（2026-10-23 评测）：粪便常规/尿液分析的「白细胞」「红细胞」是镜检项目
# （`个/HP`、`/HP`），却被匹配到血常规 WBC/RBC（`10^9/L`、`10^12/L`）。
# 血/尿/便里的"白细胞"是完全不同的检验项目，混配会让医生看到错误量级。


@pytest.mark.parametrize(
    "raw_name,unit",
    [
        ("白细胞", "个/HP"),
        ("白细胞", "/HP"),
        ("红细胞", "个/HP"),
        ("红细胞", "/HP"),
        ("白细胞", "/LP"),
        ("红细胞", "个/低倍视野"),
    ],
)
def test_microscopy_unit_not_matched_to_blood_indicator(db, auth, raw_name, unit):
    """镜检单位不得命中血液定量指标（WBC/RBC 标准单位是 10^9/L、10^12/L）。"""
    payload = map_payload(
        db,
        _payload(_obs(raw_name, 1.5, unit)),
        subject_id=auth["subjectId"],
        allow_ai=False,      # 关闭 AI 兜底，只看确定性匹配是否误配
    )
    obs = payload["observations"][0]
    assert obs["code"] not in ("WBC", "RBC"), (raw_name, unit, obs["code"])


@pytest.mark.parametrize(
    "raw_name,unit,expected",
    [
        # 血液定量单位仍应正常命中（安全阀不能误伤）
        ("白细胞", "10^9/L", "WBC"),
        ("白细胞", "/uL", "WBC"),
        ("红细胞", "10^12/L", "RBC"),
    ],
)
def test_blood_units_still_match(db, auth, raw_name, unit, expected):
    payload = map_payload(
        db,
        _payload(_obs(raw_name, 6.5, unit)),
        subject_id=auth["subjectId"],
        allow_ai=False,
    )
    obs = payload["observations"][0]
    assert obs["code"] == expected, (raw_name, unit, obs["code"])


def test_microscopy_detection_helper():
    """镜检单位识别：不能把体积计数单位误判成镜检。"""
    from app.preprocessing.mapper import _looks_microscopy_unit

    for text in ("个/HP", "/HP", "个/hp", "/LP", "个/高倍视野", "/低倍视野"):
        assert _looks_microscopy_unit(text), text
    for text in ("10^9/L", "/uL", "个/uL", "g/L", "%", "", "-"):
        assert not _looks_microscopy_unit(text), text



def test_reference_range_converted_together(db, auth):
    """参考区间必须与数值同步换算，否则异常判定会错。"""
    payload = map_payload(
        db,
        _payload(
            {
                **_obs("血红蛋白", 12.5, "g/dL"),
                "ref_low": 13.0,
                "ref_high": 17.5,
            }
        ),
        subject_id=auth["subjectId"],
    )
    obs = payload["observations"][0]
    assert obs["unit"] == "g/L"
    assert obs["ref_low"] == pytest.approx(130.0)
    assert obs["ref_high"] == pytest.approx(175.0)
    assert obs["abnormal_flag"] == "L"


def test_same_indicator_across_reports_has_one_code(db, auth):
    """两次导入（不同叫法/单位）落库后 code 相同，趋势才能连成一条线。"""
    first = map_payload(
        db, _payload(_obs("血红蛋白", 125.0, "g/L")), subject_id=auth["subjectId"]
    )["observations"][0]
    second = map_payload(
        db, _payload(_obs("血色素", 12.5, "g/dL")), subject_id=auth["subjectId"]
    )["observations"][0]
    assert first["code"] == second["code"] == "HGB"
    assert first["unit"] == second["unit"] == "g/L"
    assert first["value"] == pytest.approx(second["value"])


def test_unknown_indicator_created_with_ai(db, auth, monkeypatch):
    """AI 可用时：未见过的指标 → AI 判定为新标准指标并落库（含标准名/单位）。"""
    entries = load_dictionary_ensured(db)
    before = db.scalar(select(func.count()).select_from(IndicatorDictionary))

    class FakeLLM:
        def available(self):
            return True

        def chat(self, messages, **kwargs):
            return (
                '{"matched": false, "code": "SODIUM_SERUM", '
                '"standard_name": "血清钠离子", "standard_unit": "mmol/L", '
                '"category": "电解质", "aliases": ["钠离子", "Na+"], '
                '"confidence": 0.9, "reason": "未在词表中"}'
            )

    payload = map_payload(
        db,
        _payload(_obs("钠离⼦测定", 140, "mmol/L")),
        subject_id=auth["subjectId"],
        llm=FakeLLM(),
        doc_type="lab_report",
        entries=entries,
        allow_ai=True,
    )
    obs = payload["observations"][0]
    assert obs["code"] == "SODIUM_SERUM"
    assert obs["display_name"] == "血清钠离子"
    after = db.scalar(select(func.count()).select_from(IndicatorDictionary))
    assert after == before + 1


def test_ai_match_reuses_existing_code(db, auth, monkeypatch):
    """AI 可用时：别名差异较大的写法由 AI 判定为已有指标，不新建。"""
    entries = load_dictionary_ensured(db)
    before = db.scalar(select(func.count()).select_from(IndicatorDictionary))

    class FakeLLM:
        def available(self):
            return True

        def chat(self, messages, **kwargs):
            return (
                '{"matched": true, "code": "TSH", "standard_name": "促甲状腺激素", '
                '"standard_unit": "mIU/L", "confidence": 0.93, "reason": "同义"}'
            )

    payload = map_payload(
        db,
        _payload(_obs("促甲状腺素(超敏)", 2.1, "mIU/L")),
        subject_id=auth["subjectId"],
        llm=FakeLLM(),
        doc_type="lab_report",
        entries=entries,
        allow_ai=True,
    )
    obs = payload["observations"][0]
    assert obs["code"] == "TSH"
    assert db.scalar(select(func.count()).select_from(IndicatorDictionary)) == before


def test_ai_unavailable_falls_back_without_blocking(db, auth):
    """AI 不可用：不得阻塞抽取；未命中仍按原文保存并进反馈队列。"""
    payload = map_payload(
        db,
        _payload(_obs("某医院自建项目X", 1.23, "U/L")),
        subject_id=auth["subjectId"],
        llm=None,
        doc_type="lab_report",
        allow_ai=True,
    )
    obs = payload["observations"][0]
    assert obs["code"] is None
    assert obs["unmapped"] is True
    assert obs["display_name"] == "某医院自建项目X"


def test_ai_never_merges_distinct_pairs(db, auth):
    """AI 也不能把成对指标合并（收缩压/舒张压），保护由代码兜底。"""

    class BadLLM:
        def available(self):
            return True

        def chat(self, messages, **kwargs):
            # 故意给一个错误答案：把舒张压判成收缩压
            return (
                '{"matched": true, "code": "BP_SYS", "standard_name": "收缩压", '
                '"confidence": 0.99, "reason": "bad"}'
            )

    payload = map_payload(
        db,
        _payload(_obs("舒张压", 95, "mmHg")),
        subject_id=auth["subjectId"],
        llm=BadLLM(),
        doc_type="lab_report",
        allow_ai=True,
    )
    obs = payload["observations"][0]
    # 精确/模糊规则已命中 BP_DIA，不应调用 AI 覆盖
    assert obs["code"] == "BP_DIA"
