"""文本归一化回归：康熙部首/兼容字形必须还原（真实报告 0 指标缺陷）。"""

import unicodedata

from app.preprocessing.textnorm import normalize_cell, normalize_text


def test_kangxi_radical_normalized_to_cjk():
    # U+2F6C 是康熙部首「目」，真实 PDF 文本层用它代替「目」
    compat = "检查项⽬"
    assert compat != "检查项目"
    assert normalize_text(compat) == "检查项目"
    assert unicodedata.normalize("NFKC", compat) == "检查项目"


def test_normalize_keeps_medical_semantics():
    # 全角数字/括号归一化不影响含义，且数值/单位不受破坏
    assert normalize_text("１０．５") == "10.5"
    assert normalize_cell("  总胆固醇   5.94  mmol/L ") == "总胆固醇 5.94 mmol/L"
    assert normalize_cell(None) == ""


def test_normalize_is_idempotent():
    once = normalize_text("检查项⽬ １．０")
    assert normalize_text(once) == once
