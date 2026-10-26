"""单位归一化与换算（跨医院同一指标单位不同 → 统一到标准单位）。

真实场景：
- 血常规白细胞：A 医院 `10^9/L`，B 医院 `/uL`、`10*3/uL`、`×10⁹/L`
- 血红蛋白：`g/L` 与 `g/dL`（差 10 倍）
- 血糖：`mmol/L` 与 `mg/dL`（需分子量换算）
- 血压：`mmHg` 与 `kPa`

不做换算会怎样：同一个「总胆固醇」在不同报告里是 5.2 与 200，
趋势线会跳变、异常判定失效、AI 结论跟着错。因此**入库前必须统一到字典标准单位**，
原始值/原始单位另行保留以便审计回溯。
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ---- 维度：base 单位 + 到 base 的系数 ----
_DIM_COUNT_VOLUME = "count/volume"      # base 10^9/L
_DIM_MASS_VOLUME = "mass/volume"        # base g/L
_DIM_SUBSTANCE_VOLUME = "substance/volume"  # base mmol/L
_DIM_PRESSURE = "pressure"              # base mmHg
_DIM_PERCENT = "percent"                # base %
_DIM_RATIO = "ratio"                    # 无量纲
_DIM_TIME = "time"                      # base s
_DIM_RATE = "rate"                      # base /min

_DIMENSION_BASE: Dict[str, str] = {
    _DIM_COUNT_VOLUME: "10^9/L",
    _DIM_MASS_VOLUME: "g/L",
    _DIM_SUBSTANCE_VOLUME: "mmol/L",
    _DIM_PRESSURE: "mmHg",
    _DIM_PERCENT: "%",
    _DIM_RATIO: "",
    _DIM_TIME: "s",
    _DIM_RATE: "/min",
}

# 归一化后的单位符号 → (维度, 到 base 的乘法系数)
_UNIT_TABLE: Dict[str, Tuple[str, float]] = {
    # 计数/体积
    "10^9/l": (_DIM_COUNT_VOLUME, 1.0),
    "10*9/l": (_DIM_COUNT_VOLUME, 1.0),
    "10e9/l": (_DIM_COUNT_VOLUME, 1.0),
    "x10^9/l": (_DIM_COUNT_VOLUME, 1.0),
    "*10^9/l": (_DIM_COUNT_VOLUME, 1.0),
    "10^9/l": (_DIM_COUNT_VOLUME, 1.0),
    "/ul": (_DIM_COUNT_VOLUME, 0.001),
    "/µl": (_DIM_COUNT_VOLUME, 0.001),
    "/μl": (_DIM_COUNT_VOLUME, 0.001),
    "10^3/ul": (_DIM_COUNT_VOLUME, 1.0),
    "10^3/µl": (_DIM_COUNT_VOLUME, 1.0),
    "10*3/ul": (_DIM_COUNT_VOLUME, 1.0),
    "10^12/l": ("count12/volume", 1.0),   # 红细胞计数单独维度（避免与 10^9/L 混算）
    "/pl": ("count12/volume", 1.0),
    # 质量/体积
    "g/l": (_DIM_MASS_VOLUME, 1.0),
    "g/dl": (_DIM_MASS_VOLUME, 10.0),
    "mg/dl": (_DIM_MASS_VOLUME, 0.01),
    "mg/l": (_DIM_MASS_VOLUME, 0.001),
    "µg/ml": (_DIM_MASS_VOLUME, 0.001),
    "ug/ml": (_DIM_MASS_VOLUME, 0.001),
    "ng/ml": (_DIM_MASS_VOLUME, 0.000001),
    # 物质的量/体积
    "mmol/l": (_DIM_SUBSTANCE_VOLUME, 1.0),
    "umol/l": (_DIM_SUBSTANCE_VOLUME, 0.001),
    "µmol/l": (_DIM_SUBSTANCE_VOLUME, 0.001),
    "μmol/l": (_DIM_SUBSTANCE_VOLUME, 0.001),
    "nmol/l": (_DIM_SUBSTANCE_VOLUME, 0.000001),
    "miol/l": (_DIM_SUBSTANCE_VOLUME, 1.0),   # OCR 常见误识
    "miu/l": (_DIM_SUBSTANCE_VOLUME, 0.001),  # 促甲状腺激素等
    "mui/l": (_DIM_SUBSTANCE_VOLUME, 0.001),
    "iu/l": (_DIM_SUBSTANCE_VOLUME, 0.001),
    # 压力
    "mmhg": (_DIM_PRESSURE, 1.0),
    "kpa": (_DIM_PRESSURE, 7.50062),
    # 百分比
    "%": (_DIM_PERCENT, 1.0),
    "％": (_DIM_PERCENT, 1.0),
    "‰": (_DIM_PERCENT, 0.1),
    # 比值/时间/速率
    "": (_DIM_RATIO, 1.0),
    "ratio": (_DIM_RATIO, 1.0),
    "s": (_DIM_TIME, 1.0),
    "秒": (_DIM_TIME, 1.0),
    "min": (_DIM_TIME, 60.0),
    "分": (_DIM_TIME, 60.0),
    "次/分": (_DIM_RATE, 1.0),
    "/min": (_DIM_RATE, 1.0),
    "bpm": (_DIM_RATE, 1.0),
    "mm/h": (_DIM_RATE, 1.0),
}

# 摩尔质量（g/mol）：用于 mg/dL ↔ mmol/L（血糖/血脂/肌酐/尿酸/胆红素等）
_MOLAR_MASS: Dict[str, float] = {
    "GLU_FASTING": 180.156,
    "GLU_RANDOM": 180.156,
    "TC": 386.65,
    "LDL_C": 386.65,
    "HDL_C": 386.65,
    "TG": 885.7,
    "CREA": 113.12,
    "UA": 168.11,
    "TBIL": 584.66,
    "DBIL": 584.66,
    "IBIL": 584.66,
    "BUN": 60.06,
    "CA": 40.08,
    "UREA": 60.06,
}

# 常见单位别名（先 NFKC，再比较）
_UNIT_SYNONYMS = {
    "10⁹/l": "10^9/l",
    "×10⁹/l": "10^9/l",
    "x10⁹/l": "10^9/l",
    "10^9/L".lower(): "10^9/l",
    "个/ul": "/ul",
    "个/µl": "/ul",
    "cells/ul": "/ul",
    "cell/ul": "/ul",
    "g/l": "g/l",
    "克/升": "g/l",
    "mmol/l": "mmol/l",
    "毫摩尔/升": "mmol/l",
    "umol/l": "umol/l",
    "μmol/l": "μmol/l",
    "µmol/l": "µmol/l",
    "u/l": "u/l",
    "iu/l": "iu/l",
    "毫米汞柱": "mmhg",
}

_OCR_FIXES = (
    ("µ", "u"),
    ("μ", "u"),
    ("×", "x"),
)


@dataclass
class UnitConversion:
    """换算结果：值已换到标准单位。"""

    value: Optional[float]
    unit_raw: Optional[str]
    unit_canonical: Optional[str]
    factor: Optional[float]
    converted: bool
    reason: str = ""

    @property
    def changed(self) -> bool:
        return self.converted and abs((self.factor or 1.0) - 1.0) > 1e-12


# NFKC 会把上标数字降为普通数字（10⁹ → 109），因此先做上标/乘号替换再归一化
_SUPERSCRIPT_FIXES = (
    ("⁹", "^9"),
    ("³", "^3"),
    ("⁶", "^6"),
    ("¹²", "^12"),
    ("×", "x"),
    ("✕", "x"),
    ("·", ""),
)


def normalize_unit(unit: Optional[str]) -> str:
    """单位归一化：上标/乘号修正 + NFKC + 小写 + OCR 误识修正。"""
    if not unit:
        return ""
    text = str(unit).strip()
    for src, dst in _SUPERSCRIPT_FIXES:
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = re.sub(r"\s+", "", text)
    # 微符号（µ/μ/渭=OCR 误认的"μ"）统一成 u
    text = text.replace("微", "u").replace("渭", "u")
    text = text.replace("µ", "u").replace("μ", "u")
    # "10*9" / "10^9" / "x10^9" / "*10^9" 都是同一含义（乘号/脱字符是排版噪音）
    text = re.sub(r"^[x*]?10\*(\d+)", r"10^\1", text)
    text = re.sub(r"^[x*]10\^", "10^", text)
    text = _UNIT_SYNONYMS.get(text, text)
    return text


def _lookup(unit_norm: str) -> Optional[Tuple[str, float]]:
    if unit_norm in _UNIT_TABLE:
        return _UNIT_TABLE[unit_norm]
    # 去掉末尾的 "/l" 之类变体
    stripped = unit_norm.rstrip(".")
    if stripped in _UNIT_TABLE:
        return _UNIT_TABLE[stripped]
    return None


def canonical_unit_for(code: Optional[str], unit: Optional[str]) -> Optional[str]:
    """把任意单位表达式映射到标准符号（同维度时用 base）。"""
    hit = _lookup(normalize_unit(unit))
    if hit is None:
        return None
    return _DIMENSION_BASE.get(hit[0])


def convert_to_canonical(
    value: Optional[float],
    unit: Optional[str],
    *,
    code: Optional[str] = None,
    target_unit: Optional[str] = None,
) -> UnitConversion:
    """把 value 从 unit 换算到 target_unit（默认该维度 base 单位）。

    不能换算时返回原值并给出 reason（调用方保留原文，不猜测）。
    """
    raw_unit = unit
    if value is None:
        return UnitConversion(None, raw_unit, target_unit, None, False, "无值")

    source = _lookup(normalize_unit(unit))
    if source is None:
        return UnitConversion(value, raw_unit, target_unit or unit, None, False, "未知单位")
    src_dim, src_factor = source

    target_norm = normalize_unit(target_unit)
    if target_norm:
        target = _lookup(target_norm)
        if target is None:
            return UnitConversion(value, raw_unit, target_unit, None, False, "目标单位无法识别")
        dst_dim, dst_factor = target
        canonical = target_unit
    else:
        dst_dim = src_dim
        dst_factor = 1.0
        canonical = _DIMENSION_BASE.get(src_dim)
        # 质量浓度且已知分子量 → 该指标标准单位是物质浓度（如 TC 200 mg/dL → mmol/L）
        if src_dim == _DIM_MASS_VOLUME and code and _MOLAR_MASS.get(code.upper()):
            dst_dim = _DIM_SUBSTANCE_VOLUME
            dst_factor = 1.0
            canonical = _DIMENSION_BASE[_DIM_SUBSTANCE_VOLUME]

    if dst_dim != src_dim:
        converted = _convert_cross_dimension(
            value, src_factor, dst_factor, code
        )
        if converted is None:
            return UnitConversion(
                value, raw_unit, canonical, None, False,
                "跨维度且缺少分子量，未换算",
            )
        return UnitConversion(converted, raw_unit, canonical, None, True, "跨维度换算")

    factor = src_factor / dst_factor
    if abs(factor - 1.0) < 1e-12:
        return UnitConversion(value, raw_unit, canonical, 1.0, True, "单位一致")
    return UnitConversion(value * factor, raw_unit, canonical, factor, True, "同维度换算")


def _convert_cross_dimension(
    value: float,
    from_factor: float,
    to_factor: float,
    code: Optional[str],
) -> Optional[float]:
    """质量浓度 ↔ 物质浓度（需要分子量，g/L → mmol/L）。"""
    molar = _MOLAR_MASS.get((code or "").upper())
    if molar is None:
        return None
    grams_per_liter = value * from_factor            # 统一到 g/L
    mmol_per_liter = grams_per_liter / molar * 1000   # 统一到 mmol/L
    return mmol_per_liter / to_factor


def format_value(value: Optional[float]) -> str:
    """展示用数值格式化（避免 5.3999999 之类浮点噪声）。"""
    if value is None:
        return "—"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.4g}"
