"""解析器注册表（§6.6 / §21）：PARSERS + 策略化选择。

**策略化（§21 新增）**：原实现固定「表格优先、VLM 兜底」。用户要求
「LLM 可用时优先视觉识别」，因此新增 ``select_parser_chain_by_strategy()``：
- ``vlm_first``（默认）：VLM 为主路径，表格/OCR 为兜底
- ``table_first``：保持原行为（可一键回滚）
- ``auto``：按文档特征选择（少页 PDF / 无文本层 / 无表格 → 视觉）

解析器能力：
- `table` —— PDF 表格直读（pdfplumber，毫秒级、零 token、边界确定）
- `vlm`   —— 逐页/拼图视觉识别（准确率高，消耗图像 token）
- `ocr`   —— 本地 OCR → 文本/文本模型结构化（扫描件兜底）
- `rule`  —— PDF 文本层正则（无 OCR 引擎时的成本底线）
- `mock`  —— 仅 LLM 未配置的离线演示
"""

from typing import List, Optional

from app.preprocessing.parser_base import BaseParser
from app.preprocessing.parsers.ocr_parser import OcrParser
from app.preprocessing.parsers.rule_parser import MockParser, RuleParser
from app.preprocessing.parsers.table_parser import TableParser
from app.preprocessing.parsers.vlm_parser import VlmParser
from app.preprocessing.strategy import (
    STRATEGY_VLM_FIRST as _STRATEGY_VLM_FIRST,
    DocProfile,
    resolve_primary,
)

PARSERS: dict[str, BaseParser] = {
    "table": TableParser(),   # PDF 表格直读（确定性、零 token）
    "vlm": VlmParser(),       # 视觉识别（准确率优先）
    "ocr": OcrParser(),       # 本地 OCR 兜底
    "rule": RuleParser(),     # 文本层正则兜底
    # 离线演示降级：available() 仅在 LLM 未配置时为 True，避免“AI 失败却给假草稿”
    "mock": MockParser(),
}

# 各策略下的解析器优先级（用于保序去重）
_ORDER_TABLE_FIRST = ("table", "vlm", "ocr", "rule", "mock")
_ORDER_VLM_FIRST = ("vlm", "table", "ocr", "rule", "mock")
_ORDER_PREFER_RULE = ("table", "ocr", "rule", "vlm", "mock")
# 兜底永远最后，避免掩盖真实解析器失败
_TERMINAL = ("mock",)


def select_parser_chain(doc_type: str = "", prefer_rule: bool = False) -> List[BaseParser]:
    """按序返回可用解析器（重试时优先确定性/本地链路，§6.6）。

    保留原签名以兼容既有调用与测试；策略化入口见 ``select_parser_chain_by_strategy``。
    """
    order = _ORDER_PREFER_RULE if prefer_rule else _ORDER_TABLE_FIRST
    return [PARSERS[name] for name in order if PARSERS[name].available()]


def select_parser(doc_type: str = "", prefer_rule: bool = False) -> BaseParser:
    """返回首选可用解析器。"""
    chain = select_parser_chain(doc_type, prefer_rule)
    if not chain:
        raise RuntimeError("无可用解析器")
    return chain[0]


def _ordered_names(
    strategy: str,
    profile: DocProfile,
    prefer_rule: bool,
    vlm_max_pages: int = 6,
) -> List[str]:
    """按策略给出解析器名的优先顺序（含兜底，未去重）。

    ``vlm_first`` 下即使 ``prefer_rule=True`` 也保持 VLM 优先（见
    ``select_parser_chain_by_strategy`` 的缺陷说明）；其它策略才允许
    ``prefer_rule`` 把确定性链路提前。
    """
    vlm_first = (strategy or "").strip().lower() == _STRATEGY_VLM_FIRST
    if prefer_rule and not (vlm_first and profile.vlm_available):
        # 重试场景：优先确定性/本地链路（原行为），不改变语义
        return list(_ORDER_PREFER_RULE)

    primary, fallbacks = resolve_primary(
        profile,
        strategy=strategy,
        vlm_max_pages=vlm_max_pages,
    )
    names = [primary, *fallbacks]
    if prefer_rule:
        # 仍想优先确定性：把 table/ocr/rule 提到 VLM 之后但保持 VLM 首要
        for name in _ORDER_PREFER_RULE:
            if name not in names:
                names.append(name)
    # 补齐策略默认顺序中缺失的项，保证仍有兜底
    base = _ORDER_VLM_FIRST if vlm_first else _ORDER_TABLE_FIRST
    for name in base:
        if name not in names:
            names.append(name)
    return names


def build_doc_profile(
    *,
    kind: str,
    page_count: int,
    has_text_layer: bool,
    table_count: int,
    doc_type: str = "",
) -> DocProfile:
    """从 pipeline 输入构造文档特征（解析器可用性自动探测）。"""
    return DocProfile(
        kind=kind,
        page_count=page_count,
        has_text_layer=has_text_layer,
        table_count=table_count,
        vlm_available=PARSERS["vlm"].available(),
        ocr_available=PARSERS["ocr"].available(),
    )


def select_parser_chain_by_strategy(
    profile: DocProfile,
    *,
    strategy: str = "vlm_first",
    prefer_rule: bool = False,
    vlm_max_pages: int = 6,
) -> List[BaseParser]:
    """策略化解析器链（§21 新增入口）。

    ``prefer_rule`` 表示「上次失败过，本次优先确定性/本地链路」，用于**避免
    反复撞同一个 LLM 故障**。但它**不能覆盖显式的 ``vlm_first`` 策略**：

    真实缺陷（2026-10-23）：`reextract` 会把 job.attempt 置为 1，于是
    ``prefer_rule=True`` → 链首变成 ``table``，VLM 被跳过，日志里
    ``strategy=vlm_first`` 却出现 ``primary=table``、最终 ``parser=ocr``。
    用户点"重新抽取"的预期是"换更好的方式重读"，结果反而降级成确定性解析，
    还把表格的行标/页码读成"指标"（OCR 串列 → ``unit=13``、``unit=71``）。

    因此：``vlm_first`` 下 VLM 可用时仍以 VLM 为首；``prefer_rule`` 只在
    VLM 不可用或其它策略下生效。``auto`` 策略同样尊重其特征判定。

    ``mock`` 始终排在**最后**：它不掩盖真实解析器的失败（前面都跑过才轮到它），
    但保证离线演示闭环仍可用（与改造前行为一致）。
    """
    names = _ordered_names(strategy, profile, prefer_rule, vlm_max_pages)
    chain: List[BaseParser] = []
    seen: set[str] = set()
    terminal: List[BaseParser] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        parser = PARSERS.get(name)
        if parser is None:
            continue
        if name in _TERMINAL:
            # 兜底解析器单独收集，最后追加
            if parser.available():
                terminal.append(parser)
            continue
        if parser.available():
            chain.append(parser)
    chain.extend(terminal)
    if not chain:
        mock = PARSERS.get("mock")
        if mock is not None and mock.available():
            chain.append(mock)
    return chain


def first_available_name(chain: List[BaseParser]) -> str:
    """取链首解析器名（用于日志与 job.parser 记录）。"""
    if not chain:
        return "none"
    return getattr(chain[0], "name", "unknown")
