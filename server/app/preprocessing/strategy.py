"""解析策略决策（§21）：按文档特征选择主解析器，纯函数无 IO。

背景：原实现固定「表格优先、VLM 兜底」，对扫描件/拍照件不可靠。
用户要求「LLM 可用时优先视觉识别」。因此改为**策略化**：

- ``vlm_first``（默认）：VLM 可用时始终为主路径，表格退为失败兜底
- ``table_first``：保持原有行为（可一键回滚）
- ``auto``：按文档特征判定（少页 PDF / 无文本层 / 无表格 → 视觉）

设计约束（AGENTS.md 第 3 条）：本模块只做**确定性判断**，不调用模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STRATEGY_VLM_FIRST = "vlm_first"
STRATEGY_TABLE_FIRST = "table_first"
STRATEGY_AUTO = "auto"
VALID_STRATEGIES = (STRATEGY_VLM_FIRST, STRATEGY_TABLE_FIRST, STRATEGY_AUTO)

# 主解析器名（与 parsers._ORDER 对齐）
PARSER_VLM = "vlm"
PARSER_TABLE = "table"
PARSER_OCR = "ocr"


@dataclass(frozen=True)
class DocProfile:
    """决策所需的文档特征（全部可从现有 pipeline 输入获得）。"""

    kind: str = "PDF"            # PDF | IMAGE
    page_count: int = 1
    has_text_layer: bool = True
    table_count: int = 0
    vlm_available: bool = False
    ocr_available: bool = False


def choose_strategy(
    profile: DocProfile,
    *,
    strategy: str = STRATEGY_VLM_FIRST,
    vlm_max_pages: int = 6,
) -> str:
    """返回首选解析器名（``vlm`` / ``table`` / ``ocr``）。"""
    strategy = (strategy or STRATEGY_VLM_FIRST).strip().lower()
    if strategy not in VALID_STRATEGIES:
        # 配置写错时按最保守（不改变既有行为）处理
        strategy = STRATEGY_TABLE_FIRST

    if strategy == STRATEGY_TABLE_FIRST:
        return _table_first(profile)
    if strategy == STRATEGY_VLM_FIRST:
        return _vlm_first(profile, vlm_max_pages=vlm_max_pages)
    return _auto(profile, vlm_max_pages=vlm_max_pages)


def _vlm_first(profile: DocProfile, *, vlm_max_pages: int) -> str:
    """视觉优先：VLM 可用即走视觉；不可用才退回表格/OCR。"""
    if profile.vlm_available:
        return PARSER_VLM
    return _fallback_without_vlm(profile)


def _table_first(profile: DocProfile) -> str:
    """表格优先（原有行为）：图片类无表格时直接走 OCR/视觉。"""
    if profile.kind.upper() == "IMAGE":
        # 图片没有表格结构，原实现也是靠 VLM/OCR
        return PARSER_VLM if profile.vlm_available else (
            PARSER_OCR if profile.ocr_available else PARSER_TABLE
        )
    return PARSER_TABLE


def _auto(profile: DocProfile, *, vlm_max_pages: int) -> str:
    """按特征判定：少页 / 无文本层 / 无表格 → 视觉。

    理由：
    - 少页 PDF 走视觉成本可控，且准确率高于表格解析
    - 无文本层说明是扫描件，表格解析必然失败
    - 无表格结构时表格解析也抽不到东西
    """
    if not profile.vlm_available:
        return _fallback_without_vlm(profile)

    if profile.kind.upper() == "IMAGE":
        return PARSER_VLM
    if not profile.has_text_layer:
        return PARSER_VLM
    if profile.table_count == 0:
        return PARSER_VLM
    if 0 < profile.page_count <= max(1, vlm_max_pages):
        return PARSER_VLM
    return PARSER_TABLE


def _fallback_without_vlm(profile: DocProfile) -> str:
    """VLM 不可用时的确定性回退：有文本层走表格，否则 OCR，最后表格兜底。"""
    if profile.kind.upper() == "PDF" and profile.has_text_layer:
        return PARSER_TABLE
    if profile.ocr_available:
        return PARSER_OCR
    return PARSER_TABLE


def fallback_chain(primary: str, profile: DocProfile) -> list[str]:
    """给定主解析器，返回其后的兜底顺序（用于 pipeline 串联）。"""
    if primary == PARSER_VLM:
        chain = [PARSER_TABLE]
        if profile.ocr_available:
            chain.append(PARSER_OCR)
        return chain
    if primary == PARSER_TABLE:
        chain = []
        if profile.vlm_available:
            chain.append(PARSER_VLM)
        if profile.ocr_available:
            chain.append(PARSER_OCR)
        return chain
    # OCR 为主
    chain = []
    if profile.vlm_available:
        chain.append(PARSER_VLM)
    return chain


def resolve_primary(
    profile: DocProfile,
    *,
    strategy: str = STRATEGY_VLM_FIRST,
    vlm_max_pages: int = 6,
) -> tuple[str, list[str]]:
    """便利入口：返回 (主解析器, 兜底链)。"""
    primary = choose_strategy(
        profile, strategy=strategy, vlm_max_pages=vlm_max_pages
    )
    return primary, fallback_chain(primary, profile)
