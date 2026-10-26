"""指标字典映射（跨医院别名对齐、单位归一、参考区间版本化、AI 兜底、E-3 队列）。

对齐策略（成本与确定性由高到低，**能确定就不调 AI**）：
1. 归一化精确命中（标准名 / code / 已学别名）→ 0 成本、确定性
2. 修饰词/缩写/同义写法归一后精确命中 → 0 成本、确定性
3. 字符相似度 ≥ 0.92 → 0 成本、高置信
4. **AI 语义对齐**（默认可用）→ 判定已有指标则复用 code；判新则新建标准指标
5. AI 不可用或仍无法确定 → 原文保留 + 入 dictionary_misses 供人工补录

单位：命中标准指标后，值/参考区间/单位统一换算到字典标准单位，
原始值与原始单位保存在 `raw_value`/`raw_unit`（审计与回溯）。

AI 底座原则（AGENTS.md §AI 底座）：这类"语义判断"默认交给 AI，
规则只用于低成本的确定性收敛，且**绝不**允许把成对/系列指标误合并。
"""

import logging
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.ids import new_id
from app.db.models import DictionaryMiss, IndicatorDictionary, IndicatorRefRange
from app.preprocessing.indicator_names import (
    best_fuzzy_candidates,
    normalize_indicator_name,
)
from app.preprocessing.units import convert_to_canonical

logger = logging.getLogger("app.mapper")

# 模糊命中阈值：低于此值交给 AI 判断（避免"平均红细胞体积"被误并到 MCV 家族）
FUZZY_ACCEPT = 0.92

# 已知的"成对/系列"指标：其名字前缀高度相似，必须靠后缀区分，禁止模糊合并
_PAIR_TRAITS = (
    ("收缩压", "舒张压"),
    ("总胆固醇", "甘油三酯"),
    ("高密度脂蛋白", "低密度脂蛋白"),
    ("中性粒细胞", "淋巴细胞"),
    ("中性粒细胞", "单核细胞"),
    ("中性粒细胞", "嗜酸性细胞"),
    ("中性粒细胞", "嗜碱性细胞"),
    ("红细胞计数", "白细胞计数"),
    ("红细胞计数", "血小板计数"),
    ("总胆红素", "直接胆红素"),
    ("平均红细胞体积", "平均血红蛋白量"),
    ("平均红细胞体积", "平均血红蛋白浓度"),
    ("平均血红蛋白量", "平均血红蛋白浓度"),
    ("尿蛋白", "尿糖"),
    ("丙氨酸氨基转移酶", "天门冬氨酸氨基转移酶"),
)


def _norm(name: str, *, strip_decorators: bool = True) -> str:
    """归一化（委托 indicator_names，含缩写展开与可选的修饰词剥离）。"""
    return normalize_indicator_name(name, strip_decorators=strip_decorators)


# `%` / `#` 后缀先规范成「百分比」/「计数」再归一化。
# 原因：`normalize_indicator_name` 的 `_PUNCT` 会把 `%` 直接**剥掉**，
# 于是「嗜酸性粒细胞%」与别名「嗜酸性粒细胞百分比」归一化后不相等
# （一个变 `嗜酸性粒细胞`、一个是 `嗜酸性粒细胞百分比`），精确匹配失败，
# 最终被 AI 另建成 `EOS_ABS`（真实缺陷，2026-10-23 血常规 2 项）。
_PCT_SUFFIX_RE = re.compile(r"[（(]?\s*[%％]\s*[)）]?\s*$")
_CNT_SUFFIX_RE = re.compile(r"[（(]?\s*#\s*[)）]?\s*$")

# 五分类的「规范 code」：`<FAMILY>_PCT` / `<FAMILY>_CNT`。
# 历史遗留的 `*_ABS`（AI 在字典缺分组时另建）与之语义完全重复，
# 属主数据重复。命中冲突时**规范 code 优先**，避免脏别名抢走匹配
# （真实缺陷：`中性粒细胞#` 精确命中旧 `NEUT_ABS` 而非新的 `NEUT_CNT`）。
_CANONICAL_CODE_RE = re.compile(r"^(NEUT|LYMPH|MONO|EOS|BASO)_(PCT|CNT)$")


def _is_canonical_cell_diff(entry: "_DictEntry") -> bool:
    return bool(_CANONICAL_CODE_RE.match(entry.code or ""))


def _norm_with_kind(name: str, *, strip_decorators: bool = True) -> str:
    """归一化前把 `%`/`#` 后缀转成「百分比」/「计数」，保住分族语义。"""
    text = str(name or "").strip()
    if _PCT_SUFFIX_RE.search(text):
        text = _PCT_SUFFIX_RE.sub("百分比", text)
    elif _CNT_SUFFIX_RE.search(text):
        text = _CNT_SUFFIX_RE.sub("计数", text)
    return _norm(text, strip_decorators=strip_decorators)


class _DictEntry:
    __slots__ = (
        "id", "code", "name_cn", "unit", "aliases", "norm_set", "relaxed_set", "category",
    )

    def __init__(self, row: IndicatorDictionary):
        self.id = row.id
        self.code = row.code
        self.name_cn = row.name_cn
        self.unit = row.unit
        self.category = row.category
        self.aliases = list(row.aliases or [])
        names = [row.name_cn or "", row.code or "", *self.aliases]
        # primary：保留“浓度/含量”等特征词，用于区分 MCH/MCHC 系列
        # 用 _norm_with_kind 让 `%`/`#` 后缀保住分族语义（否则被 _PUNCT 剥掉）
        self.norm_set = {_norm_with_kind(n, strip_decorators=False) for n in names}
        self.norm_set.discard("")
        # relaxed：剥掉修饰词，兼容“总胆固醇测定”/“白细胞计数”
        self.relaxed_set = {_norm_with_kind(n) for n in names}
        self.relaxed_set.discard("")


def load_dictionary(db: Session) -> List[_DictEntry]:
    """加载启用中的标准指标；过滤明显噪声项（历史脏数据防御）。

    历史版本允许 AI 把排版碎片（T1/年0/每3）写成标准指标并落库；
    这里在加载阶段就排除，避免它们继续"精确命中"污染新草稿。
    """
    from app.preprocessing.indicator_aligner import is_plausible_indicator_name

    rows = db.execute(
        select(IndicatorDictionary).where(IndicatorDictionary.active.is_(True))
    ).scalars()
    entries: List[_DictEntry] = []
    for row in rows:
        ok, _reason = is_plausible_indicator_name(row.name_cn or "")
        if not ok:
            logger.warning(
                "dictionary.skip_noise code=%s name=%r", row.code, row.name_cn
            )
            continue
        entries.append(_DictEntry(row))
    return entries


def _empty_dictionary(db: Session) -> bool:
    return db.scalar(select(IndicatorDictionary.id).limit(1)) is None


def load_dictionary_ensured(db: Session) -> List[_DictEntry]:
    """字典为空时自动播种（幂等），再加载。

    真实环境缺陷：容器只跑迁移不跑种子，字典为 0 行使全部指标“字典未命中”。
    这里做一次自愈，避免不同部署环境行为不一致。
    """
    entries = load_dictionary(db)
    if entries or not _empty_dictionary(db):
        return entries
    try:
        from app.data.dictionary_seed import upsert

        stats = upsert(db)
        logger.warning(
            "dictionary.auto_seeded created=%s total=%s", stats["created"], stats["total"]
        )
        return load_dictionary(db)
    except Exception:  # noqa: BLE001  播种失败不应阻断抽取
        logger.exception("dictionary.auto_seed_failed")
        return entries


# 白细胞五分类的"百分比 / 计数"分族：裸名（如"中性粒细胞"）必须靠**单位**消歧，
# 否则计数值会被错配到百分比指标（实测缺陷：20250126 血常规 6 项 display_name 错）。
_CELL_DIFF_BASES = {
    "NEUT": ("中性粒细胞", "中性细胞"),
    "LYMPH": ("淋巴细胞",),
    "MONO": ("单核细胞",),
    "EOS": ("嗜酸性细胞", "嗜酸性粒细胞", "嗜酸细胞"),
    "BASO": ("嗜碱性细胞", "嗜碱性粒细胞", "嗜碱细胞"),
}

# 名字里显式声明"百分比/计数"的后缀标记
_MARKER_RE = re.compile(r"%|％|#|百分比|百分率|比率|计数")
_MARKER_KIND = {
    "%": "pct", "％": "pct", "百分比": "pct", "百分率": "pct", "比率": "pct",
    "#": "cnt", "计数": "cnt",
}


def _unit_kind(unit: str) -> str:
    """判断单位属于"百分比"还是"计数"。空/未知返回 ''。"""
    text = (unit or "").strip().lower().replace(" ", "")
    if not text:
        return ""
    if text in ("%", "％", "百分比", "percent"):
        return "pct"
    if "10^9" in text or "10*9" in text or "10⁹" in text or "×10⁹" in text or "x10^9" in text:
        return "cnt"
    return ""


# ---- 标本类型安全阀（血 / 尿 / 便）----
# 真实缺陷（2026-10-23 评测）：粪便常规/尿液分析的「白细胞」「红细胞」是**镜检**
# 项目（单位 `个/HP`、`/HP`），却被匹配到血常规的 WBC/RBC（`10^9/L`、`10^12/L`）。
# 血、尿、便里的"白细胞"是**完全不同的检验项目**，混配会让医生看到错误的量级
# （0.5 个/HP 被当成 0.5×10^9/L）。这是 AGENTS.md 第 8 条要拦的错配。
#
# 判定依据用**单位量纲**（比猜测标本类型可靠，且单位本来就在数据里）：
#   - 血液计数单位（10^9/L、10^12/L、/uL …）→ 量纲 count/volume
#   - 镜检单位（/HP、个/HP、/LP、/视野）→ 量纲 unknown（不在换算表里）
# 因此：候选指标标准单位量纲已知、而观测单位量纲未知且形态像镜检 → 拒绝。
_MICROCOPY_UNIT_RE = re.compile(
    r"(个|cells?)?\s*/\s*(hp|lp|hpf|lpf|视野|高倍|低倍)",
    re.I,
)


def _looks_microscopy_unit(unit: str) -> bool:
    """是否是镜检计数单位（每高倍/低倍视野），而非体积计数单位。"""
    text = str(unit or "").strip()
    if not text:
        return False
    if _MICROCOPY_UNIT_RE.search(text):
        return True
    return False


def _unit_dimension(unit: str) -> Optional[str]:
    """单位对应的量纲族（count/volume、mass/volume…）；无法判定返回 None。"""
    from app.preprocessing.units import _lookup, normalize_unit

    hit = _lookup(normalize_unit(unit))
    return hit[0] if hit else None


def _specimen_conflict(entry: "_DictEntry", unit: str) -> bool:
    """观测单位与字典标准单位是否属于**不同标本体系**（血 vs 镜检）。

    只在「观测是镜检单位、字典是体积计数单位」时判冲突；
    其它情况（单位缺失、都可换算、字典也无单位）一律不拦，交原有逻辑处理。
    """
    if not _looks_microscopy_unit(unit):
        return False
    dict_dim = _unit_dimension(entry.unit or "")
    if dict_dim is None:
        return False
    # 字典标准单位是体积/浓度类量纲 → 该指标属于血/体液定量项目，不是镜检。
    # 注意 `count12/volume` 是红细胞计数的独立维度（10^12/L）。
    return dict_dim in (
        "count/volume",
        "count12/volume",
        "mass/volume",
        "substance/volume",
    )


def _cell_diff_key(raw_name: str) -> Tuple[Optional[str], str]:
    """识别"白细胞五分类"指标 → (族名, 显式种类)。

    - 族名：NEUT/LYMPH/MONO/EOS/BASO，非五分类项目为 None；
    - 显式种类：'pct'（名字写了 %/百分比/比率）、'cnt'（写了 #/计数）、
      或 ''（裸名，只能靠单位判断）。
    注意 `normalize_indicator_name` 会剥掉 `%`，所以标记必须在**原始名**上找。
    """
    if not raw_name:
        return (None, "")
    text = str(raw_name).strip()
    found = _MARKER_RE.search(text)
    kind = _MARKER_KIND.get(found.group(0), "") if found else ""
    base = normalize_indicator_name(_MARKER_RE.sub("", text), strip_decorators=False)
    if not base:
        return (None, kind)
    for family, variants in _CELL_DIFF_BASES.items():
        for variant in variants:
            if base == normalize_indicator_name(variant, strip_decorators=False):
                return (family, kind)
    return (None, kind)


def _resolve_cell_diff_family(
    entries: List[_DictEntry], raw_name: str, unit: str
) -> Optional[_DictEntry]:
    """裸细胞名 + 单位 → 精确落到 PCT 或 CNT 指标。

    - 名字已显式写 %/#/百分比/计数 → 不做单位消歧（以名字为准，交给精确匹配）；
    - 裸名 + 可辨单位 → 返回对应族指标；
    - 裸名 + 单位缺失/未知 → 返回 None 且调用方**拒绝猜测**
      （AGENTS.md 第 8 条：宁可新建，不可错并）。
    """
    family, explicit = _cell_diff_key(raw_name)
    if not family or explicit:
        return None
    kind = _unit_kind(unit)
    if not kind:
        return None
    target = f"{family}_{'PCT' if kind == 'pct' else 'CNT'}"
    for entry in entries:
        if entry.code == target:
            return entry
    return None


def match_indicator(
    entries: List[_DictEntry], raw_name: str, unit: str = ""
) -> Optional[_DictEntry]:
    """两级精确匹配：primary（保留特征词）优先，relaxed（剥修饰词）次之。

    先 primary 可以保证「平均红细胞血红蛋白浓度」不会被并进 MCH，
    再 relaxed 兼容「总胆固醇测定」这类修饰词写法。

    ``unit`` 非空时用于**成对指标消歧**：白细胞五分类的裸名
    （如"中性粒细胞"）按单位落到 PCT（%）或 CNT（10^9/L）——这是
    防止"计数被错配成百分比"的关键安全阀（AGENTS.md 第 8 条）。
    """
    if not raw_name:
        return None
    family, explicit = _cell_diff_key(raw_name)
    if family and not explicit:
        # 裸五分类名：只接受按单位判定；判不出就拒绝匹配，等 AI 对齐兜底
        return _resolve_cell_diff_family(entries, raw_name, unit)

    def _pick(matches: List[_DictEntry]) -> Optional[_DictEntry]:
        """多个条目命中同一名字时，规范分族 code 优先（`_CNT` 胜过 `_ABS`）。"""
        if len(matches) <= 1:
            return matches[0] if matches else None
        canonical = [e for e in matches if _is_canonical_cell_diff(e)]
        return canonical[0] if canonical else matches[0]

    def _drop_specimen_conflicts(matches: List[_DictEntry]) -> List[_DictEntry]:
        """剔除与观测单位**不同标本体系**的候选（镜检 vs 血液定量）。"""
        return [e for e in matches if not _specimen_conflict(e, unit)]

    primary = _norm_with_kind(raw_name, strip_decorators=False)
    if primary:
        hits = _drop_specimen_conflicts([e for e in entries if primary in e.norm_set])
        if hits:
            return _pick(hits)
    relaxed = _norm_with_kind(raw_name)
    if not relaxed:
        return None
    hits = _drop_specimen_conflicts([e for e in entries if relaxed in e.relaxed_set])
    if hits:
        return _pick(hits)
    return None


def _is_confusable_pair(a: str, b: str) -> bool:
    """两个名字是否是"成对/系列指标"（前缀高度相似、必须靠后缀区分）。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb or na == nb:
        return False
    for left, right in _PAIR_TRAITS:
        la, ra = _norm(left), _norm(right)
        if {na, nb} <= {la, ra}:
            return True
    return False


def match_indicator_fuzzy(
    entries: List[_DictEntry], raw_name: str, unit: str = ""
) -> Optional[Tuple[_DictEntry, float]]:
    """模糊匹配：返回 (entry, score)，仅当分数 ≥ FUZZY_ACCEPT 且不是易混成对指标。

    ``unit`` 用于**标本类型安全阀**：镜检单位（`个/HP`）不得模糊匹配到
    血液定量指标（WBC/RBC，标准单位 `10^9/L`/`10^12/L`）。
    """
    if not raw_name:
        return None
    # 先按标本体系过滤词表，避免镜检项目模糊撞上血液指标
    pool = [e for e in entries if not _specimen_conflict(e, unit)]
    vocabulary = [(e.code, e.name_cn, e.unit) for e in pool]
    candidates = best_fuzzy_candidates(raw_name, vocabulary, limit=3)
    if not candidates:
        return None
    best = candidates[0]
    if best.score < FUZZY_ACCEPT:
        return None
    entry = next((e for e in pool if e.code == best.key), None)
    if entry is None:
        return None
    # 安全阀：候选与原文若属于成对/系列指标，宁可交给 AI 也不模糊合并
    if _is_confusable_pair(raw_name, entry.name_cn):
        return None
    # 次优候选与最优过于接近时说明歧义大，交给 AI
    if len(candidates) > 1 and candidates[0].score - candidates[1].score < 0.05:
        return None
    return entry, best.score


def _apply_unit_conversion(obs: dict, entry: _DictEntry) -> None:
    """把观测值/参考区间换算到字典标准单位；原始值保留在 raw_* 字段。"""
    target_unit = entry.unit
    if not target_unit:
        return
    value = obs.get("value")
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    source_unit = obs.get("unit")
    if not source_unit:
        # 报告没写单位：按标准单位理解（不换算、不覆盖）
        obs["unit"] = target_unit
        return

    if numeric:
        conv = convert_to_canonical(
            float(value), source_unit, code=entry.code, target_unit=target_unit
        )
        if conv.converted and conv.unit_canonical:
            new_value = round(conv.value, 6) if conv.value is not None else value
            # 跨维度换算的 factor 为 None，不能只用 changed 判断，需比较实际数值
            value_changed = abs(float(new_value) - float(value)) > 1e-9
            if value_changed:
                obs["raw_value"] = value
                obs["raw_unit"] = source_unit
                obs["unit_conversion"] = {
                    "from": source_unit,
                    "to": conv.unit_canonical,
                    "reason": conv.reason,
                }
            obs["value"] = new_value
            obs["unit"] = conv.unit_canonical
    else:
        # 文本值（阴性/未见明显异常）只统一单位写法
        canonical = convert_to_canonical(None, source_unit, code=entry.code,
                                        target_unit=target_unit)
        obs["unit"] = canonical.unit_canonical or source_unit

    # 参考区间同步换算（否则异常判定与趋势带会错）
    for low_key, high_key in (("ref_low", "ref_high"),):
        low, high = obs.get(low_key), obs.get(high_key)
        if low is None and high is None:
            continue
        if isinstance(low, (int, float)):
            conv_low = convert_to_canonical(
                float(low), source_unit, code=entry.code, target_unit=target_unit
            )
            if conv_low.converted and conv_low.value is not None:
                obs[low_key] = round(conv_low.value, 6)
        if isinstance(high, (int, float)):
            conv_high = convert_to_canonical(
                float(high), source_unit, code=entry.code, target_unit=target_unit
            )
            if conv_high.converted and conv_high.value is not None:
                obs[high_key] = round(conv_high.value, 6)


def _latest_ref_range(db: Session, dictionary_id: str) -> Optional[IndicatorRefRange]:
    return db.scalar(
        select(IndicatorRefRange)
        .where(IndicatorRefRange.dictionary_id == dictionary_id)
        .order_by(IndicatorRefRange.version.desc())
        .limit(1)
    )


def _compute_flag(value, ref_low, ref_high, existing: Optional[str]) -> Optional[str]:
    """异常标记：优先报告自带（H/L/A），否则按参考区间确定性计算（A-6 代码计算）。"""
    if existing in ("H", "L", "A", "N"):
        return existing
    if isinstance(value, (int, float)):
        if ref_high is not None and value > float(ref_high):
            return "H"
        if ref_low is not None and value < float(ref_low):
            return "L"
        if ref_high is not None or ref_low is not None:
            return "N"
    return None


def map_payload(
    db: Session,
    payload: dict,
    *,
    subject_id: str,
    document_id: Optional[str] = None,
    llm=None,
    doc_type: str = "",
    entries: Optional[List[_DictEntry]] = None,
    allow_ai: bool = True,
) -> dict:
    """observations 逐项对齐标准指标（精确 → 模糊 → AI → 原文保留）。

    AI 兜底由 `indicator_aligner` 执行：判定为已有指标则复用其 code/标准名/单位；
    判定为新指标则新建标准指标（含标准名与标准单位），后续报告直接复用。
    """
    catalog = entries if entries is not None else load_dictionary_ensured(db)
    misses = 0

    def _entries() -> List[_DictEntry]:
        return catalog

    for obs in payload.get("observations") or []:
        raw_name = str(obs.get("raw_name") or "")

        # 排版噪声（T1/年0/每3/页码）在匹配前就拦掉：
        # 否则会被历史脏别名"精确命中"成正式指标（实测：年0 → AGE）。
        from app.preprocessing.indicator_aligner import is_noise_text

        if is_noise_text(raw_name):
            obs["code"] = None
            obs["dictionary_id"] = None
            obs["display_name"] = raw_name
            obs["unmapped"] = True
            obs["align_rejected"] = {"reason": "排版噪声，不作为指标"}
            misses += 1
            db.add(
                DictionaryMiss(
                    id=new_id(),
                    subject_id=subject_id,
                    document_id=document_id,
                    raw_name=obs.get("raw_name"),
                    raw_value=str(obs.get("value")) if obs.get("value") is not None else None,
                    raw_unit=obs.get("unit"),
                    resolved=False,
                )
            )
            continue

        # 单位参与精确匹配：裸细胞名（"中性粒细胞"）靠单位落到 % 或 10^9/L 分族
        entry = match_indicator(_entries(), raw_name, unit=str(obs.get("unit") or ""))
        match_kind = "exact"

        if entry is None:
            fuzzy = match_indicator_fuzzy(
                _entries(), raw_name, unit=str(obs.get("unit") or "")
            )
            if fuzzy is not None:
                entry, score = fuzzy
                match_kind = "fuzzy"
                obs["match_score"] = score

        if entry is not None:
            obs["code"] = entry.code
            obs["dictionary_id"] = entry.id
            obs["display_name"] = entry.name_cn
            obs["unmapped"] = False
            obs["match_kind"] = match_kind
            # 参考区间快照：报告自带优先；否则取字典最新版本（§9.3 版本化）
            if obs.get("ref_low") is None and obs.get("ref_high") is None and not obs.get("ref_text"):
                rr = _latest_ref_range(db, entry.id)
                if rr is not None:
                    obs["ref_low"] = float(rr.low_value) if rr.low_value is not None else None
                    obs["ref_high"] = float(rr.high_value) if rr.high_value is not None else None
                    obs["ref_text"] = rr.text_range
            # 单位统一到标准单位（值 + 参考区间同步换算）
            _apply_unit_conversion(obs, entry)
        else:
            # 未命中：交给 AI 语义对齐（AI 默认可用；不可用则原文保留）
            aligned = False
            if allow_ai and llm is not None:
                aligned = _align_one_with_ai(
                    db,
                    llm,
                    obs,
                    catalog=catalog,
                    subject_id=subject_id,
                    document_id=document_id,
                    doc_type=doc_type,
                )
            if not aligned:
                # E-3 未命中：原文保留（unmapped 标记）+ 入反馈队列
                obs["code"] = None
                obs["dictionary_id"] = None
                obs["display_name"] = obs.get("raw_name")
                obs["unmapped"] = True
                db.add(
                    DictionaryMiss(
                        id=new_id(),
                        subject_id=subject_id,
                        document_id=document_id,
                        raw_name=obs.get("raw_name"),
                        raw_value=str(obs.get("value")) if obs.get("value") is not None else None,
                        raw_unit=obs.get("unit"),
                        resolved=False,
                    )
                )
                misses += 1

        obs["abnormal_flag"] = _compute_flag(
            _to_number(obs.get("value")),
            obs.get("ref_low"),
            obs.get("ref_high"),
            obs.get("abnormal_flag"),
        )

    if misses:
        logger.info("dictionary.miss doc=%s count=%s", document_id, misses)
    return payload


def _align_one_with_ai(
    db: Session,
    llm,
    obs: dict,
    *,
    catalog: List[_DictEntry],
    subject_id: str,
    document_id: Optional[str],
    doc_type: str,
) -> bool:
    """单个未命中指标交给 AI：命中复用 code，判新则新建标准指标。"""
    from app.preprocessing.indicator_aligner import (
        _create_standard_indicator,
        align_with_llm,
    )

    taken = {e.code for e in catalog}
    decision = align_with_llm(
        llm,
        raw_name=str(obs.get("raw_name") or ""),
        raw_unit=obs.get("unit"),
        raw_value=obs.get("value"),
        doc_type=doc_type,
        entries=catalog,
        taken_codes=taken,
    )
    if decision is None:
        return False

    # 安全阀：AI 若把成对/系列指标误判为同一项，拒绝该结论（宁可保留原文）
    if decision.action == "ai_match" and decision.code:
        target = next((e for e in catalog if e.code == decision.code), None)
        if target is not None and _is_confusable_pair(
            str(obs.get("raw_name") or ""), target.name_cn
        ):
            logger.warning(
                "align.rejected_pair raw=%s ai_code=%s", obs.get("raw_name"), decision.code
            )
            return False

    if decision.action == "ai_match" and decision.code:
        target = next((e for e in catalog if e.code == decision.code), None)
        obs["code"] = decision.code
        obs["dictionary_id"] = getattr(target, "id", None)
        obs["display_name"] = decision.display_name or getattr(target, "name_cn", None) or obs.get("raw_name")
        obs["unmapped"] = target is None
        obs["match_kind"] = "ai"
        obs["ai_alignment"] = decision.to_dict()
        if target is not None:
            _apply_unit_conversion(obs, target)
        elif decision.unit and not obs.get("unit"):
            obs["unit"] = decision.unit
        return True

    if decision.action == "ai_new" and decision.code:
        row = _create_standard_indicator(
            db,
            decision,
            raw_name=str(obs.get("raw_name") or ""),
            raw_unit=obs.get("unit"),
            raw_value=obs.get("value"),
        )
        if row is None:
            # 代码侧准入门槛拒绝（噪声/结论措辞/形态不符）：保留原文并留痕
            from app.preprocessing.indicator_aligner import is_plausible_indicator_name

            ok, reason = is_plausible_indicator_name(
                decision.display_name or str(obs.get("raw_name") or "")
            )
            obs["align_rejected"] = {
                "reason": reason or "未通过指标准入校验",
                "ai_code": decision.code,
                "ai_name": decision.display_name,
            }
            logger.info(
                "align.rejected doc=%s raw=%s reason=%s",
                document_id, obs.get("raw_name"), reason,
            )
            return False
        obs["code"] = row.code
        obs["dictionary_id"] = row.id
        obs["display_name"] = row.name_cn
        obs["unmapped"] = False
        obs["match_kind"] = "ai_new"
        obs["ai_alignment"] = decision.to_dict()
        catalog.append(_DictEntry(row))
        if row.unit:
            _apply_unit_conversion(
                obs,
                _DictEntry(row),
            )
        return True

    return False


def _to_number(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip())
        if m:
            try:
                return float(value)
            except ValueError:
                return None
    return None
