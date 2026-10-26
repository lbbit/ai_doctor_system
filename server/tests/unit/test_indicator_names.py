"""跨医院指标命名/单位差异的归一化回归。

真实痛点：同一指标在不同检查单叫法不同、单位不同，导入多次后变成多个指标，
趋势断成多段，体验很差。
"""

import pytest

from app.preprocessing.indicator_names import (
    best_fuzzy_candidates,
    normalize_indicator_name,
    similarity,
)


@pytest.mark.parametrize(
    "a,b",
    [
        ("血红蛋白", "血色素"),
        ("血红蛋白", "HGB"),
        ("红细胞压积", "红细胞比积"),
        ("红细胞压积", "红细胞比容"),
        ("空腹血糖", "空腹血葡萄糖"),
        ("体重指数", "体质指数"),
        ("平均红细胞血红蛋白含量", "平均血红蛋白量"),
        ("白细胞计数", "白细胞"),
        ("中性粒细胞百分比", "中性粒细胞%"),
        ("谷丙转氨酶", "丙氨酸氨基转移酶"),
        ("尿素氮", "尿素"),
        ("尿酸", "UA"),
    ],
)
def test_equivalent_names_normalize_identically(a, b):
    assert normalize_indicator_name(a) == normalize_indicator_name(b), (
        normalize_indicator_name(a),
        normalize_indicator_name(b),
    )


def test_decorators_stripped():
    assert normalize_indicator_name("总胆固醇测定") == normalize_indicator_name("总胆固醇")
    assert normalize_indicator_name("白细胞计数") == normalize_indicator_name("白细胞")
    assert normalize_indicator_name("血红蛋白浓度") == normalize_indicator_name("血红蛋白")


def test_distinct_indicators_not_merged():
    """不能把不同指标误合并（宁可新建，也不能错并）。"""
    assert normalize_indicator_name("总胆固醇") != normalize_indicator_name("甘油三酯")
    assert normalize_indicator_name("收缩压") != normalize_indicator_name("舒张压")
    assert normalize_indicator_name("平均红细胞体积") != normalize_indicator_name(
        "平均红细胞血红蛋白浓度"
    )


def test_similarity_orders_sensibly():
    assert similarity("血红蛋白", "血红蛋白") == 1.0
    assert similarity("血红蛋白", "血色素") > 0.8
    assert similarity("血红蛋白", "平均红细胞血红蛋白浓度") < 0.95
    assert similarity("总胆固醇", "甘油三酯") < 0.6


def test_best_candidates_ranked():
    vocab = [
        ("HGB", "血红蛋白", "g/L"),
        ("MCV", "平均红细胞体积", "fL"),
        ("MCH", "平均血红蛋白量", "pg"),
        ("MCHC", "平均血红蛋白浓度", "g/L"),
    ]
    cands = best_fuzzy_candidates("血红蛋白HGB", vocab)
    assert cands[0].key == "HGB"
    assert cands[0].score >= 0.9
