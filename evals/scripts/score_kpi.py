#!/usr/bin/env python3
"""KPI 跑分（PRD §3 Go/No-Go）：纯标准库，任意 Python ≥3.10。

用法：
  python score_kpi.py k1k2 --dir ../dataset/kpi_k1k2 [--annotations FILE] [--dry-run]
  python score_kpi.py k3   --questions ../dataset/kpi_k3/questions.jsonl
  python score_kpi.py selftest

预测 JSON 约定（predictions/<样本同名>.json）：
  {"file": "files/report_001.jpg",
   "fields": [{"name": "...", "value": "...", "unit": "...", "date": "YYYY-MM-DD"}]}
标注 JSONL 见 TEMPLATE_annotations.jsonl；K3 verdict_manual ∈ pass|fail|null。
口径：K1 = value+unit+date 三要素全对字段 / 标注字段总数；K2 = 命中的 abnormal 字段 / 全部 abnormal 字段；
     K3 = pass / (pass+fail)。阈值 K1≥90%、K2≥95%、K3≥85%，任一不达标 → No-Go。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

THRESHOLDS = {"K1": 0.90, "K2": 0.95, "K3": 0.85}


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{i}: bad JSONL: {exc}") from exc
    return rows


def _num_eq(a: str, b: str) -> bool:
    """数值容差比较：能解析为数字则按数值等值，否则字符串 strip 后相等。"""
    a, b = str(a).strip(), str(b).strip()
    if a == b:
        return True
    try:
        fa, fb = float(a), float(b)
    except ValueError:
        return False
    tol = max(abs(fa), abs(fb)) * 1e-6 + 1e-9
    return abs(fa - fb) <= tol


# ---------------------------------------------------------------- 指标名归一化
# 为什么需要：标注用报告上的原始名（"白细胞"、"中性粒细胞%"），系统用标准化名
# （"白细胞计数"、"中性粒细胞百分比"）。若直接字符串比对，**抽取正确也会判错**。
# 实测基线：200/214 未命中全部源于此（每份血常规恰好少 17 项）。
# 归一化规则与业务侧一致（NFKC + 去修饰词 + 百分比/计数后缀统一），但不放宽
# 到"模糊匹配"，避免把成对指标（收缩压/舒张压、MCV/MCH/MCHC）误合并。
_NAME_NOISE = (
    "测定", "检测", "定量", "（计算）", "(计算)", "*", " ", "\u3000",
)
# 报告常见简写 → 标准名（只收录语义等价的确定映射）
_NAME_EQUIV = {
    "%": "百分比",
    "百分比": "百分比",
    "#": "计数",
    "计数": "计数",
    "分布宽度": "分布宽度",
}


# 成对/易混指标：绝不可被判为同一项（AGENTS.md 第 8 条「安全阀优先于智能」）
# 血常规里"中性粒细胞%"（百分比）与"中性粒细胞#/计数"是**两个不同指标**，
# 若归一化去掉 %/# 差异，会把 30.5% 与 2.22×10^9/L 错配 → K1 既虚高又失真。
_PAIR_SUFFIX = ("百分比", "计数")


def normalize_name(name: str, *, keep_count_kind: bool = False) -> str:
    """指标名归一化（确定性、无模型）。

    ``keep_count_kind=True`` 时保留「百分比 / 计数」区分（成对指标比对用）。
    """
    import re
    import unicodedata

    text = unicodedata.normalize("NFKC", str(name or "")).strip()
    for token in _NAME_NOISE:
        text = text.replace(token, "")
    text = text.replace("（", "(").replace("）", ")").replace("：", ":")
    # 百分比 / 计数后缀统一（"中性粒细胞%" → "中性粒细胞百分比"）
    text = re.sub(r"\(%\)|%）|%$", "百分比", text)
    text = re.sub(r"\(#\)|#＄|#$", "计数", text)
    # "血糖(空腹)" 括号补语移到前面（"血糖(空腹)" == "空腹血糖"）
    m = re.fullmatch(r"([^()]+)\(([^()]+)\)", text)
    if m:
        head, qualifier = m.group(1).strip(), m.group(2).strip()
        text = qualifier + head
    text = text.replace("(", "").replace(")", "")
    if not keep_count_kind:
        # 仅在**不区分计数/百分比**时才允许去掉"计数"后缀
        text = re.sub(r"(?<=[\u4e00-\u9fa5])计数$", "", text)
    return text.lower()


def _name_kind(name: str, unit: str = "") -> str:
    """从「名字后缀」或「单位」判断这是百分比项还是计数项。"""
    strict = normalize_name(name, keep_count_kind=True)
    if strict.endswith("百分比"):
        return "百分比"
    if strict.endswith("计数"):
        return "计数"
    # 名字没写后缀（真实报告大量用裸名"中性粒细胞"+单位）→ 用单位补判
    u = str(unit or "").strip().lower().replace(" ", "")
    if u in ("%", "％", "百分比", "percent"):
        return "百分比"
    if "10^9" in u or "10*9" in u or "10⁹" in u or "×10⁹" in u or "x10^9" in u:
        return "计数"
    return ""


# 白细胞五分类的基名：只有这些裸名才存在"% / #"两族歧义，
# 其它指标（白细胞 / 血小板）的"计数"只是修饰词，去掉不影响身份。
_CELL_DIFF_FAMILIES = (
    ("中性粒细胞", "中性细胞"),
    ("淋巴细胞",),
    ("单核细胞",),
    ("嗜酸性细胞", "嗜酸性粒细胞", "嗜酸细胞"),
    ("嗜碱性细胞", "嗜碱性粒细胞", "嗜碱细胞"),
)


def _cell_diff_family(name: str) -> str:
    """返回五分类的族名（中性粒细胞/淋巴细胞/…），非五分类返回 ''。

    用**去后缀的基名**比对：`中性粒细胞%` → `中性粒细胞`，
    `嗜酸性粒细胞计数` → `嗜酸性粒细胞`。
    """
    base = re.sub(r"(百分比|百分率|比率|计数)$", "", normalize_name(name, keep_count_kind=True))
    for variants in _CELL_DIFF_FAMILIES:
        for variant in variants:
            if base == normalize_name(variant, keep_count_kind=True) or base == variant:
                return variants[0]
    return ""


# 同一指标的常见异名（不同医院/仪器写法）。**只收录医学上确为同一指标**的，
# 成对/易混指标（收缩压/舒张压、MCV/MCH/MCHC）绝不在此表出现。
_SYNONYM_GROUPS = (
    # 红细胞分布宽度：是否带 CV / 变异系数只是写法差异
    ("红细胞分布宽度", "红细胞分布宽度cv", "红细胞分布宽度变异系数", "rdwcv", "rdw"),
    # 红细胞比容 = 红细胞压积（同一指标两种叫法）
    ("红细胞压积", "红细胞比容", "红细胞比积", "压积", "hct"),
    # 血小板比容 = 血小板压积
    ("血小板压积", "血小板比容", "pct"),
    # 平均红细胞体积：词序差异
    ("平均红细胞体积", "红细胞平均体积", "mcv"),
    # 转氨酶 ↔ 转移酶（同一指标的两种写法）
    ("丙氨酸氨基转移酶", "谷丙转氨酶", "丙氨酸氨基转氨酶", "alt"),
    ("天门冬氨酸氨基转移酶", "谷草转氨酶", "天门冬氨酸氨基转氨酶",
     "门冬氨酸氨基转移酶", "ast"),
    ("谷氨酰转肽酶", "谷氨酰氨转移酶", "谷氨酰转移酶", "γ谷氨酰转肽酶", "ggt"),
    # 白球比 = 白蛋白/球蛋白比值（注意 `/` 与 `比` 两种写法都要收录）
    ("白蛋白球蛋白比值", "白球比", "白蛋白球蛋白比", "白蛋白/球蛋白比值",
     "白蛋白/球蛋白", "a/g", "ag比"),
    # 传染病筛查：是否带「抗体」只是写法差异
    ("人类免疫缺陷病毒抗体筛查试验", "人类免疫缺陷病毒筛查试验",
     "人免疫缺陷病毒筛查试验", "人免疫缺陷病毒抗体筛查试验", "hiv筛查"),
    ("丙型肝炎病毒抗体", "丙型肝炎病毒抗体筛查试验", "丙肝抗体", "hcv抗体"),
    # 乙肝五项：简称「乙肝」vs 全称「乙型肝炎」
    ("乙型肝炎表面抗原", "乙肝表面抗原", "hbsag"),
    ("乙型肝炎表面抗体", "乙肝表面抗体", "hbsab"),
    ("乙型肝炎e抗原", "乙肝e抗原", "hbeag"),
    ("乙型肝炎e抗体", "乙肝e抗体", "hbeab"),
    ("乙型肝炎核心抗体", "乙肝核心抗体", "hbcab"),
    # 血气 / 微量元素异名
    ("总二氧化碳", "二氧化碳总量", "二氧化碳结合力", "tco2"),
    ("铁", "血清铁", "血铁", "fe"),
    # 无机盐：报告写「无机磷」，标注常写「磷」
    ("磷", "无机磷", "血清磷", "血磷"),
    # pH：「PH值」与「pH」只差「值」与大小写
    ("ph", "ph值"),
)


def _synonym_key(name: str) -> str:
    """归并到同义组的规范键；不在任何组时返回归一化名本身。"""
    norm = normalize_name(name, keep_count_kind=True)
    for group in _SYNONYM_GROUPS:
        if norm in group:
            return "syn:" + group[0]
    return norm


def _is_microscopy_unit(unit: str) -> bool:
    """是否镜检计数单位（每高倍/低倍视野）。"""
    text = str(unit or "").strip()
    if not text:
        return False
    return bool(re.search(r"(个|cells?)?\s*/\s*(hp|lp|hpf|lpf|视野|高倍|低倍)", text, re.I))


def _looks_microscopy_unit_pair(a: str, b: str) -> bool:
    """两侧是否同属镜检体系（用于多个候选时挑对标本体系）。"""
    return _is_microscopy_unit(a) and _is_microscopy_unit(b)


def _strip_specimen_prefix(name: str) -> str:
    """去掉标本前缀（尿液/粪便/血清…），用于跨标本体系的同名定性项比对。

    背景（2026-10-23）：系统对尿常规/粪便常规项目统一加标本前缀，
    「糖」「蛋白」「颜色」→「尿糖」「尿蛋白」「尿液颜色」。
    这是**正确的建模**（尿糖 ≠ 血糖，颜色需限定标本），
    但标注沿用报告上的裸名，评分时需归一再比。

    安全阀：只在**两侧都归一到同一裸名**时才算匹配；带不同标本前缀的
    两个名字（尿糖 vs 血糖）归一后仍不同名，不会被误合并。
    """
    text = normalize_name(name, keep_count_kind=True)
    for prefix in ("尿液", "尿", "粪便", "大便", "血清", "全血", "血浆", "脑脊液"):
        # 允许剥离后只剩 1 个字（尿糖 → 糖、尿胆原 → 胆原），
        # 但不允许剥成空串（避免把「尿」这个单字项目本身剥没）。
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix):]
            break
    # 再剥「镜检」标记：`尿镜检红细胞` → `红细胞`（与裸名「红细胞」对齐）。
    # 镜检限定的语义由**单位**（个/HPF）承担，不靠名字。
    for marker in ("镜检", "显微镜检查", "镜下"):
        if text.startswith(marker) and len(text) > len(marker):
            text = text[len(marker):]
            break
    return text


def names_match(annotated: str, predicted: str, *, a_unit: str = "", b_unit: str = "") -> bool:
    """指标名是否指向同一指标。

    规则（从严到宽，任一命中即匹配）：
    1. 归一化后完全相等；
    2. 归一化后命中同一**同义组**（`_SYNONYM_GROUPS`，仅收录医学上确为同一指标的异名）。

    **安全阀**：若两者在「百分比 / 计数」维度不同，一律视为不同指标
    （中性粒细胞% ≠ 中性粒细胞#；红细胞计数 ≠ 红细胞比容）。
    kind 优先取名字后缀；名字是裸名（如"中性粒细胞"，标注常见写法）时
    **用单位补判**——否则裸名会绕过安全阀，把计数项错配成百分比项。

    **五分类族校验**：裸五分类名（`中性粒细胞`）只能与**同族**写法合并，
    绝不能与其它族或「白细胞计数」这类不同指标合并。
    （真实缺陷，2026-10-23：早期版本只比对 kind，导致「中性粒细胞」被
    判为等于「白细胞计数」，K1 50 次错配、整体从 52.84% 掉到 49.29%。）
    """
    a_strict = normalize_name(annotated, keep_count_kind=True)
    b_strict = normalize_name(predicted, keep_count_kind=True)
    a_kind = _name_kind(annotated, a_unit)
    b_kind = _name_kind(predicted, b_unit)
    # 安全阀：类型不同且都非空 → 不是同一指标
    if a_kind and b_kind and a_kind != b_kind:
        return False

    a_family = _cell_diff_family(annotated)
    b_family = _cell_diff_family(predicted)
    if a_family or b_family:
        # 任一方是五分类：必须同族
        if a_family != b_family:
            return False
        # 同族还须 kind 一致。任一方 kind 判不出（裸名且单位缺失）时
        # **不放行**——宁可判不匹配（宁可漏配，不可错配，AGENTS.md 第 8 条）。
        if a_kind != b_kind:
            return False
        return True

    a = normalize_name(annotated)
    b = normalize_name(predicted)
    if a == b:
        return True
    # 标本前缀差异（尿糖 vs 糖、粪便颜色 vs 颜色）
    if _strip_specimen_prefix(annotated) == _strip_specimen_prefix(predicted):
        return True
    # 同义组归并（仅限 _SYNONYM_GROUPS 内的确为同一指标的异名）
    if _synonym_key(annotated) == _synonym_key(predicted):
        return True
    # 词序变体：去掉括号补语后比较（"血糖(空腹)" vs "空腹血糖" 已在归一化里处理，
    # 这里兜底"总胆固醇" vs "胆固醇(总)"之类）
    return False


# ---- 单位换算（仅用于评分：同量纲不同量级 → 换算后比数值）----
# 评分器是纯标准库，不能依赖后端 units.py，因此自带最小换算表。
# 只收录**同一量纲内**的量级换算；不做跨量纲（质量↔物质浓度）换算，
# 那需要分子量，属于后端职责。
_UNIT_SCALE: dict[str, tuple[str, float]] = {
    # 物质浓度（base mmol/L）
    "mmol/l": ("substance", 1.0),
    "umol/l": ("substance", 0.001),
    "nmol/l": ("substance", 1e-6),
    "pmol/l": ("substance", 1e-9),
    "mg/dl": ("substance_mass", 1.0),
    # 质量浓度（base g/L）
    "g/l": ("mass", 1.0),
    "mg/l": ("mass", 0.001),
    "mg/dl_mass": ("mass", 0.01),
    "ug/l": ("mass", 1e-6),
    "ng/ml": ("mass", 1e-6),
    "ng/l": ("mass", 1e-9),
    "pg/ml": ("mass", 1e-9),
    # 计数浓度（base 10^9/L）
    "10^9/l": ("count", 1.0),
    "/ul": ("count", 0.001),
    # 酶活性（base U/L）
    "u/l": ("enzyme", 1.0),
    "iu/l": ("enzyme", 1.0),
}


def _unit_norm_key(unit: str) -> str:
    """评分用的单位归一（与 _unit_eq 的 norm 保持一致口径）。"""
    import unicodedata

    text = unicodedata.normalize("NFKC", str(unit or "")).strip()
    text = text.replace("μ", "u").replace("µ", "u").replace("渭", "u")
    text = text.replace("×", "x").replace("·", "*")
    text = text.replace("^", "").replace("⁹", "9").replace("⁰", "0")
    text = re.sub(r"^\^?10\^?", "10^", text)
    return text.replace(" ", "").lower()


def _units_convertible(a: str, b: str) -> bool:
    """两个单位是否同量纲、可换算（仅量级差异）。"""
    ka, kb = _unit_norm_key(a), _unit_norm_key(b)
    if ka == kb:
        return True
    sa, sb = _UNIT_SCALE.get(ka), _UNIT_SCALE.get(kb)
    return bool(sa and sb and sa[0] == sb[0])


def _value_eq(a_val, a_unit, b_val, b_unit) -> bool:
    """数值是否等价：单位同量纲时换算到同一量级再比。

    单位**不可换算**时，只有单位写法也等价才认可数值相等——
    否则「5 mmol/L」与「5 %」会因数值巧合被判命中（量纲完全不同）。
    """
    same_unit = _unit_eq(a_unit, b_unit)
    convertible = _units_convertible(a_unit, b_unit)
    if not same_unit and not convertible:
        return False
    if _num_eq(a_val, b_val):
        return True
    ka, kb = _unit_norm_key(a_unit), _unit_norm_key(b_unit)
    sa, sb = _UNIT_SCALE.get(ka), _UNIT_SCALE.get(kb)
    if not (sa and sb and sa[0] == sb[0]):
        return False
    try:
        fa, fb = float(str(a_val).strip()), float(str(b_val).strip())
    except ValueError:
        return False
    # 都换算到该量纲的 base 后比较
    ca, cb = fa * sa[1], fb * sb[1]
    tol = max(abs(ca), abs(cb)) * 1e-4 + 1e-9
    return abs(ca - cb) <= tol


def _qual_value_eq(a, b) -> bool:
    """定性/半定量值比较（比 K1 宽松，但仍有安全阀）。

    处理报告里同一个值的不同写法：
    - 数值格式：`132` ≡ `132.0`、`5.50` ≡ `5.5`（实测 27/69 定性 miss 属此类）
    - 空与占位符：`` ≡ `-` ≡ `未见`（部分报告用符号表示"无"）
    - 定性词首尾空白：`阴性 ` ≡ `阴性`
    - 带前缀的定性：`阴性/2.00` ≡ `阴性/<2.00`（报告常加 `<` 表示低于检出限）

    **不做**的事：不把 `阴性` 与 `阳性` 判等（临床意义相反），
    不把 `未见` 与任意数值判等（前者是"未检出"，后者是测得值）。
    """
    a_s, b_s = str(a or "").strip(), str(b or "").strip()
    if a_s == b_s:
        return True
    # 纯数值（含格式差异）
    if _num_eq(a_s, b_s):
        return True
    # 空值等价（都表示"无结果"）
    blank = {"", "-", "—", "–", "/", "无"}
    if a_s in blank and b_s in blank:
        return True
    # 定性词 + 可选数值：比较定性部分与数值部分
    ma = re.fullmatch(r"([^\d/]*?)\s*[/]?\s*([<>]?)\s*([\d.]+)?", a_s)
    mb = re.fullmatch(r"([^\d/]*?)\s*[/]?\s*([<>]?)\s*([\d.]+)?", b_s)
    if ma and mb:
        word_a, _, num_a = ma.group(1).strip(), ma.group(2), ma.group(3)
        word_b, _, num_b = mb.group(1).strip(), mb.group(2), mb.group(3)
        # 定性词必须完全相同（阴性 ≠ 阳性，未见 ≠ 阳性）
        if word_a and word_b and word_a != word_b:
            return False
        if num_a and num_b:
            # 有 `<`/`>` 修饰时视为"低于/高于检出限"，与裸值视为同一报告口径
            return _num_eq(num_a, num_b)
        return not num_a and not num_b
    return False


def _unit_eq(annotated: str, predicted: str) -> bool:
    """单位比较：归一化大小写/微符号/乘号/**无量纲占位符**写法。

    背景：标注写 ``ng/ml``，系统规范化后输出 ``ng/mL``；``umol/L`` vs ``μmol/L``；
    ``10^9/L`` vs ``×10⁹/L``；``/ul`` vs ``个/μL``（镜检计数的计数单位，
    "个"是量词不是量纲）。这些是**同一单位的不同写法**，不应判错。
    但不同量级（``g/L`` vs ``g/dL``）必须判错——只做符号写法归一，不做换算。
    """
    import unicodedata

    # 无量纲占位符：标注常写 ``-``/``/``，系统输出空串
    _BLANK_UNITS = {"", "-", "—", "–", "/", "无", "n/a", "na"}

    def norm(value: str) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip()
        text = text.replace("μ", "u").replace("µ", "u").replace("渭", "u")
        text = text.replace("×", "x").replace("＊", "*").replace("·", "*")
        text = text.replace("^", "").replace("⁹", "9").replace("⁰", "0")
        text = text.replace(" ", "")
        text = text.lower()
        if text in _BLANK_UNITS:
            return ""
        # 镜检计数单位：`个/ul`、`个/HP`、`/ul`、`/HP` 的量词"个"无信息量
        if text.startswith("个/"):
            text = text[1:]
        # HP / HPF / 高倍视野 都是"每高倍视野"；LP / LPF / 低倍视野 同理
        text = re.sub(r"/(高倍视野|高倍)$", "/hp", text)
        text = re.sub(r"/(低倍视野|低倍)$", "/lp", text)
        text = re.sub(r"/(hpf)$", "/hp", text)
        text = re.sub(r"/(lpf)$", "/lp", text)
        return text

    return norm(annotated) == norm(predicted)


def score_k1k2(
    annotations: list[dict],
    pred_dir: Path,
    dry_run: bool = False,
    include_classes: tuple[str, ...] = ("primary",),
) -> dict:
    """K1/K2 计分。

    ``include_classes``：只统计这些 ``kpiClass`` 的字段（默认仅 primary）。
    背景：真实标注里混有定性检验（核酸"未检出"、尿常规"阴性"）与影像描述
    （"影像诊断=支气管肺炎"），它们天然没有单位 → unit 比对必然失败，
    若计入会污染分母、让准确率失真。预处理脚本已写入 ``kpiClass``，
    此处按类过滤，并把定性项**单独统计**供参考。
    """
    k1_hit = k1_total = k2_hit = k2_total = 0
    # 定性项单独统计（不进 K1/K2，但要看它抽得出来）
    qual_hit = qual_total = 0
    missing_preds, extra_fields, misses = [], [], []
    for ann in annotations:
        pred_file = pred_dir / (Path(ann["file"]).stem + ".json")
        if not pred_file.exists():
            missing_preds.append(str(pred_file))
            continue
        pred = json.loads(pred_file.read_text(encoding="utf-8"))
        pred_fields = pred.get("fields", [])
        # 用**严格归一化**名建索引（保留百分比/计数区分，避免成对指标被覆盖）
        pmap: dict[str, dict] = {}
        for f in pred_fields:
            pmap.setdefault(normalize_name(str(f.get("name") or ""), keep_count_kind=True), f)

        def _find_pred(name: str, unit: str = "") -> tuple[dict | None, str]:
            """返回 (预测字段, 匹配方式)。

            先用严格归一化索引命中；未命中再遍历，但**必须过 names_match
            的安全阀**（百分比/计数不可混配，裸名靠 ``unit`` 补判）。

            多个候选时，**优先取单位体系与标注一致的那个**：
            尿常规同时有「尿镜检红细胞 个/HPF」和「红细胞计数 /ul」，
            标注「红细胞 个/HP」应命中前者而不是后者。
            """
            key = normalize_name(name, keep_count_kind=True)
            if key in pmap:
                return pmap[key], "normalized"
            candidates = [
                cand for cand in pred_fields
                if names_match(
                    name,
                    str(cand.get("name") or ""),
                    a_unit=unit,
                    b_unit=str(cand.get("unit") or ""),
                )
            ]
            if not candidates:
                return None, "miss"
            if len(candidates) == 1:
                return candidates[0], "normalized_loose"
            # 优先单位等价 / 可换算者，其次镜检↔镜检、定量↔定量
            def _rank(cand: dict) -> tuple[int, int]:
                c_unit = str(cand.get("unit") or "")
                unit_score = 0
                if _unit_eq(unit, c_unit) or _units_convertible(unit, c_unit):
                    unit_score = 2
                elif _looks_microscopy_unit_pair(unit, c_unit):
                    unit_score = 1
                return (-unit_score, 0)
            candidates.sort(key=_rank)
            return candidates[0], "normalized_loose"

        annotated_names = set()
        for field in ann.get("fields", []):
            name = field["name"]
            cls = field.get("kpiClass", "primary")
            in_scope = cls in include_classes
            if in_scope:
                annotated_names.add(name)

            pf, how = _find_pred(name, str(field.get("unit") or ""))
            a_unit = field.get("unit", "")
            b_unit = (pf or {}).get("unit", "")
            # 数值比较：单位不同但可换算时（7.52 mmol/L ≡ 7520 μmol/L）
            # 必须换算后再比，否则"抽取正确"会被判成数值错。
            num_ok = pf is not None and _value_eq(
                field["value"], a_unit, pf.get("value", ""), b_unit
            )
            unit_ok = pf is not None and (
                _unit_eq(a_unit, b_unit) or _units_convertible(a_unit, b_unit)
            )
            hit = (
                pf is not None
                and num_ok
                and unit_ok
                and str(field.get("date", "")).strip()
                == str(pf.get("date", "")).strip()
            )

            if cls == "qualitative":
                qual_total += 1
                # 定性项比较：**先按数值等价，再按字符串**。
                # 真实缺陷（2026-10-23）：此前只做 `str(a) == str(b)`，
                # 于是 `132` vs `132.0`、`5.50` vs `5.5` 被判错——
                # 实测 69 项定性 miss 里有 27 项（39%）属此类纯格式差异。
                q_hit = pf is not None and _qual_value_eq(
                    field.get("value", ""), str(pf.get("value", ""))
                )
                qual_hit += int(q_hit)
                continue
            if not in_scope:
                continue

            k1_total += 1
            k1_hit += int(hit)
            if not hit:
                reason = []
                if pf is None:
                    reason.append("未抽取到该指标")
                else:
                    if not _num_eq(field["value"], pf.get("value", "")):
                        reason.append(f"数值 {field['value']!r}≠{pf.get('value')!r}")
                    if not _unit_eq(field.get("unit", ""), pf.get("unit", "")):
                        reason.append(f"单位 {field.get('unit')!r}≠{pf.get('unit')!r}")
                    if str(field.get("date", "")).strip() != str(pf.get("date", "")).strip():
                        reason.append(f"日期 {field.get('date')!r}≠{pf.get('date')!r}")
                misses.append(
                    {
                        "file": Path(ann["file"]).name,
                        "name": name,
                        "matchedAs": (pf or {}).get("name") if pf else None,
                        "abnormal": bool(field.get("abnormal")),
                        "reason": "；".join(reason),
                    }
                )
            if field.get("abnormal"):
                k2_total += 1
                k2_hit += int(hit)
        # 多余字段：用严格归一化名比对（避免成对指标被误判为同一项）
        ann_norm = {
            normalize_name(n, keep_count_kind=True) for n in annotated_names
        }
        for extra_key in set(pmap.keys()) - ann_norm:
            extra_fields.append(f"{pred_file.name}:{pmap[extra_key].get('name')}")

    result = {
        "K1_field_accuracy": (k1_hit, k1_total),
        "K2_abnormal_recall": (k2_hit, k2_total),
        "qualitative_hit_rate(参考)": (qual_hit, qual_total),
        "missing_predictions": missing_preds,
        "extra_pred_fields(抽查)": extra_fields,
        "misses": misses,
    }
    if dry_run or not annotations:
        print("[dry-run] 标注/预测未就绪，仅校验脚本可运行。")
        return result
    r1 = k1_hit / k1_total if k1_total else float("nan")
    r2 = k2_hit / k2_total if k2_total else float("nan")
    result["verdict"] = {"K1": _gate(r1, THRESHOLDS["K1"]), "K2": _gate(r2, THRESHOLDS["K2"])}
    return result


def score_k3(rows: list[dict]) -> dict:
    judged = [r for r in rows if r.get("verdict_manual") in ("pass", "fail")]
    passed = sum(1 for r in judged if r["verdict_manual"] == "pass")
    out = {"judged": len(judged), "passed": passed}
    if not judged:
        print("[dry-run] K3 verdict_manual 尚未人工填写（需真实 LLM 网关下评测）。")
        return out
    rate = passed / len(judged)
    out.update({"rate": round(rate, 4), "verdict": {"K3": _gate(rate, THRESHOLDS["K3"])}})
    return out


def _gate(rate: float, threshold: float) -> str:
    return "GO" if rate >= threshold else "NO-GO"


def selftest() -> None:
    # --- 名称归一化：抽取正确但命名不同，不得判错 ---
    assert names_match("白细胞", "白细胞计数")
    assert names_match("中性粒细胞%", "中性粒细胞百分比")
    assert names_match("血小板", "血小板计数")
    assert names_match("血糖(空腹)", "空腹血糖")
    assert names_match("铁蛋白", "铁蛋白")
    # --- 安全阀：成对/易混指标绝不能被判为同一项 ---
    # 血常规里 % 与 # 是两个不同指标（实测曾把 30.5% 错配成 2.22×10^9/L）
    assert not names_match("中性粒细胞%", "中性粒细胞")
    assert not names_match("中性粒细胞#", "中性粒细胞百分比")
    assert not names_match("淋巴细胞%", "淋巴细胞计数")
    assert not names_match("收缩压", "舒张压")
    assert not names_match("MCV", "MCH")
    assert not names_match("MCH", "MCHC")
    assert not names_match("总胆固醇", "甘油三酯")
    assert not names_match("白细胞", "红细胞")
    # --- 裸名 + 单位补判 kind：真实报告常见"中性粒细胞 2.22 10^9/L" ---
    # 裸计数项（标注单位 10^9/L）≠ 百分比项 —— 必须判不同
    assert not names_match("中性粒细胞", "中性粒细胞百分比", a_unit="10^9/L", b_unit="%")
    assert not names_match("淋巴细胞", "淋巴细胞百分比", a_unit="10^9/L", b_unit="%")
    # 裸百分比项（标注单位 %）≠ 计数项
    assert not names_match("中性粒细胞", "中性粒细胞计数", a_unit="%", b_unit="10^9/L")
    # 同 kind 的裸名 ↔ 带后缀标准名，仍应命中（不能因加了单位就漏配）
    assert names_match("中性粒细胞", "中性粒细胞百分比", a_unit="%", b_unit="%")
    assert names_match("中性粒细胞", "中性粒细胞计数", a_unit="10^9/L", b_unit="10^9/L")

    # --- 五分类族校验：裸名绝不能跨族/跨指标合并 ---
    # 真实缺陷（2026-10-23）：早期只比对 kind，导致「中性粒细胞」被判等于
    # 「白细胞计数」，10 份血常规 × 5 项 = 50 次错配，K1 从 52.84% 掉到 49.29%。
    assert not names_match("中性粒细胞", "白细胞计数", a_unit="10^9/L", b_unit="10^9/L")
    assert not names_match("淋巴细胞", "白细胞计数", a_unit="10^9/L", b_unit="10^9/L")
    assert not names_match("单核细胞", "中性粒细胞计数", a_unit="10^9/L", b_unit="10^9/L")
    assert not names_match("嗜酸性粒细胞", "嗜碱性粒细胞计数", a_unit="10^9/L", b_unit="10^9/L")
    # 同族才可合并
    assert names_match("淋巴细胞", "淋巴细胞计数", a_unit="10^9/L", b_unit="10^9/L")
    assert names_match("嗜酸性粒细胞", "嗜酸性细胞百分比", a_unit="%", b_unit="%")

    # --- 同义异名：确为同一指标的写法差异（不得判错） ---
    # 真实漏配（2026-10-23）：10 份血常规 × 4 项 = 40 次"未抽取到"，
    # 其实都抽到了，只是标注名与系统标准名写法不同。
    assert names_match("红细胞分布宽度", "红细胞分布宽度CV")
    assert names_match("红细胞比容", "红细胞压积")
    assert names_match("血小板比容", "血小板压积")
    assert names_match("红细胞平均体积", "平均红细胞体积")
    # 转氨酶 ↔ 转移酶、白球比 ↔ 白蛋白/球蛋白比值
    assert names_match("谷丙转氨酶", "丙氨酸氨基转移酶")
    assert names_match("谷草转氨酶", "天门冬氨酸氨基转移酶")
    assert names_match("谷氨酰氨转移酶", "谷氨酰转肽酶")
    assert names_match("白球比", "白蛋白/球蛋白比值")
    # 乙肝五项 / 血气 / 微量元素 / 传染病筛查的常见异名
    assert names_match("乙肝表面抗体", "乙型肝炎表面抗体")
    assert names_match("乙肝核心抗体", "乙型肝炎核心抗体")
    assert names_match("二氧化碳总量", "总二氧化碳")
    assert names_match("铁", "血清铁")
    assert names_match("磷", "无机磷")
    assert not names_match("磷", "碱性磷酸酶")      # 磷 ≠ 磷酸酶
    assert not names_match("磷", "磷脂")
    assert names_match("PH值", "pH")
    assert names_match("人免疫缺陷病毒筛查试验", "人类免疫缺陷病毒抗体筛查试验")
    assert names_match("丙型肝炎病毒抗体筛查试验", "丙型肝炎病毒抗体")
    # 同义组不能把易混系列指标拉进来
    assert not names_match("平均红细胞体积", "平均血红蛋白量")
    assert not names_match("平均血红蛋白量", "平均血红蛋白浓度")
    assert not names_match("谷丙转氨酶", "谷草转氨酶")
    assert not names_match("乙型肝炎表面抗原", "乙型肝炎表面抗体")
    # --- 单位写法等价 ---
    assert _unit_eq("ng/ml", "ng/mL")
    assert _unit_eq("umol/L", "μmol/L")
    assert _unit_eq("10^9/L", "10^9/L")
    # 无量纲占位符与镜检计数量词（"个"不是量纲）
    assert _unit_eq("", "-")
    assert _unit_eq("", "/")
    assert _unit_eq("/ul", "个/μL")
    assert _unit_eq("个/HP", "/HP")
    # HP/HPF/高倍视野 都是"每高倍视野"
    assert _unit_eq("/HP", "个/HPF")
    assert _unit_eq("/LP", "个/LPF")
    # 不同量级/不同量纲必须判错
    assert not _unit_eq("g/L", "g/dL")
    assert not _unit_eq("mmol/L", "mg/dL")
    assert not _unit_eq("/HP", "10^9/L")      # 镜检 vs 血液计数，绝不能等同

    # --- 数值比较：同量纲不同量级要先换算（真实缺陷 2026-10-23）---
    # 微量元素「铁」：标注 7.52 mmol/L，系统输出 7520 μmol/L（同一数值）
    assert _value_eq("7.52", "mmol/L", "7520", "μmol/L")
    assert _value_eq("7.52", "mmol/L", "7.52", "mmol/L")
    assert _value_eq("100", "U/L", "100", "IU/L")
    # 量纲不同时，数值巧合不能判命中
    assert not _value_eq("5", "mmol/L", "5", "%")
    assert not _value_eq("1.0", "g/L", "1.0", "%")

    # --- 定性值比较（真实缺陷 2026-10-23：27/69 定性 miss 是纯格式差异）---
    assert _qual_value_eq("132", "132.0")        # 数值格式
    assert _qual_value_eq("5.50", "5.5")
    assert _qual_value_eq("", "-")               # 空与占位符
    assert _qual_value_eq("阴性/2.00", "阴性/<2.00")   # 检出限前缀
    assert _qual_value_eq("未见", "未见")
    # 安全阀：临床意义相反/不同的值绝不能判等
    assert not _qual_value_eq("阴性", "阳性")
    assert not _qual_value_eq("未见", "0")
    assert not _qual_value_eq("黄色", "黄褐色")

    # --- 标本前缀归一：系统给尿/便项目加前缀是正确建模 ---
    assert names_match("颜色", "尿液颜色")
    assert names_match("糖", "尿糖")
    assert names_match("酮体", "尿酮体")
    assert names_match("白细胞酯酶", "尿白细胞酯酶")
    # 安全阀：不同标本体系不得互配（尿糖 ≠ 血糖）
    assert not names_match("糖", "血糖")
    assert not names_match("蛋白", "血红蛋白")

    ann = [
        {
            "file": "files/a.jpg",
            "fields": [
                # 标注用原名，预测用标准名 -> 应命中
                {"name": "血糖(空腹)", "value": "6.80", "unit": "mmol/L",
                 "date": "2026-03-12", "abnormal": True, "kpiClass": "primary"},
                {"name": "白细胞", "value": "6.8", "unit": "10^9/L",
                 "date": "2026-03-12", "abnormal": False, "kpiClass": "primary"},
                # 数值不同 -> 应未命中
                {"name": "血红蛋白", "value": "145", "unit": "g/L",
                 "date": "2026-03-12", "abnormal": False, "kpiClass": "primary"},
                # 定性项 -> 不进 K1/K2
                {"name": "乙肝表面抗原", "value": "阴性", "unit": "",
                 "date": "2026-03-12", "abnormal": False, "kpiClass": "qualitative"},
            ],
        }
    ]
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.json").write_text(
            json.dumps(
                {
                    "file": "files/a.jpg",
                    "fields": [
                        {"name": "空腹血糖", "value": "6.8", "unit": "mmol/L",
                         "date": "2026-03-12"},
                        {"name": "白细胞计数", "value": "6.8", "unit": "10^9/L",
                         "date": "2026-03-12"},
                        {"name": "血红蛋白", "value": "146", "unit": "g/L",
                         "date": "2026-03-12"},
                        {"name": "乙肝表面抗原", "value": "阴性", "unit": "",
                         "date": "2026-03-12"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        res = score_k1k2(ann, Path(td))
        # 血糖 + 白细胞命中；血红蛋白数值不符 -> 2/3
        assert res["K1_field_accuracy"] == (2, 3), res
        assert res["K2_abnormal_recall"] == (1, 1), res
        assert res["qualitative_hit_rate(参考)"] == (1, 1), res
        assert res["verdict"]["K1"] == "NO-GO" and res["verdict"]["K2"] == "GO", res

    k3 = score_k3([{"id": "q1", "verdict_manual": "pass"}, {"id": "q2", "verdict_manual": None}])
    assert k3["judged"] == 1 and k3["passed"] == 1 and k3["rate"] == 1.0, k3
    print("selftest OK")


def _summarize(result: dict) -> str:
    """把 (hit,total) 元组渲染成百分比文本。"""
    lines = []
    for key in ("K1_field_accuracy", "K2_abnormal_recall", "qualitative_hit_rate(参考)"):
        hit, total = result.get(key, (0, 0))
        rate = (hit / total * 100) if total else float("nan")
        lines.append(f"  {key}: {hit}/{total} = {rate:.2f}%" if total else f"  {key}: 无样本")
    if result.get("missing_predictions"):
        lines.append(f"  缺预测的报告: {len(result['missing_predictions'])}")
    if result.get("misses"):
        lines.append(f"  未命中明细: {len(result['misses'])} 条（见 JSON 输出）")
    if result.get("verdict"):
        lines.append(f"  结论: {result['verdict']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("k1k2")
    p1.add_argument("--dir", default=".")
    p1.add_argument("--annotations", default=None)
    p1.add_argument("--dry-run", action="store_true")
    p1.add_argument(
        "--classes",
        default="primary",
        help="逗号分隔的 kpiClass，默认 primary（排除定性/影像）",
    )
    p3 = sub.add_parser("k3")
    p3.add_argument("--questions", required=True)
    sub.add_parser("selftest")
    args = ap.parse_args()

    if args.cmd == "selftest":
        selftest()
    elif args.cmd == "k1k2":
        base = Path(args.dir)
        ann_path = Path(args.annotations) if args.annotations else base / "annotations.jsonl"
        annotations = _load_jsonl(ann_path) if ann_path.exists() else []
        classes = tuple(c.strip() for c in args.classes.split(",") if c.strip())
        result = score_k1k2(
            annotations,
            base / "predictions",
            dry_run=args.dry_run or not annotations,
            include_classes=classes,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\n摘要：")
        print(_summarize(result))
    elif args.cmd == "k3":
        rows = _load_jsonl(Path(args.questions))
        print(json.dumps(score_k3(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
