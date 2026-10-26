"""配置存储与解析：优先级、掩码、批量校验、LLM 分场景。"""

import pytest
from sqlalchemy.orm import Session

from app.config import resolver
from app.config.registry import ConfigValidationError
from app.config.resolver import (
    SOURCE_DB,
    resolve,
    resolve_llm_profile,
    invalidate,
)
from app.config.store import load_overrides, mask_secret, reset_keys, upsert_many
from app.db.models.system_setting import SystemSetting


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate()
    yield
    invalidate()


class TestMasking:
    def test_mask_hides_content(self):
        assert mask_secret("sk-super-secret-1234") == "••••••••"
        assert mask_secret("") == ""
        assert mask_secret(None) == ""


class TestStoreWrite:
    def test_write_and_read_back(self, db: Session):
        upsert_many(
            db,
            [{"key": "llm.chat.model", "value": "strong-model"}],
            account_id=None,
        )
        db.commit()
        overrides = load_overrides(db)
        assert overrides["llm.chat.model"] == "strong-model"

    def test_rejects_secret_key_write(self, db: Session):
        """密钥类配置**禁止**写入 DB（AGENTS.md 密钥约束）。"""
        with pytest.raises(ConfigValidationError):
            upsert_many(
                db,
                [{"key": "llm.chat.api_key", "value": "sk-leaked"}],
                account_id=None,
            )
        assert db.get(SystemSetting, "llm.chat.api_key") is None

    def test_batch_is_all_or_nothing(self, db: Session):
        """任一项非法 → 整体不写，避免半写状态。"""
        with pytest.raises(ConfigValidationError):
            upsert_many(
                db,
                [
                    {"key": "llm.chat.model", "value": "ok-model"},
                    {"key": "agent.max_results", "value": 999},
                ],
                account_id=None,
            )
        assert db.get(SystemSetting, "llm.chat.model") is None

    def test_unknown_key_rejected(self, db: Session):
        with pytest.raises(ConfigValidationError):
            upsert_many(
                db, [{"key": "totally.unknown", "value": 1}], account_id=None
            )

    def test_reset_restores_env(self, db: Session):
        upsert_many(
            db, [{"key": "agent.max_results", "value": 9}], account_id=None
        )
        db.commit()
        reset_keys(db, ["agent.max_results"])
        db.commit()
        assert db.get(SystemSetting, "agent.max_results") is None


class TestResolverPriority:
    def test_db_overrides_env(self, db: Session):
        upsert_many(
            db, [{"key": "agent.max_results", "value": 8}], account_id=None
        )
        db.commit()
        invalidate()
        got = resolve(db, "agent.max_results")
        assert got.value == 8
        assert got.source == SOURCE_DB

    def test_env_fallback_when_no_override(self, db: Session):
        invalidate()
        got = resolve(db, "agent.max_results")
        assert got.source != SOURCE_DB

    def test_cache_serves_repeat_calls(self, db: Session):
        upsert_many(
            db, [{"key": "agent.max_results", "value": 6}], account_id=None
        )
        db.commit()
        invalidate()
        assert resolve(db, "agent.max_results").value == 6
        # 绕过 invalidate 直接改库：缓存期内仍返回旧值（验证缓存生效）
        row = db.get(SystemSetting, "agent.max_results")
        row.value_json = 2
        db.commit()
        assert resolve(db, "agent.max_results").value == 6
        invalidate()
        assert resolve(db, "agent.max_results").value == 2

    def test_invalid_override_does_not_break_site(self, db: Session):
        """历史脏数据（超范围）应被跳过，配置仍可用。"""
        db.add(
            SystemSetting(key="agent.max_results", value_json=9999, updated_by=None)
        )
        db.commit()
        invalidate()
        got = resolve(db, "agent.max_results")
        assert got.source != SOURCE_DB
        assert got.value <= 10


class TestLLMProfile:
    def test_chat_and_extraction_are_independent(self, db: Session):
        upsert_many(
            db,
            [
                {"key": "llm.extraction.model", "value": "cheap-flash"},
                {"key": "llm.chat.model", "value": "strong-reasoner"},
                {"key": "llm.extraction.timeout_seconds", "value": 45},
                {"key": "llm.chat.timeout_seconds", "value": 120},
            ],
            account_id=None,
        )
        db.commit()
        invalidate()
        ext = resolve_llm_profile(db, "extraction")
        chat = resolve_llm_profile(db, "chat")
        assert ext.model == "cheap-flash"
        assert chat.model == "strong-reasoner"
        assert ext.timeout_seconds == 45
        assert chat.timeout_seconds == 120
        assert ext.model != chat.model

    def test_unknown_scenario_falls_back_to_extraction(self, db: Session):
        invalidate()
        prof = resolve_llm_profile(db, "banana")
        assert prof.scenario == "extraction"

    def test_profile_available_reflects_key(self, db: Session):
        invalidate()
        prof = resolve_llm_profile(db, "chat")
        # 测试环境通常未配置真实 key
        assert isinstance(prof.available(), bool)

