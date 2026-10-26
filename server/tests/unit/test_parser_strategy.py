"""解析策略决策矩阵（§21）：确定性、无 IO。"""

import pytest

from app.preprocessing.strategy import (
    PARSER_OCR,
    PARSER_TABLE,
    PARSER_VLM,
    STRATEGY_AUTO,
    STRATEGY_TABLE_FIRST,
    STRATEGY_VLM_FIRST,
    DocProfile,
    choose_strategy,
    fallback_chain,
    resolve_primary,
)


def pdf(pages=1, text=True, tables=0, vlm=True, ocr=True):
    return DocProfile(
        kind="PDF",
        page_count=pages,
        has_text_layer=text,
        table_count=tables,
        vlm_available=vlm,
        ocr_available=ocr,
    )


def img(vlm=True, ocr=True):
    return DocProfile(
        kind="IMAGE", page_count=1, has_text_layer=False, table_count=0,
        vlm_available=vlm, ocr_available=ocr,
    )


class TestVlmFirst:
    """默认策略：LLM 可用就优先视觉（用户明确要求）。"""

    def test_pdf_with_tables_still_uses_vlm(self):
        """关键回归：有表格的 PDF 也走视觉（原实现是表格优先）。"""
        assert choose_strategy(pdf(pages=20, tables=10), strategy=STRATEGY_VLM_FIRST) == PARSER_VLM

    def test_scanned_pdf_uses_vlm(self):
        assert choose_strategy(pdf(text=False), strategy=STRATEGY_VLM_FIRST) == PARSER_VLM

    def test_image_uses_vlm(self):
        assert choose_strategy(img(), strategy=STRATEGY_VLM_FIRST) == PARSER_VLM

    def test_without_vlm_falls_back_to_table(self):
        assert choose_strategy(pdf(vlm=False), strategy=STRATEGY_VLM_FIRST) == PARSER_TABLE

    def test_without_vlm_image_falls_back_to_ocr(self):
        assert choose_strategy(img(vlm=False), strategy=STRATEGY_VLM_FIRST) == PARSER_OCR

    def test_without_vlm_and_ocr_image_falls_back_to_table(self):
        assert choose_strategy(img(vlm=False, ocr=False), strategy=STRATEGY_VLM_FIRST) == PARSER_TABLE


class TestTableFirst:
    """回滚策略：保持原有行为。"""

    def test_pdf_uses_table(self):
        assert choose_strategy(pdf(pages=20, tables=10), strategy=STRATEGY_TABLE_FIRST) == PARSER_TABLE

    def test_image_uses_vlm_if_available(self):
        assert choose_strategy(img(), strategy=STRATEGY_TABLE_FIRST) == PARSER_VLM

    def test_image_without_vlm_uses_ocr(self):
        assert choose_strategy(img(vlm=False), strategy=STRATEGY_TABLE_FIRST) == PARSER_OCR


class TestAuto:
    def test_few_pages_uses_vlm(self):
        assert choose_strategy(pdf(pages=3, tables=5), strategy=STRATEGY_AUTO, vlm_max_pages=6) == PARSER_VLM

    def test_many_pages_with_tables_uses_table(self):
        assert (
            choose_strategy(pdf(pages=20, tables=10), strategy=STRATEGY_AUTO, vlm_max_pages=6)
            == PARSER_TABLE
        )

    def test_no_text_layer_uses_vlm(self):
        assert (
            choose_strategy(pdf(pages=20, text=False), strategy=STRATEGY_AUTO, vlm_max_pages=6)
            == PARSER_VLM
        )

    def test_no_tables_uses_vlm(self):
        assert (
            choose_strategy(pdf(pages=20, tables=0), strategy=STRATEGY_AUTO, vlm_max_pages=6)
            == PARSER_VLM
        )

    def test_threshold_boundary_inclusive(self):
        assert (
            choose_strategy(pdf(pages=6, tables=3), strategy=STRATEGY_AUTO, vlm_max_pages=6)
            == PARSER_VLM
        )
        assert (
            choose_strategy(pdf(pages=7, tables=3), strategy=STRATEGY_AUTO, vlm_max_pages=6)
            == PARSER_TABLE
        )

    def test_without_vlm_never_returns_vlm(self):
        for prof in (pdf(vlm=False), pdf(text=False, vlm=False), img(vlm=False)):
            assert choose_strategy(prof, strategy=STRATEGY_AUTO) != PARSER_VLM


class TestInvalidStrategy:
    def test_unknown_strategy_falls_back_to_table_first(self):
        """配置写错时不得意外启用昂贵路径。"""
        assert choose_strategy(pdf(tables=10), strategy="banana") == PARSER_TABLE

    def test_empty_strategy_uses_default(self):
        assert choose_strategy(pdf(), strategy="") == PARSER_VLM


class TestFallbackChain:
    def test_vlm_primary_falls_back_to_table_then_ocr(self):
        assert fallback_chain(PARSER_VLM, pdf(ocr=True)) == [PARSER_TABLE, PARSER_OCR]

    def test_vlm_primary_skips_ocr_when_unavailable(self):
        assert fallback_chain(PARSER_VLM, pdf(ocr=False)) == [PARSER_TABLE]

    def test_table_primary_falls_back_to_vlm_then_ocr(self):
        assert fallback_chain(PARSER_TABLE, pdf(vlm=True, ocr=True)) == [PARSER_VLM, PARSER_OCR]

    def test_table_primary_without_vlm(self):
        assert fallback_chain(PARSER_TABLE, pdf(vlm=False, ocr=True)) == [PARSER_OCR]

    def test_ocr_primary_falls_back_to_vlm(self):
        assert fallback_chain(PARSER_OCR, pdf(vlm=True)) == [PARSER_VLM]


class TestResolvePrimary:
    def test_returns_primary_and_chain(self):
        primary, chain = resolve_primary(pdf(), strategy=STRATEGY_VLM_FIRST)
        assert primary == PARSER_VLM
        assert PARSER_TABLE in chain

    def test_chain_never_contains_primary(self):
        for strat in (STRATEGY_VLM_FIRST, STRATEGY_TABLE_FIRST, STRATEGY_AUTO):
            primary, chain = resolve_primary(pdf(pages=3, tables=2), strategy=strat)
            assert primary not in chain


class TestPreferRuleDoesNotOverpowerVlmFirst:
    """重试降级不得覆盖显式 vlm_first 策略（2026-10-23 真实缺陷）。

    `reextract` 把 job.attempt 置为 1 → `prefer_rule=True` → 旧实现直接返回
    `_ORDER_PREFER_RULE`（table 打头），于是日志出现
    `strategy=vlm_first primary=table`，最终 `parser=ocr`：
    用户点"重新抽取"想换更好的方式重读，结果反而降级，OCR 还串列把
    `unit` 读成 `13`/`71`，并把页码读成"指标"。
    """

    @staticmethod
    def _force_available(monkeypatch, names):
        """强制指定解析器可用（测试环境无 LLM key / OCR 依赖）。"""
        import app.preprocessing.parsers as parsers_mod

        for key, parser in parsers_mod.PARSERS.items():
            monkeypatch.setattr(
                parser, "available", lambda k=key: k in names, raising=False
            )

    def test_vlm_first_with_prefer_rule_keeps_vlm_primary(self, monkeypatch):
        from app.preprocessing.parsers import select_parser_chain_by_strategy

        self._force_available(monkeypatch, {"table", "vlm", "ocr", "rule"})
        chain = select_parser_chain_by_strategy(
            pdf(pages=1, text=True, tables=0, vlm=True),
            strategy=STRATEGY_VLM_FIRST,
            prefer_rule=True,
        )
        assert chain, "链不应为空"
        assert chain[0].name == PARSER_VLM, [p.name for p in chain]

    def test_vlm_unavailable_prefer_rule_falls_back_deterministically(self, monkeypatch):
        from app.preprocessing.parsers import select_parser_chain_by_strategy

        self._force_available(monkeypatch, {"table", "ocr", "rule"})
        chain = select_parser_chain_by_strategy(
            pdf(pages=1, vlm=False, ocr=True),
            strategy=STRATEGY_VLM_FIRST,
            prefer_rule=True,
        )
        assert chain[0].name == PARSER_TABLE, [p.name for p in chain]

    def test_table_first_with_prefer_rule_unchanged(self, monkeypatch):
        from app.preprocessing.parsers import select_parser_chain_by_strategy

        self._force_available(monkeypatch, {"table", "vlm", "ocr", "rule"})
        chain = select_parser_chain_by_strategy(
            pdf(pages=1, vlm=True),
            strategy=STRATEGY_TABLE_FIRST,
            prefer_rule=True,
        )
        assert chain[0].name == PARSER_TABLE, [p.name for p in chain]