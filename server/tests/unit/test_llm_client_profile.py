"""LLM 客户端分场景配置改造。

改造点：
- ``LLMClient`` 接受显式 profile（base_url/key/model/timeout），不再直读 settings。
- ``get_llm_client(scenario)``：``extraction``（成本优先）/ ``chat``（能力优先）。
- 兼容旧调用：``get_llm_client()`` 无参时使用 extraction，行为与改造前一致。

AA-1：抽取与问诊必须能分别配置模型与超时（用户诉求：识别量大且简单要控成本，
问诊要求能力强）。
"""

import pytest

from app.config import resolver
from app.config.registry import ConfigValidationError
from app.config.store import upsert_many
from app.llm import client as client_mod
from app.llm.client import LLMClient, get_llm_client


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    resolver.invalidate()
    client_mod.reset_client_cache()
    # 保证离线：默认无 key
    monkeypatch.setenv("LLM_API_KEY", "")
    resolver.invalidate()
    yield
    resolver.invalidate()
    client_mod.reset_client_cache()


class TestProfileWiring:
    def test_extraction_uses_extraction_profile(self, db, monkeypatch):
        upsert_many(
            db,
            [
                {"key": "llm.extraction.model", "value": "cheap-flash"},
                {"key": "llm.extraction.timeout_seconds", "value": 30},
            ],
            account_id=None,
        )
        db.commit()
        resolver.invalidate()
        c = get_llm_client("extraction", db=db)
        assert c.model == "cheap-flash"
        assert c.timeout == 30

    def test_chat_uses_chat_profile(self, db):
        upsert_many(
            db,
            [
                {"key": "llm.chat.model", "value": "strong-model"},
                {"key": "llm.chat.timeout_seconds", "value": 120},
            ],
            account_id=None,
        )
        db.commit()
        resolver.invalidate()
        c = get_llm_client("chat", db=db)
        assert c.model == "strong-model"
        assert c.timeout == 120

    def test_two_scenarios_differ(self, db):
        upsert_many(
            db,
            [
                {"key": "llm.extraction.model", "value": "cheap-flash"},
                {"key": "llm.chat.model", "value": "strong-model"},
            ],
            account_id=None,
        )
        db.commit()
        resolver.invalidate()
        ext = get_llm_client("extraction", db=db)
        chat = get_llm_client("chat", db=db)
        assert ext.model != chat.model

    def test_default_scenario_is_extraction(self, db):
        resolver.invalidate()
        c = get_llm_client(db=db)
        assert c.scenario == "extraction"

    def test_falls_back_on_unknown_scenario(self, db):
        resolver.invalidate()
        c = get_llm_client("banana", db=db)
        assert c.scenario == "extraction"


class TestAvailability:
    def test_unavailable_without_key(self, db):
        resolver.invalidate()
        c = get_llm_client("chat", db=db)
        assert c.available() is False

    def test_available_with_env_key(self, db, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
        resolver.invalidate()
        c = get_llm_client("chat", db=db)
        assert c.available() is True


class TestSecretNeverFromDb:
    def test_api_key_cannot_be_stored(self, db):
        with pytest.raises(ConfigValidationError):
            upsert_many(
                db, [{"key": "llm.chat.api_key", "value": "sk-nope"}], account_id=None
            )
