"""指标名归一化与模糊匹配候选（跨医院命名差异的第一道防线）。

真实报告里同一指标的常见写法（实测样本）：
- 血红蛋白 / 血色素 / HGB / Hb / 血红蛋白量
- 红细胞压积 / 红细胞比积 / 红细胞比容 / HCT
- 平均红细胞血红蛋白含量 / 平均血红蛋白量 / MCH
- 空腹血糖 / 空腹血葡萄糖 / 血糖 / GLU / FBG
- 总胆固醇 / 胆固醇 / TC / Chol
- 体重指数 / 体质指数 / BMI
- 白细胞计数 / 白细胞 / 白血球 / WBC

策略顺序（成本由低到高）：
1. 归一化后精确命中（标准名 / code / 已学别名）
2. 等价词替换后再精确命中（含“计数/测定/定量/百分比”等修饰词剥离）
3. 字符相似度（SequenceMatcher）取最高候选，需超过阈值
4. AI 语义对齐（如果前 3 步都不够，见 matcher 层）
5. 仍无法确定 → 新建标准指标（AI 生成 code/标准名/单位/分类）

本模块只做 1–3（纯函数、可单测、无外部依赖），4–5 在 `indicator_aligner.py`。
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Sequence, Tuple

from app.preprocessing.textnorm import normalize_text

# 修饰词：出现/不出现不应影响指标身份
_DECORATORS = (
    "定量测定",
    "定量",
    "测定",
    "检测",
    "检查",
    "计数",
    "浓度",
    "含量",
    "水平",
    "百分比",
    "百分率",
    "比值",
    "量",
    "值",
    "结果",
)

# 等价写法（同义前缀/后缀）
_SYNONYMS = (
    ("红血球", "红细胞"),
    ("白血球", "白细胞"),
    ("血色素", "血红蛋白"),
    ("比积", "压积"),
    ("比容", "压积"),
    ("体质指数", "身体质量指数"),
    ("体重指数", "身体质量指数"),
    ("谷丙转氨酶", "丙氨酸氨基转移酶"),
    ("谷草转氨酶", "天门冬氨酸氨基转移酶"),
    # 「转氨酶」与「转移酶」是同一种写法差异（真实缺陷，2026-10-23：
    # 医院写「丙氨酸氨基转氨酶」，标准名是「丙氨酸氨基转移酶」，未归一 →
    # 同一指标在库中分裂，K1 漏配 3 次/份 × 3 项）
    ("丙氨酸氨基转氨酶", "丙氨酸氨基转移酶"),
    ("天门冬氨酸氨基转氨酶", "天门冬氨酸氨基转移酶"),
    # 裸后缀「门冬氨酸」只在不属于「天门冬氨酸」时才补前缀。
    # 否则「天门冬氨酸」会被再补一次 → 「天天门冬氨酸…」（真实 bug）。
    ("(?<!天)门冬氨酸", "天门冬氨酸"),
    ("门冬氨酸氨基转氨酶", "天门冬氨酸氨基转移酶"),
    # 裸后缀「转肽酶」同理：只在前面不是「谷氨酰」时才补，
    # 否则「谷氨酰转肽酶」→「谷氨酰谷氨酰转肽酶」（真实 bug）。
    ("谷氨酰氨转移酶", "谷氨酰转肽酶"),
    ("谷氨酰转移酶", "谷氨酰转肽酶"),
    ("γ谷氨酰转肽酶", "谷氨酰转肽酶"),
    ("r谷氨酰转肽酶", "谷氨酰转肽酶"),
    ("(?<!谷氨酰)转肽酶", "谷氨酰转肽酶"),
    # 白球比 = 白蛋白/球蛋白比值（同一指标的计算式写法）
    ("白球比", "白蛋白球蛋白比值"),
    ("白蛋白球蛋白比", "白蛋白球蛋白比值"),
    ("白蛋白/球蛋白比值", "白蛋白球蛋白比值"),
    ("白蛋白/球蛋白", "白蛋白球蛋白比值"),
    ("a/g", "白蛋白球蛋白比值"),
    ("ag比", "白蛋白球蛋白比值"),
    # 乙肝五项：医院常写简称「乙肝」，标准名用全称「乙型肝炎」
    # （真实缺陷，2026-10-23 评测：乙肝表面抗体/e抗体/核心抗体 3 项漏配）
    ("乙肝表面抗原", "乙型肝炎表面抗原"),
    ("乙肝表面抗体", "乙型肝炎表面抗体"),
    ("乙肝e抗原", "乙型肝炎e抗原"),
    ("乙肝e抗体", "乙型肝炎e抗体"),
    ("乙肝核心抗体", "乙型肝炎核心抗体"),
    ("乙肝两对半", "乙型肝炎五项"),
    # 传染病筛查：简称 vs 全称。
    # ⚠ 不能写 ("…筛查", "…筛查试验") 这类**后缀追加**映射：
    #   目标串包含源串，替换后仍会命中自己 →「…筛查试验试验」。
    #   只做等长/改写的整串映射。
    ("艾滋病毒", "人类免疫缺陷病毒"),
    ("艾滋抗体", "人类免疫缺陷病毒抗体"),
    ("人免疫缺陷病毒", "人类免疫缺陷病毒"),
    ("丙肝抗体", "丙型肝炎病毒抗体"),
    ("梅毒筛查", "梅毒抗体筛查试验"),
    # 血气：二氧化碳总量 ↔ 总二氧化碳（词序差异）
    ("二氧化碳总量", "总二氧化碳"),
    ("二氧化碳结合力", "总二氧化碳"),
    # 微量元素：血清铁 ↔ 铁（报告常加"血清"限定）
    ("血清铁", "铁"),
    ("血铁", "铁"),
    # 电解质/无机盐：报告写「无机磷」「血清磷」，标准名「磷」
    # （真实缺陷，2026-10-23：生化报告抽到「无机磷」但标注写「磷」，被判未抽取）
    ("无机磷", "磷"),
    ("血清磷", "磷"),
    ("血磷", "磷"),
    ("尿胆元", "尿胆原"),
    ("肌酸酐", "肌酐"),
    ("尿素氮", "尿素"),
    ("空腹血葡萄糖", "空腹血糖"),
    ("葡萄糖", "血糖"),
    # 完整短语替换：MCH 的常见写法（不能用短词“平均红细胞血红蛋白”，
    # 否则 MCHC“平均红细胞血红蛋白浓度”会被错并到 MCH）
    ("平均红细胞血红蛋白含量", "平均血红蛋白量"),
    ("平均红细胞血红蛋白量", "平均血红蛋白量"),
)

# 英文/拼音缩写 → 中文标准名（医院报告大量使用缩写）
_ABBREVIATIONS = {
    "hgb": "血红蛋白",
    "hb": "血红蛋白",
    "wbc": "白细胞计数",
    "rbc": "红细胞计数",
    "plt": "血小板计数",
    "hct": "红细胞压积",
    "mcv": "平均红细胞体积",
    "mch": "平均血红蛋白量",
    "mchc": "平均血红蛋白浓度",
    "glu": "空腹血糖",
    "fbg": "空腹血糖",
    "hba1c": "糖化血红蛋白",
    "alt": "丙氨酸氨基转移酶",
    "gpt": "丙氨酸氨基转移酶",
    "ast": "天门冬氨酸氨基转移酶",
    "got": "天门冬氨酸氨基转移酶",
    "tbil": "总胆红素",
    "dbil": "直接胆红素",
    "ibil": "间接胆红素",
    "tp": "总蛋白",
    "alb": "白蛋白",
    "glb": "球蛋白",
    "crea": "肌酐",
    "cr": "肌酐",
    "bun": "尿素",
    "urea": "尿素",
    "ua": "尿酸",
    "tc": "总胆固醇",
    "chol": "总胆固醇",
    "tg": "甘油三酯",
    "ldl": "低密度脂蛋白胆固醇",
    "ldlc": "低密度脂蛋白胆固醇",
    "hdl": "高密度脂蛋白胆固醇",
    "hdlc": "高密度脂蛋白胆固醇",
    "tsh": "促甲状腺激素",
    "crp": "C反应蛋白",
    "esr": "红细胞沉降率",
    "bmi": "身体质量指数",
    "pdw": "血小板分布宽度",
    "mpv": "平均血小板体积",
}

_PUNCT = re.compile(r"[\s（）()【】\[\]·:：.。，,、\-_/\\|%％]+")


def normalize_indicator_name(name: str, *, strip_decorators: bool = True) -> str:
    """指标名归一：NFKC + 去标点空白 + 小写 + 缩写展开 + 等价词替换（+ 剥修饰词）。

    两级归一很关键：
    - `strip_decorators=False`（primary）：保留“浓度/含量”等词，用于区分 MCH/MCHC 系列；
    - `strip_decorators=True`（relaxed）：剥掉修饰词，兼容“总胆固醇测定”这类写法。
    """
    if not name:
        return ""
    text = normalize_text(str(name)).lower()
    text = _PUNCT.sub("", text)
    # 纯缩写（或"中文+缩写"）先展开，避免中英混写被当成两个指标
    if text in _ABBREVIATIONS:
        text = _ABBREVIATIONS[text]
    else:
        for abbr, cn in _ABBREVIATIONS.items():
            if abbr not in text:
                continue
            if cn in text:
                # 中文标准名已在，缩写作冗余噪音去掉（"血红蛋白HGB"）
                text = text.replace(abbr, "")
            else:
                text = text.replace(abbr, cn)
    text = _apply_synonyms(text)
    if not strip_decorators:
        return text
    changed = True
    while changed:
        changed = False
        for deco in _DECORATORS:
            if text.endswith(deco) and len(text) > len(deco) + 1:
                text = text[: -len(deco)]
                changed = True
    return text


# 同义词替换：**最长优先、单次扫描不回头**。
# 早期实现是 `for src, dst in _SYNONYMS: text = text.replace(src, dst)`，
# 逐条顺序替换，后一条会命中前一条的产物，产生重复展开（真实缺陷）：
#   「谷氨酰转肽酶」→ replace("转肽酶","谷氨酰转肽酶") → 「谷氨酰谷氨酰转肽酶」
#   「天门冬氨酸氨基转移酶」→ replace("门冬氨酸","天门冬氨酸") → 「天天门冬氨酸…」
# 现在改为：把原文里所有命中按位置排序，从最长/最左开始替换，
# 替换结果不再参与本轮匹配。源写法允许是正则（用于带否定前瞻的后缀规则）。
_SYNONYM_SORTED = tuple(
    sorted(
        ((re.compile(src), dst, src) for src, dst in _SYNONYMS),
        key=lambda item: -len(item[2]),
    )
)


def _apply_synonyms(text: str) -> str:
    if not text:
        return text
    matches: List[tuple[int, int, str]] = []
    for pattern, dst, _src in _SYNONYM_SORTED:
        for m in pattern.finditer(text):
            if m.end() > m.start():
                matches.append((m.start(), m.end(), dst))
    if not matches:
        return text
    # 左边界优先、同起点取最长，且区间不重叠（已被覆盖的位置跳过）
    matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
    out: List[str] = []
    cursor = 0
    for start, end, dst in matches:
        if start < cursor:
            continue           # 与已替换区间重叠，跳过（不回头替换）
        out.append(text[cursor:start])
        out.append(dst)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def name_core_tokens(name: str) -> List[str]:
    """拆出用于相似度比较的片段（中文按 2 字滑窗，英文按词）。"""
    norm = normalize_indicator_name(name)
    tokens: List[str] = []
    cjk = re.sub(r"[a-z0-9%]+", "", norm)
    for i in range(len(cjk) - 1):
        tokens.append(cjk[i : i + 2])
    tokens.extend(re.findall(r"[a-z0-9%]+", norm))
    return tokens


def similarity(a: str, b: str) -> float:
    """0~1 相似度：归一化后相等优先，其次整串比值 + 片段重叠。"""
    na, nb = normalize_indicator_name(a), normalize_indicator_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(name_core_tokens(a)), set(name_core_tokens(b))
    containment = 0.0

    # 一方完整包含另一方（中英混写很常见："血红蛋白HGB" vs "血红蛋白"）
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        containment = len(shorter) / len(longer)

    if not ta or not tb:
        return max(ratio, containment)
    overlap = len(ta & tb) / len(ta | tb)
    # 包含关系的权重很高：这通常是同一指标的完整/简写形式
    return max(ratio, 0.25 * overlap + 0.75 * containment, containment)


@dataclass
class Candidate:
    key: str          # code 或归一化名
    label: str        # 展示名（标准中文名）
    score: float
    unit: Optional[str] = None


def best_fuzzy_candidates(
    raw_name: str,
    vocabulary: Iterable[Tuple[str, str, Optional[str]]],
    *,
    limit: int = 3,
) -> List[Candidate]:
    """在已知指标词表中找最可能的候选（vocabulary: (key, label, unit)）。"""
    scored: List[Candidate] = []
    for key, label, unit in vocabulary:
        score = similarity(raw_name, label)
        if score > 0:
            scored.append(Candidate(key=key, label=label, score=round(score, 3), unit=unit))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:limit]
