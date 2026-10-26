"""流式引用缓冲：不变量「已放行内容永不改动」。"""

import pytest

from app.chat.stream_utils import (
    MAX_CITE_BUFFER,
    CitationBuffer,
    iter_stream_chunks,
)


class TestBasicPassthrough:
    def test_plain_text_passes_through(self):
        buf = CitationBuffer(set())
        out = buf.feed("你的血压偏高")
        assert "".join(out) == "你的血压偏高"
        assert buf.pending == ""

    def test_empty_token(self):
        buf = CitationBuffer(set())
        assert buf.feed("") == []

    def test_multiple_feeds_concatenate(self):
        buf = CitationBuffer(None)
        chunks = []
        chunks += buf.feed("血压")
        chunks += buf.feed("偏高")
        assert "".join(chunks) == "血压偏高"


class TestCitationRetained:
    def test_allowed_label_kept(self):
        buf = CitationBuffer({"2021 化验单"})
        out = []
        for tok in ["[依据 1", "：2021 化验单", "]"]:
            out += buf.feed(tok)
        assert "".join(out) == "[依据 1：2021 化验单]"
        assert buf.stripped == []

    def test_full_marker_in_one_token(self):
        buf = CitationBuffer({"2021 化验单"})
        out = buf.feed("见[依据：2021 化验单]处")
        assert "".join(out) == "见[依据：2021 化验单]处"

    def test_label_without_number(self):
        buf = CitationBuffer({"报告A"})
        out = buf.feed("[依据：报告A]")
        assert "".join(out) == "[依据：报告A]"

    def test_halfwidth_colon(self):
        buf = CitationBuffer({"报告A"})
        out = buf.feed("[依据 1: 报告A]")
        assert "".join(out) == "[依据 1: 报告A]"

    def test_partial_match_fallback(self):
        """与 extract_citations 一致的包含匹配兜底。"""
        buf = CitationBuffer({"血常规"})
        out = buf.feed("[依据：血常规报告 2021]")
        assert "".join(out) == "[依据：血常规报告 2021]"


class TestCitationStripped:
    def test_unknown_label_stripped(self):
        buf = CitationBuffer({"2021 化验单"})
        out = []
        for tok in ["结论", "[依据：不存在的报告", "]"]:
            out += buf.feed(tok)
        assert "".join(out) == "结论"
        assert buf.stripped == ["不存在的报告"]

    def test_stripped_mid_stream_keeps_rest(self):
        buf = CitationBuffer({"A"})
        out = []
        for tok in ["前", "[依据：B", "]", "后"]:
            out += buf.feed(tok)
        assert "".join(out) == "前后"

    def test_never_modifies_emitted_content(self):
        """核心不变量：已放行的内容拼起来永不改变。"""
        buf = CitationBuffer({"A"})
        emitted = ""
        for tok in ["结", "论", "[", "依据", "：", "B", "]", "继续", "说明"]:
            for chunk in buf.feed(tok):
                emitted += chunk
                # 每次追加后，之前内容必须保持不变（这里用快照对比）
                assert emitted.startswith(emitted[: len(emitted)])
        assert "[依据：B]" not in emitted
        assert emitted.endswith("继续说明")


class TestNonCitationBrackets:
    def test_numeric_bracket_kept(self):
        buf = CitationBuffer(set())
        out = buf.feed("第[1]项")
        assert "".join(out) == "第[1]项"

    def test_plain_bracket_text_kept(self):
        buf = CitationBuffer(set())
        out = buf.feed("[备注]内容")
        assert "".join(out) == "[备注]内容"

    def test_nested_bracket_releases_head(self):
        """嵌套 '['：前一段按普通文本放行，后一段按引用规则判定。"""
        buf = CitationBuffer({"B"})
        out = buf.feed("[依据：A[依据：B]")
        joined = "".join(out)
        # 前一段未闭合 → 作为普通文本放行（保留原样）
        assert joined.startswith("[依据：A")
        # 后一段是合法且标签允许的引用 → 保留
        assert "[依据：B]" in joined
        assert buf.pending == ""

    def test_overlong_buffer_released_as_text(self):
        buf = CitationBuffer(set())
        long_text = "[" + "字" * (MAX_CITE_BUFFER + 20)
        out = buf.feed(long_text)
        joined = "".join(out)
        assert joined.startswith("[")
        assert buf.pending == ""
        assert buf.stripped == []


class TestFlush:
    def test_flush_unclosed_marker(self):
        buf = CitationBuffer(set())
        buf.feed("[依据：A")
        out = buf.flush()
        assert "".join(out) == "[依据：A"
        assert buf.pending == ""

    def test_flush_empty(self):
        buf = CitationBuffer(set())
        assert buf.flush() == []

    def test_flush_idempotent(self):
        buf = CitationBuffer(set())
        buf.feed("[未闭合")
        assert len(buf.flush()) == 1
        assert buf.flush() == []


class TestNoPoolMeansKeepAll:
    def test_none_pool_keeps_everything(self):
        """pool 为 None 时不剥除任何内容（安全默认，避免误删）。"""
        buf = CitationBuffer(None)
        out = []
        for tok in ["[依据：任意标签", "]"]:
            out += buf.feed(tok)
        assert "".join(out) == "[依据：任意标签]"
        assert buf.stripped == []


class TestIterHelper:
    def test_helper_returns_chunks_and_stripped(self):
        tokens = ["结论", "[依据：B", "]", "完成"]
        chunks, stripped = iter_stream_chunks(iter(tokens), {"A"})
        assert "".join(chunks) == "结论完成"
        assert stripped == ["B"]

    def test_helper_keeps_allowed(self):
        tokens = ["[依据：A", "]"]
        chunks, stripped = iter_stream_chunks(iter(tokens), {"A"})
        assert "".join(chunks) == "[依据：A]"
        assert stripped == []


class TestCharByChar:
    """最严苛场景：单字符流式（真实 LLM token 可能极碎）。"""

    @pytest.mark.parametrize(
        "text,pool,expect",
        [
            ("见[依据：A]处", {"A"}, "见[依据：A]处"),
            ("见[依据：B]处", {"A"}, "见处"),
            ("无引用", {"A"}, "无引用"),
            ("第[1]项", {"A"}, "第[1]项"),
        ],
    )
    def test_char_by_char(self, text, pool, expect):
        buf = CitationBuffer(pool)
        out = []
        for ch in text:
            out += buf.feed(ch)
        out += buf.flush()
        assert "".join(out) == expect
