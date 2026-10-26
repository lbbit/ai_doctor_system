"""会话标题 LLM 概述集成测试（§16）。"""

import json

import pytest
from fastapi.testclient import TestClient

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


def _new_conv(client: TestClient, auth: dict) -> str:
    return client.post(
        "/api/chat/conversations",
        headers=auth["headers"],
        json={"subjectId": auth["subjectId"]},
    ).json()["data"]["id"]


def _title_of(client: TestClient, auth: dict, conv_id: str) -> str:
    detail = client.get(
        f"/api/chat/conversations/{conv_id}", headers=auth["headers"]
    ).json()["data"]
    return detail["conversation"]["title"]


class TestTitleGeneration:
    def test_first_turn_generates_semantic_title(
        self, client: TestClient, auth: dict, fake_llm
    ):
        """首轮回答后标题应为 LLM 生成的短标题，而非截断的用户原句。"""
        fake = fake_llm("建议控制饮食并复查血脂。", plan_json='{"need_search": false, "calls": []}')
        # 让标题调用返回固定短标题：按提示词区分（标题提示词含“标题生成器”）
        original_answer = fake.answer

        def route(messages):
            first = str(messages[0].get("content", ""))
            if "会话标题生成器" in first:
                return "血脂异常咨询"
            return original_answer

        fake.answer = route
        conv = _new_conv(client, auth)
        raw = client.post(
            f"/api/chat/conversations/{conv}/messages",
            headers=auth["headers"],
            json={"content": "你好，我最近血脂有点高，应该注意什么？"},
        ).text
        assert "done" in [n for n, _ in _events(raw)]
        assert _title_of(client, auth, conv) == "血脂异常咨询"

    def test_title_not_overwritten_on_second_turn(
        self, client: TestClient, auth: dict, fake_llm
    ):
        """第二轮提问不得再次改写标题（幂等 + 成本控制）。"""
        fake = fake_llm("答复。", plan_json='{"need_search": false, "calls": []}')

        def route(messages):
            first = str(messages[0].get("content", ""))
            if "会话标题生成器" in first:
                return "血糖管理咨询"
            return "答复。"

        fake.answer = route
        conv = _new_conv(client, auth)
        client.post(
            f"/api/chat/conversations/{conv}/messages",
            headers=auth["headers"],
            json={"content": "我血糖偏高怎么办"},
        )
        first_title = _title_of(client, auth, conv)
        assert first_title == "血糖管理咨询"

        client.post(
            f"/api/chat/conversations/{conv}/messages",
            headers=auth["headers"],
            json={"content": "那饮食上具体怎么吃"},
        )
        assert _title_of(client, auth, conv) == first_title

    def test_llm_failure_keeps_fallback_title(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """标题生成失败时保留占位标题，问诊正常完成。"""
        fake = fake_llm("答复内容。", plan_json='{"need_search": false, "calls": []}')
        # orchestration 在模块顶层 import 了 generate_title，必须打到该本地引用上
        import app.chat.orchestration as orch_mod

        def boom(*a, **k):
            raise RuntimeError("title gateway down")

        monkeypatch.setattr(orch_mod, "generate_title", boom)
        conv = _new_conv(client, auth)
        raw = client.post(
            f"/api/chat/conversations/{conv}/messages",
            headers=auth["headers"],
            json={"content": "帮我看看最近的体检报告"},
        ).text
        names = [n for n, _ in _events(raw)]
        assert "done" in names
        # 降级为占位标题「新会话」        assert _title_of(client, auth, conv) == "新会话"

    def test_unusable_title_keeps_fallback(
        self, client: TestClient, auth: dict, fake_llm
    ):
        """模型输出无意义（纯标点/空洞词）时保留占位标题。"""
        fake = fake_llm("答复。", plan_json='{"need_search": false, "calls": []}')

        def route(messages):
            first = str(messages[0].get("content", ""))
            if "会话标题生成器" in first:
                return "会话"
            return "答复。"

        fake.answer = route
        conv = _new_conv(client, auth)
        client.post(
            f"/api/chat/conversations/{conv}/messages",
            headers=auth["headers"],
            json={"content": "血压 140/90 算高吗"},
        )
        assert _title_of(client, auth, conv) == "新会话"

    def test_llm_unavailable_keeps_fallback(
        self, client: TestClient, auth: dict, fake_llm, monkeypatch
    ):
        """标题模型不可用 → 保留占位标题，问诊不中断。"""
        fake_llm("答复。", plan_json='{"need_search": false, "calls": []}')
        import app.chat.orchestration as orch_mod

        class Unavailable:
            model = "none"

            def available(self):
                return False

            def chat(self, *a, **k):
                raise AssertionError("should not be called")

        # get_llm_client 在函数内部按 scenario 导入，需在导入源模块上替换
        import app.llm.client as client_mod
        import app.chat.orchestration  # noqa: F401  确保绑定已建立

        original = client_mod.get_llm_client

        def patched(scenario="extraction", db=None):
            if scenario == "extraction":
                return Unavailable()
            return original(scenario, db=db)

        monkeypatch.setattr(client_mod, "get_llm_client", patched)
        conv = _new_conv(client, auth)
        raw = client.post(
            f"/api/chat/conversations/{conv}/messages",
            headers=auth["headers"],
            json={"content": "孩子发烧 38.5 度怎么办"},
        ).text
        assert "done" in [n for n, _ in _events(raw)]
        assert _title_of(client, auth, conv) == "新会话"
