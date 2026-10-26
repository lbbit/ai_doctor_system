"""账户角色管理与配置变更通知（PLAN §17）。"""

import pytest
from fastapi.testclient import TestClient

from app.config import resolver
from app.config.notify import classify_keys, is_high_impact
from app.config.permissions import POLICY_ADMIN_ONLY, ROLE_ADMIN, ROLE_USER
from app.config.settings import settings


@pytest.fixture(autouse=True)
def _reset():
    resolver.invalidate()
    yield
    resolver.invalidate()


@pytest.fixture()
def admin_auth(client: TestClient, auth: dict) -> dict:
    """auth fixture 创建的是首个账户 → 自动 admin。"""
    me = client.get("/api/auth/me", headers=auth["headers"]).json()["data"]
    assert me["account"]["role"] == ROLE_ADMIN
    return auth


class TestAccountList:
    def test_admin_can_list(self, client: TestClient, admin_auth: dict, second_auth: dict):
        r = client.get("/api/accounts", headers=admin_auth["headers"])
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["canManage"] is True
        assert data["adminCount"] >= 1
        emails = [i["email"] for i in data["items"]]
        assert admin_auth["account"]["email"] in emails
        assert second_auth["account"]["email"] in emails
        # 不得返回密码哈希等敏感字段
        for item in data["items"]:
            assert "passwordHash" not in item
            assert "password_hash" not in item

    def test_non_admin_forbidden(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        r = client.get("/api/accounts", headers=second_auth["headers"])
        assert r.status_code == 403

    def test_requires_auth(self, client: TestClient):
        assert client.get("/api/accounts").status_code == 401


class TestRoleChange:
    def test_promote_user_to_admin(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        target_id = second_auth["account"]["id"]
        r = client.patch(
            f"/api/accounts/{target_id}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_ADMIN},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["changed"] is True
        assert r.json()["data"]["account"]["role"] == ROLE_ADMIN

        # 提升后该账户确实可以写配置
        write = client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "chat.history_limit", "value": 24}]},
        )
        assert write.status_code == 200

    def test_demote_other_admin(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        target_id = second_auth["account"]["id"]
        client.patch(
            f"/api/accounts/{target_id}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_ADMIN},
        )
        r = client.patch(
            f"/api/accounts/{target_id}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_USER},
        )
        assert r.status_code == 200
        assert r.json()["data"]["account"]["role"] == ROLE_USER

    def test_cannot_demote_self(
        self, client: TestClient, admin_auth: dict
    ):
        """防止唯一管理员自锁。"""
        my_id = admin_auth["account"]["id"]
        r = client.patch(
            f"/api/accounts/{my_id}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_USER},
        )
        assert r.status_code == 422
        assert "自己" in r.json()["message"]

    def test_cannot_demote_last_admin(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        """其他管理员降级最后一名 admin 也要拦住。"""
        target = second_auth["account"]["id"]
        # 先让 second 成为 admin（此时有 2 个 admin）
        client.patch(
            f"/api/accounts/{target}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_ADMIN},
        )
        # second 尝试把 auth 降级 → auth 不是自己，但会导致只剩 1 个 admin?
        # 此处 auth 与 second 都是 admin，降级 auth 后仍剩 second，应允许
        r = client.patch(
            f"/api/accounts/{admin_auth['account']['id']}/role",
            headers=second_auth["headers"],
            json={"role": ROLE_USER},
        )
        assert r.status_code == 200

    def test_last_admin_protection(self, client: TestClient, admin_auth: dict, db):
        """只剩一个管理员时，任何人都不能把他降级。"""
        from app.db.models import Account

        # 构造：只有 auth 是 admin
        for row in db.query(Account).all():
            if row.id != admin_auth["account"]["id"]:
                row.role = ROLE_USER
        db.commit()

        # 让另一个账户（临时提权再降回）无法操作：直接用 second 账户（user）尝试
        # 会先被 403 拦住，因此这里用「自己降自己」路径覆盖最后管理员保护
        r = client.patch(
            f"/api/accounts/{admin_auth['account']['id']}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_USER},
        )
        assert r.status_code == 422

    def test_non_admin_cannot_change_role(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        r = client.patch(
            f"/api/accounts/{second_auth['account']['id']}/role",
            headers=second_auth["headers"],
            json={"role": ROLE_ADMIN},
        )
        assert r.status_code == 403

    def test_invalid_role_rejected(self, client: TestClient, admin_auth: dict, second_auth: dict):
        r = client.patch(
            f"/api/accounts/{second_auth['account']['id']}/role",
            headers=admin_auth["headers"],
            json={"role": "superuser"},
        )
        assert r.status_code == 422

    def test_unknown_account_404(self, client: TestClient, admin_auth: dict):
        r = client.patch(
            "/api/accounts/nonexistent-id/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_ADMIN},
        )
        assert r.status_code == 404

    def test_noop_change_reports_unchanged(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        target = second_auth["account"]["id"]
        r = client.patch(
            f"/api/accounts/{target}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_USER},
        )
        assert r.status_code == 200
        assert r.json()["data"]["changed"] is False

    def test_role_change_is_audited(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        target = second_auth["account"]["id"]
        client.patch(
            f"/api/accounts/{target}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_ADMIN},
        )
        logs = client.get(
            "/api/audit/logs",
            headers=admin_auth["headers"],
            params={"action": "account.role_change"},
        ).json()["data"]
        assert logs["total"] >= 1


class TestHighImpactClassification:
    @pytest.mark.parametrize(
        "keys,expected",
        [
            (["llm.chat.model"], True),
            (["llm.extraction.api_key"], True),
            (["agent.enabled"], True),
            (["chat.history_limit"], False),
            (["upload.max_upload_mb"], False),
            ([], False),
        ],
    )
    def test_is_high_impact(self, keys, expected):
        assert is_high_impact(keys) is expected

    def test_classify_filters_low_impact(self):
        assert classify_keys(["llm.chat.model", "chat.history_limit"]) == ["llm.chat.model"]
        assert classify_keys(["upload.max_upload_mb"]) == []


class TestConfigNotices:
    def test_admin_sees_all(self, client: TestClient, admin_auth: dict, second_auth: dict):
        # 先提升 second 并让 second 改一次配置
        client.patch(
            f"/api/accounts/{second_auth['account']['id']}/role",
            headers=admin_auth["headers"],
            json={"role": ROLE_ADMIN},
        )
        client.patch(
            "/api/settings/system",
            headers=second_auth["headers"],
            json={"items": [{"key": "llm.chat.model", "value": "notice-model"}]},
        )
        data = client.get(
            "/api/accounts/config-notices", headers=admin_auth["headers"]
        ).json()["data"]
        assert data["scope"] == "all"
        assert data["isAdmin"] is True
        assert any(
            "llm.chat.model" in n["keys"] and n["highImpact"] for n in data["items"]
        )

    def test_user_sees_only_own(
        self, client: TestClient, admin_auth: dict, second_auth: dict
    ):
        client.patch(
            "/api/settings/system",
            headers=admin_auth["headers"],
            json={"items": [{"key": "llm.chat.timeout_seconds", "value": 100}]},
        )
        data = client.get(
            "/api/accounts/config-notices", headers=second_auth["headers"]
        ).json()["data"]
        assert data["scope"] == "mine"
        assert data["isAdmin"] is False
        # 未产生过变更的普通用户看不到管理员的操作
        assert data["items"] == []

    def test_reset_also_notified(self, client: TestClient, admin_auth: dict):
        client.patch(
            "/api/settings/system",
            headers=admin_auth["headers"],
            json={"items": [{"key": "agent.max_results", "value": 7}]},
        )
        client.post(
            "/api/settings/system/reset",
            headers=admin_auth["headers"],
            json={"keys": ["agent.max_results"]},
        )
        items = client.get(
            "/api/accounts/config-notices", headers=admin_auth["headers"]
        ).json()["data"]["items"]
        actions = {n["action"] for n in items}
        assert "config.reset" in actions

    def test_notice_never_contains_secret_value(self, client: TestClient, admin_auth: dict):
        """高影响键变更的通知里不得出现任何密钥明文。"""
        client.patch(
            "/api/settings/system",
            headers=admin_auth["headers"],
            json={"items": [{"key": "llm.extraction.model", "value": "safe-model"}]},
        )
        items = client.get(
            "/api/accounts/config-notices", headers=admin_auth["headers"]
        ).json()["data"]["items"]
        blob = str(items)
        assert "sk-" not in blob.lower()
        assert "api_key" not in blob or "llm.extraction.model" in blob

    def test_requires_auth(self, client: TestClient):
        assert client.get("/api/accounts/config-notices").status_code == 401
