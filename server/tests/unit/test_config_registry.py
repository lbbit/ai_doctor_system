"""配置项白名单与校验（registry）。"""

import pytest

from app.config.registry import (
    SPECS,
    ConfigValidationError,
    coerce_and_validate,
    get_spec,
    list_specs,
)


class TestRegistryShape:
    def test_specs_registered(self):
        assert len(SPECS) >= 15
        for key in ("llm.extraction.model", "llm.chat.model", "agent.enabled"):
            assert key in SPECS

    def test_group_filter(self):
        chat = list_specs("llm_chat")
        assert chat and all(s.group == "llm_chat" for s in chat)
        assert any(s.key == "llm.chat.model" for s in chat)

    def test_unknown_key_rejected(self):
        with pytest.raises(ConfigValidationError) as ei:
            get_spec("llm.evil.model")
        assert "未登记" in ei.value.reason

    def test_api_keys_are_env_only(self):
        """密钥类字段必须声明为 secret_env_only，且拒绝写库。"""
        for key in ("llm.extraction.api_key", "llm.chat.api_key"):
            spec = get_spec(key)
            assert spec.secret_env_only is True
            with pytest.raises(ConfigValidationError) as ei:
                coerce_and_validate(key, "sk-should-not-be-stored")
            assert "环境变量" in ei.value.reason


class TestCoercion:
    def test_bool_accepts_common_forms(self):
        for raw, expect in [
            (True, True),
            ("true", True),
            ("1", True),
            ("on", True),
            (False, False),
            ("false", False),
            ("0", False),
            ("off", False),
        ]:
            assert coerce_and_validate("agent.enabled", raw) is expect

    def test_bool_rejects_garbage(self):
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("agent.enabled", "maybe")

    def test_int_rejects_float_fraction(self):
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("agent.max_results", 3.5)

    def test_int_accepts_numeric_string(self):
        assert coerce_and_validate("agent.max_results", " 7 ") == 7

    def test_int_out_of_range(self):
        with pytest.raises(ConfigValidationError) as ei:
            coerce_and_validate("agent.max_results", 99)
        assert "不能大于" in ei.value.reason
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("agent.max_results", 0)

    def test_int_rejects_bool(self):
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("agent.max_results", True)

    def test_enum_choices(self):
        assert coerce_and_validate("agent.search_provider", "searxng") == "searxng"
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("agent.search_provider", "google")

    def test_url_scheme_restricted(self):
        """防注入：只允许 http/https，拦 javascript: 与 file:。"""
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("agent.searxng_base_url", "javascript:alert(1)")
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("agent.searxng_base_url", "file:///etc/passwd")
        assert coerce_and_validate("agent.searxng_base_url", "http://searxng:8080") == (
            "http://searxng:8080"
        )

    def test_url_empty_allowed(self):
        assert coerce_and_validate("agent.searxng_base_url", "") == ""

    def test_model_name_rejects_blank_and_newline(self):
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("llm.chat.model", "   ")
        with pytest.raises(ConfigValidationError):
            coerce_and_validate("llm.chat.model", "a\nb")

    def test_model_name_accepts_normal(self):
        assert coerce_and_validate("llm.chat.model", " deepseek-v4.1-flash ") == (
            "deepseek-v4.1-flash"
        )
