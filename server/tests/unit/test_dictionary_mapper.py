"""字典映射单测（AC-5.2/5.3、E-3 未命中队列、参考区间版本化快照）。"""

from app.common.ids import new_id
from app.db.models import (
    Account,
    DictionaryMiss,
    IndicatorDictionary,
    IndicatorRefRange,
    Subject,
)
from app.preprocessing.mapper import load_dictionary, map_payload, match_indicator


def _seed(db):
    account = Account(id=new_id(), email="m@example.com", password_hash="x")
    subject = Subject(id=new_id(), account_id=account.id, name="本人", relation="本人")
    d = IndicatorDictionary(
        id=new_id(), code="GLU_FASTING", name_cn="空腹血糖",
        category="生化", unit="mmol/L", aliases=["血糖", "FBG"],
        is_top_trend=True, active=True,
    )
    db.add_all([account, subject, d])
    db.flush()
    db.add(
        IndicatorRefRange(
            id=new_id(), dictionary_id=d.id, version=1,
            low_value=3.9, high_value=6.1, applies_to={},
        )
    )
    db.commit()
    return account, subject, d


def test_alias_match(db):
    _seed(db)
    entries = load_dictionary(db)
    assert match_indicator(entries, "血糖") is not None
    assert match_indicator(entries, "空腹血糖") is not None
    assert match_indicator(entries, "GLU_FASTING") is not None
    assert match_indicator(entries, "不存在的指标XYZ") is None


def test_map_payload_hit_and_snapshot(db):
    account, subject, d = _seed(db)
    payload = {
        "observations": [
            {"raw_name": "血糖", "value": 7.2, "unit": None,
             "ref_low": None, "ref_high": None, "confidence": 0.9,
             "abnormal_flag": None, "status": "proposed",
             "code": None, "dictionary_id": None, "display_name": "血糖"},
        ]
    }
    out = map_payload(db, payload, subject_id=subject.id)
    obs = out["observations"][0]
    assert obs["code"] == "GLU_FASTING"
    assert obs["display_name"] == "空腹血糖"
    assert obs["unit"] == "mmol/L"
    # 参考区间快照来自字典版本
    assert float(obs["ref_low"]) == 3.9 and float(obs["ref_high"]) == 6.1
    # 确定性异常计算（7.2 > 6.1 → H）
    assert obs["abnormal_flag"] == "H"
    assert obs["unmapped"] is False


def test_map_payload_miss_queue(db):
    account, subject, d = _seed(db)
    payload = {
        "observations": [
            {"raw_name": "神秘指标", "value": 1, "unit": "U",
             "confidence": 0.5, "status": "proposed",
             "code": None, "dictionary_id": None, "display_name": "神秘指标"},
        ]
    }
    out = map_payload(db, payload, subject_id=subject.id)
    obs = out["observations"][0]
    assert obs["unmapped"] is True
    assert obs["code"] is None
    assert obs["display_name"] == "神秘指标"  # E-3 原文保留
    db.flush()  # session autoflush=False：先刷再查
    miss = db.query(DictionaryMiss).one()
    assert miss.raw_name == "神秘指标"
    assert miss.resolved is False
