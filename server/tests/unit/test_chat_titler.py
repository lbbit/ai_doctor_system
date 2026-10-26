"""会话标题生成：清洗规则与失败降级。"""

import pytest

from app.chat.titler import MAX_TITLE_CHARS, clean_title, generate_title


class _FakeLLM:
    def __init__(self, response=None, available=True, raises=False):
        self.response = response
        self._available = available
        self.raises = raises
        self.calls = []

    def available(self):
        return self._available

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.raises:
            raise RuntimeError("gateway down")
        return self.response


class TestCleanTitle:
    @pytest.mark.parametrize(
        "raw,expect",
        [
            ("血脂异常咨询", "血脂异常咨询"),
            ("  血脂异常咨询  ", "血脂异常咨询"),
            ("标题：血脂异常咨询", "血脂异常咨询"),
            ("Title: 血脂异常咨询", "血脂异常咨询"),
            ('"血脂异常咨询"', "血脂异常咨询"),
            ("《血脂异常咨询》", "血脂异常咨询"),
            ("血脂异常咨询。", "血脂异常咨询"),
            ("血脂异常，咨询！", "血脂异常咨询"),
            ("血脂异常咨询\n这是解释说明", "血脂异常咨询"),
        ],
    )
    def test_cleaning(self, raw, expect):
        assert clean_title(raw) == expect

    def test_rejects_empty(self):
        assert clean_title("") is None
        assert clean_title("   ") is None
        assert clean_title(None) is None

    def test_rejects_punctuation_only(self):
        assert clean_title("。。。") is None
        assert clean_title('""') is None

    def test_rejects_empty_words(self):
        assert clean_title("咨询") is None
        assert clean_title("会话") is None

    def test_truncates_long(self):
        long_raw = "血脂" * 30
        result = clean_title(long_raw)
        assert result is not None
        assert len(result) <= MAX_TITLE_CHARS

    def test_rejects_too_short(self):
        assert clean_title("血") is None


class TestGenerateTitle:
    def test_normal(self):
        llm = _FakeLLM("血脂异常咨询")
        title = generate_title(llm, "我的血脂偏高要注意什么", "建议控制饮食……")
        assert title == "血脂异常咨询"
        assert len(llm.calls) == 1
        # 提示词应包含提问与回答摘要
        payload = llm.calls[0][1]["content"]
        assert "血脂偏高" in payload
        assert "控制饮食" in payload

    def test_llm_unavailable_returns_none(self):
        llm = _FakeLLM("血脂异常咨询", available=False)
        assert generate_title(llm, "血脂偏高", "建议") is None
        assert llm.calls == []

    def test_exception_degrades(self):
        llm = _FakeLLM(raises=True)
        assert generate_title(llm, "血脂偏高", "建议") is None

    def test_bad_output_degrades(self):
        assert generate_title(_FakeLLM(""), "血脂偏高", "建议") is None
        assert generate_title(_FakeLLM("会话"), "血脂偏高", "建议") is None

    def test_empty_question_returns_none(self):
        llm = _FakeLLM("标题")
        assert generate_title(llm, "   ", "答复") is None
        assert llm.calls == []

    def test_disabled(self):
        llm = _FakeLLM("血脂异常咨询")
        assert generate_title(llm, "血脂偏高", "建议", enabled=False) is None
        assert llm.calls == []

    def test_answer_truncated_in_prompt(self):
        llm = _FakeLLM("长答案咨询")
        generate_title(llm, "问", "答" * 2000)
        payload = llm.calls[0][1]["content"]
        # 回答只截取前 500 字，避免上下文膨胀
        assert len(payload) < 900
