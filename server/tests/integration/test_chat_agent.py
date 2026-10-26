"""问诊 Agent 集成测试：启用 / 禁用 / 失败降级 三条路径。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.agent.search import FakeSearchProvider, SearchResult
from app.config import resolver


@pytest.fixture(autouse=True)
def _reset():
    resolver.invalidate()
    yield
    resolver.invalidate()


def _events(raw: str) -> list[tuple[str, dict]]:
    """解析 SSE 文本为 [(event, data)]。"""
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


def _ask(client: TestClient, auth: dict) -> str:
    conv = client.post(
        "/api/chat/conversations",
        headers=auth["headers"],
        json={"subjectId": auth["subjectId"]},
    ).json()["data"]
    return conv["id"]


def _stream(client: TestClient, auth: dict, conv_id: str, text: str) -> list:
    r = client.post(
        f"/api/chat/conversations/{conv_id}/messages",
        headers=auth["headers"],
        json={"content": text},
    )
    assert r.status_code == 200, r.text
    return _events(r.text)


class TestAgentDisabled:
    def test_no_agent_stage_when_disabled(
        self, client: TestClient, auth: dict, fake_llm
    ):
        """agent.enabled=false 时不应有任何工具轮阶段（planning/tool_*）。

        注意：thinking / generating 属于通用流式阶段（§19），
        与 Agent 开关无关，因此允许出现。
        """
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "agent.enabled", "value": False}]},
        )
        fake_llm("多喝水，注意休息。")
        conv = _ask(client, auth)
        events = _stream(client, auth, conv, "我最近有点头晕怎么办")
        names = [n for n, _ in events]
        stages = [d.get("stage") for n, d in events if n == "status"]
        assert "planning" not in stages
        assert "tool_running" not in stages
        assert "tool_done" not in stages
        assert "delta" in names
        assert "done" in names


class TestAgentEnabled:
    def test_status_event_emitted(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """启用 Agent 时应有 planning 阶段事件。"""
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
        fake_llm("建议复查。")
        conv = _ask(client, auth)
        events = _stream(client, auth, conv, "血常规白细胞偏高是什么意思")
        stages = [d.get("stage") for n, d in events if n == "status"]
        assert "planning" in stages

    def test_done_carries_agent_meta(self, client: TestClient, auth: dict, fake_llm):
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
        fake_llm("解答内容。")
        conv = _ask(client, auth)
        events = _stream(client, auth, conv, "血压高怎么办")
        done = next(d for n, d in events if n == "done")
        assert "agent" in done
        assert "usedTools" in done["agent"]
        assert "toolErrors" in done["agent"]

    def test_search_result_injected_into_prompt(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """搜索成功时，结果必须出现在送给模型的 system 消息里。"""
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "agent.enabled", "value": True}]},
        )
        fake = fake_llm(
            "根据检索资料，成人高血压诊断标准为 140/90 mmHg。",
            plan_json='{"need_search": true, "calls": [{"name": "web_search", "args": {"query": "高血压 诊断标准"}}]}',
        )
        provider = FakeSearchProvider(
            [
                SearchResult(
                    title="高血压诊断标准",
                    url="https://example.org/htn",
                    snippet="成人诊室血压 ≥140/90 mmHg 可诊断高血压。",
                )
            ]
        )
        import app.agent.tools as tools_mod

        monkeypatch.setattr(tools_mod, "build_provider", lambda db=None: provider)

        conv = _ask(client, auth)
        events = _stream(client, auth, conv, "成人高血压诊断标准是多少")
        done = next(d for n, d in events if n == "done")
        assert done["agent"]["usedTools"] == ["web_search"]
        # 最终生成调用的 system 消息应包含检索内容
        # （标题生成也会调用 LLM，需按提示词筛出主回答调用）
        main_calls = [
            c
            for c in fake.calls
            if "会话标题生成器" not in str(c[0].get("content", ""))
        ]
        assert main_calls
        system_text = main_calls[-1][0]["content"]
        assert "外部检索资料" in system_text
        assert "140/90" in system_text

    def test_clinical_skill_injected_even_without_search(
        self, client: TestClient, auth: dict, fake_llm
    ):
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
        fake = fake_llm(
            "解读如下。",
            plan_json='{"need_search": false, "calls": []}',
        )
        conv = _ask(client, auth)
        _stream(client, auth, conv, "帮我看看血常规里的白细胞偏高")
        # 排除标题生成调用，只看主回答的 system 消息
        main_calls = [
            c
            for c in fake.calls
            if "会话标题生成器" not in str(c[0].get("content", ""))
        ]
        system_text = main_calls[-1][0]["content"]
        assert "临床思维框架" in system_text


class TestAgentDegrade:
    def test_search_timeout_still_answers(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """搜索超时/失败必须降级：仍返回完整回答，并在 toolErrors 记录。"""
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "agent.enabled", "value": True}]},
        )
        fake_llm(
            "未获取到外部资料，基于档案回答：建议复查。",
            plan_json='{"need_search": true, "calls": [{"name": "web_search", "args": {"query": "血脂 标准"}}]}',
        )
        import app.agent.tools as tools_mod

        monkeypatch.setattr(
            tools_mod,
            "build_provider",
            lambda db=None: FakeSearchProvider(raises=True),
        )
        conv = _ask(client, auth)
        events = _stream(client, auth, conv, "血脂标准是多少")
        done = next(d for n, d in events if n == "done")
        assert done["agent"]["usedTools"] == []
        assert done["agent"]["toolErrors"]
        # 回答仍然产出
        text = "".join(d.get("content", "") for n, d in events if n == "delta")
        assert text.strip()

    def test_planner_bad_json_degrades(self, client: TestClient, auth: dict, fake_llm):
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "agent.enabled", "value": True}]},
        )
        fake_llm("回答内容。", plan_json="这不是 JSON")
        conv = _ask(client, auth)
        events = _stream(client, auth, conv, "血压偏高怎么处理")
        done = next(d for n, d in events if n == "done")
        assert done["agent"]["usedTools"] == []
        text = "".join(d.get("content", "") for n, d in events if n == "delta")
        assert text.strip()

    def test_redflag_still_first_when_agent_enabled(
        self, client: TestClient, auth: dict, fake_llm, db
    ):
        """红旗优先级不受 Agent 影响：仍有红卡片事件。"""
        from app.common.ids import new_id
        from app.db.models import RedFlagRule

        db.add(
            RedFlagRule(
                id=new_id(),
                code="RF_AGENT_CHEST",
                name="测试胸痛",
                keywords=["测试胸痛词"],
                keyword_logic="any",
                context_logic=None,
                severity="high",
                message="请立即就医测试",
                action="refer_now",
                enabled=True,
                version=1,
            )
        )
        db.commit()
        from app.redflag import loader

        loader.reload(force=True)

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
        fake_llm("请立即就医。")
        conv = _ask(client, auth)
        events = _stream(client, auth, conv, "我今天有测试胸痛词")
        names = [n for n, _ in events]
        assert "redflag" in names
        rf = next(d for n, d in events if n == "redflag")
        assert rf["severity"] == "high"
