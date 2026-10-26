"""单位归一与换算（跨医院同一指标单位不同必须统一）。

不做换算的后果：同一「总胆固醇」一家是 5.2 mmol/L、另一家是 200 mg/dL，
趋势线跳变、异常判定失效、AI 结论跟着错。
"""

import pytest

from app.preprocessing.units import (
    canonical_unit_for,
    convert_to_canonical,
    normalize_unit,
)


def test_normalize_unit_handles_unicode_variants():
    assert normalize_unit("10⁹/L") == "10^9/l"
    assert normalize_unit("×10⁹/L") == "10^9/l"
    assert normalize_unit("10*9/L") == "10^9/l"
    assert normalize_unit(" µmol/L ") == "umol/l"
    assert normalize_unit("μmol/L") == "umol/l"
    assert normalize_unit(None) == ""


def test_same_dimension_conversion_count():
    # /uL → 10^9/L（白细胞 4400/uL = 4.4×10^9/L）
    conv = convert_to_canonical(4400, "/uL")
    assert conv.converted is True
    assert conv.unit_canonical == "10^9/L"
    assert abs(conv.value - 4.4) < 1e-9


def test_same_dimension_conversion_mass():
    # g/dL → g/L（血红蛋白 12.5 g/dL = 125 g/L）
    conv = convert_to_canonical(12.5, "g/dL")
    assert conv.unit_canonical == "g/L"
    assert abs(conv.value - 125.0) < 1e-6


def test_mass_to_substance_needs_molar_mass():
    # 总胆固醇 200 mg/dL → mmol/L（分子量 386.65 → 5.17）
    conv = convert_to_canonical(200, "mg/dL", code="TC")
    assert conv.converted is True
    assert conv.unit_canonical == "mmol/L"
    assert abs(conv.value - 5.173) < 0.01

    # 缺分子量 → 只做同维度换算（mg/dL → g/L），不猜测物质浓度
    unknown = convert_to_canonical(200, "mg/dL", code="NOT_IN_TABLE")
    assert unknown.unit_canonical == "g/L"
    assert unknown.value == pytest.approx(2.0)


def test_pressure_kpa_to_mmhg():
    conv = convert_to_canonical(16.0, "kPa")
    assert conv.unit_canonical == "mmHg"
    assert abs(conv.value - 120.0) < 0.1


def test_unknown_unit_keeps_value():
    conv = convert_to_canonical(3.2, "unknownxyz")
    assert conv.converted is False
    assert conv.value == 3.2
    assert conv.unit_canonical == "unknownxyz"


def test_percent_and_rate_units():
    assert convert_to_canonical(45, "%").unit_canonical == "%"
    rate = convert_to_canonical(68, "次/分")
    assert rate.unit_canonical == "/min"
    assert rate.value == 68


def test_canonical_unit_lookup():
    assert canonical_unit_for("WBC", "/uL") == "10^9/L"
    assert canonical_unit_for("HGB", "g/dL") == "g/L"
    assert canonical_unit_for("X", "") == ""


def test_explicit_target_unit():
    conv = convert_to_canonical(4400, "/uL", target_unit="10^9/L")
    assert conv.value == pytest.approx(4.4)
    conv2 = convert_to_canonical(4.4, "10^9/L", target_unit="10^12/L")
    # 维度不同且无分子量 → 不换算（10^9 与 10^12 是不同维度，不能瞎换）
    assert conv2.converted is False
