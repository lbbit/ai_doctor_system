"""配置路由与 Agent 组件的接口测试。"""

import pytest
from fastapi.testclient import TestClient

from app.config import resolver
from app.llm import client as client_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    resolver.invalidate()
    client_mod.reset_client_cache()
    yield
    resolver.invalidate()
    client_mod.reset_client_cache()


class TestGetSettings:
    def test_returns_items_with_source(self, client: TestClient, auth: dict):
        r = client.get("/api/settings/system", headers=auth["headers"])
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["editable"] is True
        keys = {i["key"] for i in data["items"]}
        assert "llm.chat.model" in keys
        assert "llm.extraction.model" in keys
        assert "agent.enabled" in keys
        for item in data["items"]:
            assert item["source"] in ("db", "env", "default")

    def test_secret_is_masked_and_readonly(self, client: TestClient, auth: dict):
        r = client.get("/api/settings/system", headers=auth["headers"])
        items = {i["key"]: i for i in r.json()["data"]["items"]}
        key_item = items["llm.chat.api_key"]
        assert key_item["isSecret"] is True
        assert key_item["readOnly"] is True
        assert key_item["secretConfigured"] in (True, False)
        # 无论是否配置，都不得返回真实 key 前缀
        assert key_item["value"] in ("", "••••••••")

    def test_requires_auth(self, client: TestClient):
        r = client.get("/api/settings/system")
        assert r.status_code == 401


class TestPatchSettings:
    def test_update_takes_effect(self, client: TestClient, auth: dict):
        r = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "llm.chat.model", "value": "strong-v2"}]},
        )
        assert r.status_code == 200, r.text
        assert "llm.chat.model" in r.json()["data"]["updated"]
        got = client.get("/api/settings/system", headers=auth["headers"]).json()["data"]["items"]
        item = {i["key"]: i for i in got}["llm.chat.model"]
        assert item["value"] == "strong-v2"
        assert item["source"] == "db"

    def test_secret_write_rejected(self, client: TestClient, auth: dict):
        r = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "llm.chat.api_key", "value": "sk-should-fail"}]},
        )
        assert r.status_code == 422
        assert "环境变量" in r.json()["message"]

    def test_batch_failure_writes_nothing(self, client: TestClient, auth: dict):
        r = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={
                "items": [
                    {"key": "llm.chat.model", "value": "should-not-persist"},
                    {"key": "agent.max_results", "value": 999},
                ]
            },
        )
        assert r.status_code == 422
        items = client.get("/api/settings/system", headers=auth["headers"]).json()["data"]["items"]
        item = {i["key"]: i for i in items}["llm.chat.model"]
        assert item["value"] != "should-not-persist"

    def test_unknown_key_rejected(self, client: TestClient, auth: dict):
        r = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "hack.me", "value": 1}]},
        )
        assert r.status_code == 422

    def test_url_injection_rejected(self, client: TestClient, auth: dict):
        r = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={
                "items": [
                    {"key": "agent.searxng_base_url", "value": "javascript:alert(1)"}
                ]
            },
        )
        assert r.status_code == 422

    def test_update_is_audited(self, client: TestClient, auth: dict):
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 25}]},
        )
        logs = client.get(
            "/api/audit/logs", headers=auth["headers"], params={"action": "config.update"}
        ).json()["data"]
        assert logs["total"] >= 1

    def test_reset_restores_env_source(self, client: TestClient, auth: dict):
        client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 30}]},
        )
        r = client.post(
            "/api/settings/system/reset",
            headers=auth["headers"],
            json={"keys": ["chat.history_limit"]},
        )
        assert r.status_code == 200
        items = client.get("/api/settings/system", headers=auth["headers"]).json()["data"]["items"]
        item = {i["key"]: i for i in items}["chat.history_limit"]
        assert item["source"] != "db"


class TestTestEndpoint:
    def test_llm_without_key_reports_offline(self, client: TestClient, auth: dict, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "")
        resolver.invalidate()
        r = client.post(
            "/api/settings/system/test", headers=auth["headers"], json={"key": "llm.chat.model"}
        )
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["supported"] is True
        assert body["ok"] is False
        assert "Key" in body["detail"] or "降级" in body["detail"]

    def test_unknown_key_rejected(self, client: TestClient, auth: dict):
        r = client.post(
            "/api/settings/system/test", headers=auth["headers"], json={"key": "nope.nope"}
        )
        assert r.status_code == 422

    def test_non_testable_key_returns_unsupported(self, client: TestClient, auth: dict):
        r = client.post(
            "/api/settings/system/test", headers=auth["headers"], json={"key": "chat.history_limit"}
        )
        assert r.status_code == 200
        assert r.json()["data"]["supported"] is False
