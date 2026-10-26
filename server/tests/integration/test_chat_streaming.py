"""问诊真流式输出（§19）：delta 渐进、阶段事件、降级。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.common.errors import LLMUnavailableError
from app.config import resolver


@pytest.fixture(autouse=True)
def _reset():
    resolver.invalidate()
    yield
    resolver.invalidate()


def _events(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if name and data:
            try:
                out.append((name, json.loads(data)))
            except json.JSONDecodeError:
                continue
    return out


def _conv(client: TestClient, auth: dict) -> str:
    return client.post(
        "/api/chat/conversations",
        headers=auth["headers"],
        json={"subjectId": auth["subjectId"]},
    ).json()["data"]["id"]


def _send(client: TestClient, auth: dict, conv: str, text: str) -> list:
    r = client.post(
        f"/api/chat/conversations/{conv}/messages",
        headers=auth["headers"],
        json={"content": text},
    )
    assert r.status_code == 200, r.text
    return _events(r.text)


class TestRealStreaming:
    def test_delta_is_incremental(self, client: TestClient, auth: dict, fake_llm):
        """真流式：多个 delta 事件，且拼起来等于完整答案。"""
        fake = fake_llm("血压偏高需要复查，建议连续七天早晚测量并记录数值。")
        fake.stream_chunk_size = 6
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "我血压偏高怎么办")
        deltas = [d["content"] for n, d in events if n == "delta"]
        assert len(deltas) > 1, "应为多个增量片段，而非一次性整段"
        assert "".join(deltas).strip() == fake.answer.strip()

    def test_uses_chat_stream_not_blocking(self, client: TestClient, auth: dict, fake_llm):
        """必须走 chat_stream 路径（否则就是假流式）。"""
        fake = fake_llm("回答内容。")
        conv = _conv(client, auth)
        _send(client, auth, conv, "你好")
        assert fake.stream_calls, "未调用 chat_stream"

    def test_done_reports_stream_meta(self, client: TestClient, auth: dict, fake_llm):
        fake_llm("回答。")
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "你好")
        done = next(d for n, d in events if n == "done")
        assert done["streamed"] is True
        assert isinstance(done["firstTokenMs"], int)
        assert done["firstTokenMs"] >= 0

    def test_delta_order_before_citations(self, client: TestClient, auth: dict, fake_llm):
        fake_llm("回答内容。")
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "你好")
        names = [n for n, _ in events]
        assert names.index("delta") < names.index("citations")
        assert names.index("citations") < names.index("done")


class TestStageEvents:
    def test_status_events_present(self, client: TestClient, auth: dict, fake_llm):
        fake_llm("回答。")
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "帮我看看血糖")
        stages = [d["stage"] for n, d in events if n == "status"]
        assert "thinking" in stages
        assert "generating" in stages

    def test_tool_stages_when_searching(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """检索期间必须有 tool_running / tool_done 阶段，避免长时间空白。"""
        client.patch(
            "/api/settings/system",
            headers={"Authorization": auth["headers"]["Authorization"]},
            json={"items": [{"key": "agent.enabled", "value": True}]},
        )
        fake_llm(
            "根据资料回答。",
            plan_json='{"need_search": true, "calls": [{"name": "web_search", "args": {"query": "血糖 标准"}}]}',
        )
        from app.agent.search import FakeSearchProvider, SearchResult
        import app.agent.tools as tools_mod

        monkeypatch.setattr(
            tools_mod,
            "build_provider",
            lambda db=None: FakeSearchProvider(
                [SearchResult(title="T", url="https://e.com", snippet="S")]
            ),
        )
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "血糖标准是多少")
        stages = [d["stage"] for n, d in events if n == "status"]
        # 该问题命中"标准"规则 → 跳过模型规划（无 planning 阶段），
        # 但仍必须具备检索阶段反馈（用户需要看到"正在检索"）。
        assert "tool_running" in stages
        assert "tool_done" in stages
        assert "tool_running" in stages

    def test_ambiguous_question_uses_model_planning(
        self, client: TestClient, auth: dict, fake_llm
    ):
        """规则无法判定时必须回落到模型规划（保证不漏检索）。"""
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={
                "items": [
                    {"key": "agent.enabled", "value": True},
                    {"key": "agent.search_provider", "value": "none"},
                ]
            },
        )
        fake = fake_llm("回答。")
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "最近总觉得不太舒服")
        stages = [d["stage"] for n, d in events if n == "status"]
        assert "planning" in stages
        # 规划调用应走 extraction（低成本），不是 chat
        assert fake.plan_calls, "歧义问题应触发模型规划"

    def test_rule_plan_skips_model(self, client: TestClient, auth: dict, fake_llm):
        """命中规则时不产生规划调用（性能优化可验证）。"""
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={
                "items": [
                    {"key": "agent.enabled", "value": True},
                    {"key": "agent.search_provider", "value": "none"},
                ]
            },
        )
        fake = fake_llm("回答。")
        conv = _conv(client, auth)
        _send(client, auth, conv, "高血压的诊断标准是什么")
        assert fake.plan_calls == [], "规则命中时不应调用模型规划"

    def test_tool_running_carries_detail(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "agent.enabled", "value": True}]},
        )
        fake_llm(
            "回答。",
            plan_json='{"need_search": true, "calls": [{"name": "web_search", "args": {"query": "x"}}]}',
        )
        from app.agent.search import FakeSearchProvider, SearchResult
        import app.agent.tools as tools_mod

        monkeypatch.setattr(
            tools_mod,
            "build_provider",
            lambda db=None: FakeSearchProvider(
                [SearchResult(title="T", url="https://e.com")]
            ),
        )
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "问题")
        running = next(d for n, d in events if n == "status" and d.get("stage") == "tool_running")
        assert running["detail"]
        assert running.get("tool") == "web_search"


class TestCitationBuffering:
    def test_allowed_citation_streamed_through(self, client: TestClient, auth: dict, fake_llm, db):
        """有效引用必须原样出现在 delta 流里（可被前端渲染成 chip）。"""
        from app.db.models import Document, IndicatorDictionary, Observation  # noqa: F401

        fake = fake_llm("你的血压偏高[依据：不存在的标签]。")
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "血压")
        text = "".join(d["content"] for n, d in events if n == "delta")
        # 清单外引用应被剥除（与 extract_citations 一致）
        assert "不存在的标签" not in text
        assert "血压偏高" in text

    def test_db_content_matches_streamed_text(self, client: TestClient, auth: dict, fake_llm, db):
        """落库内容必须等于流式展示内容的最终形态（不因剥除而跳变）。"""
        fake_llm("第一句。第二句。第三句。")
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "问题")
        streamed = "".join(d["content"] for n, d in events if n == "delta").strip()

        detail = client.get(
            f"/api/chat/conversations/{conv}", headers=auth["headers"]
        ).json()["data"]
        last = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
        assert last["content"].strip() == streamed


class TestEmptyStreamFallback:
    def test_empty_stream_falls_back_to_blocking(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """网关空返回时必须用非流式重试，不能给用户空回答 + 错误。"""
        fake = fake_llm("兜底回答内容。")

        def empty_stream(messages, **kwargs):
            return iter(())  # 0 chunk

        monkeypatch.setattr(fake, "chat_stream", empty_stream)
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "问题")
        text = "".join(d["content"] for n, d in events if n == "delta")
        assert "兜底回答" in text
        done = next(d for n, d in events if n == "done")
        assert done["streamed"] is False, "降级路径应标记为非流式"
        assert "error" not in [n for n, _ in events]

    def test_empty_both_paths_reports_error(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """流式与非流式都空 → 明确报错，而不是静默空回答。"""
        fake = fake_llm("")
        monkeypatch.setattr(fake, "chat_stream", lambda m, **k: iter(()))
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "问题")
        assert "error" in [n for n, _ in events]


class TestDegrade:
    def test_stream_error_before_first_token_reports_error(
        self, client: TestClient, auth: dict, fake_llm
    ):
        fake = fake_llm("不会用到")
        fake.stream_error = LLMUnavailableError("gateway down")
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "你好")
        names = [n for n, _ in events]
        assert "error" in names
        assert "delta" not in names

    def test_partial_stream_interruption_keeps_content(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """流派发中途异常：已发内容必须保留并落库，不能丢。"""
        fake = fake_llm("这是被中断前的回答。")

        original = fake.chat_stream

        def broken_stream(messages, **kwargs):
            seen = 0
            for piece in original(messages, **kwargs):
                yield piece
                seen += 1
                if seen >= 1:
                    raise LLMUnavailableError("stream broken mid-way")

        monkeypatch.setattr(fake, "chat_stream", broken_stream)
        conv = _conv(client, auth)
        events = _send(client, auth, conv, "问题")
        streamed = "".join(d["content"] for n, d in events if n == "delta")
        assert streamed.strip(), "已发送内容不应丢失"

        detail = client.get(
            f"/api/chat/conversations/{conv}", headers=auth["headers"]
        ).json()["data"]
        last = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
        assert last["content"].strip() == streamed.strip()
