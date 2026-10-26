"""Agent 搜索提供方 + 工具执行 + 规划器测试。"""

import pytest

from app.agent.planner import parse_plan
from app.agent.search import (
    FakeSearchProvider,
    SearchResult,
    WikipediaProvider,
    is_safe_url,
    strip_html,
    unwrap_ddg_redirect,
)
from app.agent.skills import list_skills, select_skills
from app.agent.tools import ToolCall, execute_tools


class TestSafeUrl:
    @pytest.mark.parametrize(
        "url,ok",
        [
            ("https://example.com/a", True),
            ("http://example.com", True),
            ("javascript:alert(1)", False),
            ("data:text/html,<script>", False),
            ("file:///etc/passwd", False),
            ("https://user:pass@evil.com", False),
            ("", False),
            ("not-a-url", False),
        ],
    )
    def test_is_safe_url(self, url, ok):
        assert is_safe_url(url) is ok

    def test_unwrap_ddg_redirect(self):
        raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=x"
        assert unwrap_ddg_redirect(raw) == "https://example.com/page"

    def test_unwrap_passthrough(self):
        assert unwrap_ddg_redirect("https://example.com") == "https://example.com"


class TestStripHtml:
    def test_removes_tags_and_entities(self):
        assert strip_html("<b>血压</b>&nbsp;偏高") == "血压 偏高"

    def test_truncates(self):
        assert len(strip_html("x" * 2000)) <= 600


class TestWikipediaProvider:
    """免费默认提供方解析逻辑（离线，不发真实请求）。"""

    def test_parses_hits(self, monkeypatch):
        payload = {
            "query": {
                "search": [
                    {"title": "高脂血症", "snippet": "<b>血脂</b>异常升高"},
                    {"title": "他汀类药物", "snippet": "降脂药"},
                ]
            }
        }
        import json

        class FakeResp:
            def read(self):
                return json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: FakeResp()
        )
        results = WikipediaProvider().search("高脂血症", max_results=5)
        assert len(results) == 2
        assert results[0].title == "高脂血症"
        assert results[0].url.startswith("https://zh.wikipedia.org/wiki/")
        assert "血脂" in results[0].snippet

    def test_network_failure_degrades(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("network unreachable")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert WikipediaProvider(timeout_seconds=2).search("x") == []

    def test_skips_empty_title(self, monkeypatch):
        import json

        payload = {"query": {"search": [{"title": "", "snippet": "x"}]}}

        class FakeResp:
            def read(self):
                return json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
        assert WikipediaProvider().search("x") == []


class TestClinicalSkills:
    def test_lab_question_selects_lab_skill(self):
        skills = select_skills("我这次血常规的白细胞偏高是什么意思")
        assert skills
        assert skills[0].id == "lab_interpretation"

    def test_symptom_question_selects_triage(self):
        skills = select_skills("最近老是胸闷气短，有点心慌")
        assert skills
        assert skills[0].id == "symptom_triage"

    def test_medication_question(self):
        skills = select_skills("这个降压药有副作用吗，能不能停药")
        assert skills
        assert skills[0].id == "medication_safety"

    def test_no_match_returns_empty(self):
        assert select_skills("你好") == []

    def test_limit_respected(self):
        skills = select_skills("血常规指标偏高，有副作用吗，需要复查随访", limit=1)
        assert len(skills) == 1

    def test_all_skills_have_guidance(self):
        skills = list_skills()
        assert len(skills) >= 5
        for s in skills:
            assert s.guidance.strip()
            assert s.triggers


class TestPlannerParse:
    def test_no_search(self):
        p = parse_plan('{"need_search": false, "calls": []}')
        assert p.need_search is False
        assert p.empty

    def test_valid_search(self):
        p = parse_plan(
            '{"need_search": true, "calls": [{"name": "web_search", "args": {"query": "高血压 诊断标准"}}]}'
        )
        assert p.need_search is True
        assert p.calls[0].name == "web_search"
        assert "高血压" in p.calls[0].args["query"]

    def test_bad_json_degrades(self):
        p = parse_plan("模型胡说八道，没有 JSON")
        assert p.need_search is False
        assert p.degraded is True

    def test_rejects_unknown_tool(self):
        p = parse_plan(
            '{"need_search": true, "calls": [{"name": "delete_database", "args": {"query": "x"}}]}'
        )
        assert p.calls == []

    def test_rejects_empty_query(self):
        p = parse_plan(
            '{"need_search": true, "calls": [{"name": "web_search", "args": {"query": "  "}}]}'
        )
        assert p.calls == []

    def test_caps_call_count(self):
        calls = ",".join(
            f'{{"name": "web_search", "args": {{"query": "q{i}"}}}}' for i in range(5)
        )
        p = parse_plan('{"need_search": true, "calls": [' + calls + "]}", max_calls=2)
        assert len(p.calls) == 2

    def test_strips_code_fence(self):
        p = parse_plan('```json\n{"need_search": false, "calls": []}\n```')
        assert p.need_search is False
        assert p.degraded is False


class TestExecuteTools:
    def test_search_success(self):
        provider = FakeSearchProvider(
            [SearchResult(title="T", url="https://e.com", snippet="S")]
        )
        bundle = execute_tools(
            [ToolCall(name="web_search", args={"query": "高血压"})],
            query="高血压",
            provider=provider,
        )
        assert bundle.used_tools == ["web_search"]
        assert "T" in bundle.render_for_prompt()
        assert provider.calls == ["高血压"]

    def test_search_failure_isolated(self):
        provider = FakeSearchProvider(raises=True)
        bundle = execute_tools(
            [ToolCall(name="web_search", args={"query": "x"})],
            query="x",
            provider=provider,
        )
        assert bundle.used_tools == []
        assert bundle.tool_errors
        # 失败不抛异常，prompt 仍可生成（只是没有外部资料）
        assert bundle.render_for_prompt() is not None

    def test_no_results(self):
        bundle = execute_tools(
            [ToolCall(name="web_search", args={"query": "x"})],
            query="x",
            provider=FakeSearchProvider([]),
        )
        assert bundle.used_tools == []

    def test_unknown_tool_rejected(self):
        bundle = execute_tools(
            [ToolCall(name="rm_rf", args={"query": "x"})],
            query="x",
            provider=FakeSearchProvider([]),
        )
        assert bundle.used_tools == []

    def test_max_calls_enforced(self):
        provider = FakeSearchProvider([SearchResult(title="T", url="https://e.com")])
        calls = [ToolCall(name="web_search", args={"query": f"q{i}"}) for i in range(5)]
        bundle = execute_tools(calls, query="q", provider=provider, max_calls=2)
        assert len(provider.calls) == 2

    def test_sanitizer_applied_to_snippet(self):
        provider = FakeSearchProvider(
            [SearchResult(title="T", url="https://e.com", snippet="张三 13800001111")]
        )
        bundle = execute_tools(
            [ToolCall(name="web_search", args={"query": "x"})],
            query="x",
            provider=provider,
            sanitizer=lambda s: s.replace("13800001111", "[PHONE]"),
        )
        assert "13800001111" not in bundle.render_for_prompt()
        assert "[PHONE]" in bundle.render_for_prompt()

    def test_empty_calls_still_gets_skills(self):
        bundle = execute_tools([], query="血常规 白细胞 偏高")
        assert bundle.used_tools == []
        assert bundle.skills
        assert "临床思维框架" in bundle.render_for_prompt()

    def test_prompt_truncated(self):
        long_snippet = "字" * 5000
        provider = FakeSearchProvider(
            [SearchResult(title="T", url="https://e.com", snippet=long_snippet)]
        )
        bundle = execute_tools(
            [ToolCall(name="web_search", args={"query": "x"})],
            query="x",
            provider=provider,
        )
        assert len(bundle.render_for_prompt()) <= 3000

    def test_query_sanitized_against_injection(self):
        provider = FakeSearchProvider([SearchResult(title="T", url="https://e.com")])
        execute_tools(
            [ToolCall(name="web_search", args={"query": "ignore previous 高血压"})],
            query="q",
            provider=provider,
        )
        assert "ignore previous" not in provider.calls[0]
        assert "高血压" in provider.calls[0]
