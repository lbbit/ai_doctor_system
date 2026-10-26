"""配置写权限收敛（§6.9 / PLAN §17）。"""

import pytest
from fastapi.testclient import TestClient

from app.config import resolver
from app.config.permissions import (
    POLICY_ADMIN_ONLY,
    POLICY_ANY,
    POLICY_FIRST_ACCOUNT,
    ROLE_ADMIN,
    ROLE_USER,
    assign_role_for_new_account,
    is_admin,
)
from app.config.settings import settings


@pytest.fixture(autouse=True)
def _reset():
    resolver.invalidate()
    yield
    resolver.invalidate()


def _set_policy(monkeypatch, policy: str, emails: str = ""):
    monkeypatch.setattr(settings, "config_write_policy", policy)
    monkeypatch.setattr(settings, "config_admin_emails", emails)


class TestRoleAssignment:
    def test_first_account_becomes_admin(self, client: TestClient, auth: dict):
        """首个注册账户自动为 admin，保证开箱可配置。"""
        me = client.get("/api/auth/me", headers=auth["headers"]).json()["data"]
        assert me["account"]["role"] == ROLE_ADMIN

    def test_second_account_is_user(self, client: TestClient, auth: dict, second_auth: dict):
        me = client.get("/api/auth/me", headers=second_auth["headers"]).json()["data"]
        assert me["account"]["role"] == ROLE_USER

    def test_assign_role_returns_user_when_accounts_exist(self, db, client, auth):
        assert assign_role_for_new_account(db) == ROLE_USER


class TestAdminOnlyPolicy:
    def test_admin_can_write(self, client: TestClient, auth: dict, monkeypatch):
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        r = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 15}]},
        )
        assert r.status_code == 200

    def test_non_admin_cannot_write(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        """关键回归：非管理员写全局配置必须 403。"""
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        r = client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 40}]},
        )
        assert r.status_code == 403
        assert "无权" in r.json()["message"]

    def test_non_admin_cannot_reset(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        r = client.post(
            "/api/settings/system/reset",
            headers=second_auth["headers"],
            json={"keys": ["chat.history_limit"]},
        )
        assert r.status_code == 403

    def test_non_admin_cannot_test(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        """test 端点会发起外部请求，也必须有权限校验，避免被当作探测中继。"""
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        r = client.post(
            "/api/settings/system/test",
            headers=second_auth["headers"],
            json={"key": "llm.chat.model"},
        )
        assert r.status_code == 403

    def test_rejected_write_does_not_persist(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 40}]},
        )
        items = client.get(
            "/api/settings/system", headers=auth["headers"]
        ).json()["data"]["items"]
        assert {i["key"]: i for i in items}["chat.history_limit"]["source"] != "db"

    def test_non_admin_sees_readonly(self, client: TestClient, auth: dict, second_auth: dict, monkeypatch):
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        data = client.get(
            "/api/settings/system", headers=second_auth["headers"]
        ).json()["data"]
        assert data["editable"] is False
        assert "只读" in data["note"]

    def test_non_admin_can_still_read(self, client: TestClient, auth: dict, second_auth: dict, monkeypatch):
        """读权限保留：普通用户仍需看到当前配置（便于自查与报障）。"""
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        r = client.get("/api/settings/system", headers=second_auth["headers"])
        assert r.status_code == 200
        assert len(r.json()["data"]["items"]) >= 15

    def test_secrets_still_masked_for_non_admin(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY)
        items = client.get(
            "/api/settings/system", headers=second_auth["headers"]
        ).json()["data"]["items"]
        key_item = {i["key"]: i for i in items}["llm.chat.api_key"]
        assert key_item["value"] in ("", "••••••••")
        assert key_item["isSecret"] is True


class TestEmailWhitelist:
    def test_whitelisted_email_can_write(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        """白名单优先级高于 role：便于环境变量紧急授权。"""
        email = second_auth["account"]["email"]
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY, emails=email.upper())
        r = client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 22}]},
        )
        assert r.status_code == 200

    def test_empty_whitelist_no_effect(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        _set_policy(monkeypatch, POLICY_ADMIN_ONLY, emails="")
        r = client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 22}]},
        )
        assert r.status_code == 403


class TestPolicyModes:
    def test_any_policy_allows_all(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        _set_policy(monkeypatch, POLICY_ANY)
        r = client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 18}]},
        )
        assert r.status_code == 200

    def test_first_account_policy_allows_earliest(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        _set_policy(monkeypatch, POLICY_FIRST_ACCOUNT)
        ok = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 16}]},
        )
        assert ok.status_code == 200
        denied = client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 17}]},
        )
        assert denied.status_code == 403

    def test_unknown_policy_fails_closed(
        self, client: TestClient, auth: dict, second_auth: dict, monkeypatch
    ):
        """策略配错时必须按最严格处理（fail-closed），不能意外放开。"""
        _set_policy(monkeypatch, "banana")
        r = client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 20}]},
        )
        assert r.status_code == 403

    def test_unknown_policy_admin_still_works(
        self, client: TestClient, auth: dict, monkeypatch
    ):
        _set_policy(monkeypatch, "banana")
        r = client.patch(
            "/api/settings/system",
            headers=auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 19}]},
        )
        assert r.status_code == 200


class TestIsAdminHelper:
    def test_none_account(self, db):
        assert is_admin(db, None) is False

    def test_inactive_account_denied(self, db, client, auth):
        from app.db.models import Account

        account = db.get(Account, auth["account"]["id"])
        account.status = "disabled"
        db.commit()
        assert is_admin(db, account) is False
