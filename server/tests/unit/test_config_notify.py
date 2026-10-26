"""配置变更通知纯函数单测（不依赖 DB）。"""

import pytest

from app.config.notify import (
    HIGH_IMPACT_PREFIXES,
    LOW_IMPACT_PREFIXES,
    classify_keys,
    is_high_impact,
)


class TestHighImpact:
    @pytest.mark.parametrize(
        "keys",
        [
            ["llm.chat.model"],
            ["llm.extraction.vlm_model"],
            ["llm.chat.timeout_seconds"],
            ["agent.enabled"],
            ["agent.search_provider"],
            ["agent.searxng_base_url"],
        ],
    )
    def test_llm_and_agent_are_high_impact(self, keys):
        assert is_high_impact(keys) is True

    @pytest.mark.parametrize(
        "keys",
        [
            ["chat.history_limit"],
            ["upload.max_upload_mb"],
            ["privacy.pii_masking_enabled"],
            [],
        ],
    )
    def test_display_only_is_not_high_impact(self, keys):
        assert is_high_impact(keys) is False

    def test_mixed_list_is_high_impact(self):
        assert is_high_impact(["chat.history_limit", "llm.chat.model"]) is True

    def test_prefix_constants_cover_expected(self):
        assert "llm." in HIGH_IMPACT_PREFIXES
        assert "agent." in HIGH_IMPACT_PREFIXES
        assert "chat." in LOW_IMPACT_PREFIXES


class TestClassifyKeys:
    def test_filters_low_impact(self):
        assert classify_keys(["llm.chat.model", "chat.history_limit"]) == [
            "llm.chat.model"
        ]

    def test_all_low_impact_returns_empty(self):
        assert classify_keys(["upload.max_upload_mb", "privacy.pii_masking_enabled"]) == []

    def test_empty_input(self):
        assert classify_keys([]) == []

    def test_unknown_key_kept(self):
        """未知前缀保守保留（宁可多通知，不可漏掉高影响变更）。"""
        assert classify_keys(["future.new_setting"]) == ["future.new_setting"]
