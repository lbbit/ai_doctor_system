"""邮件通知：内容安全、降级、离线可测。"""

import pytest

from app.config import mail as mail_mod
from app.config.mail import (
    ConfigChangeMail,
    _build_message,
    notify_by_mail,
    resolve_recipients,
    send_config_change_mail,
)
from app.config.settings import settings


@pytest.fixture()
def smtp_env(monkeypatch):
    """配置一个"已启用"的 SMTP（不含真实凭据）。"""

    def _apply(
        enabled: bool = True,
        host: str = "smtp.example.com",
        password: str = "test-password-not-real",
        to: str = "admin@example.com",
    ):
        monkeypatch.setattr(settings, "smtp_enabled", enabled)
        monkeypatch.setattr(settings, "smtp_host", host)
        monkeypatch.setattr(settings, "smtp_port", 465)
        monkeypatch.setattr(settings, "smtp_use_ssl", True)
        monkeypatch.setattr(settings, "smtp_username", "sender@example.com")
        monkeypatch.setattr(settings, "smtp_password", password)
        monkeypatch.setattr(settings, "smtp_from", "sender@example.com")
        monkeypatch.setattr(settings, "smtp_to", to)
        monkeypatch.setattr(settings, "smtp_timeout_seconds", 5)

    return _apply


def _mail(high_impact: bool = True) -> ConfigChangeMail:
    from datetime import datetime, timezone

    return ConfigChangeMail(
        action="config.update",
        keys=["llm.chat.model"],
        actor_email="actor@example.com",
        high_impact=high_impact,
        occurred_at=datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc),
    )


class TestConfigured:
    def test_not_configured_returns_false(self, monkeypatch):
        monkeypatch.setattr(settings, "smtp_enabled", False)
        assert send_config_change_mail(_mail(), recipients=["a@b.com"]) is False

    def test_missing_password_counts_as_unconfigured(self, smtp_env, monkeypatch):
        smtp_env(password="")
        assert settings.smtp_configured() is False
        assert send_config_change_mail(_mail(), recipients=["a@b.com"]) is False

    def test_missing_host_counts_as_unconfigured(self, smtp_env):
        smtp_env(host="")
        assert settings.smtp_configured() is False

    def test_no_recipients_returns_false(self, smtp_env):
        assert send_config_change_mail(_mail(), recipients=[]) is False


class TestMessageContent:
    def test_subject_marks_high_impact(self):
        assert "高影响" in _mail(high_impact=True).subject()
        assert "高影响" not in _mail(high_impact=False).subject()

    def test_body_lists_keys(self):
        body = _mail().body_text()
        assert "llm.chat.model" in body
        assert "actor@example.com" in body
        assert "2026-10-22" in body

    def test_body_explicitly_states_no_values(self):
        body = _mail().body_text()
        assert "不包含任何配置值或密钥内容" in body

    def test_body_never_contains_password(self, smtp_env):
        """关键安全断言：邮件正文绝不能出现 SMTP 密码。"""
        smtp_env(password="super-secret-smtp-pwd")
        body = _mail().body_text()
        assert "super-secret-smtp-pwd" not in body

    def test_built_message_headers(self, smtp_env):
        smtp_env()
        msg = _build_message(_mail(), ["admin@example.com"])
        assert "系统配置变更通知" in msg["Subject"]
        assert msg["To"] == "admin@example.com"
        assert msg["From"]  # 有发件人
        payload = msg.get_content()
        assert "llm.chat.model" in payload
        assert "super-secret" not in payload


class TestSendingDegrade:
    def test_send_failure_does_not_raise(self, smtp_env, monkeypatch):
        smtp_env()

        def boom(msg, to):
            raise OSError("smtp unreachable")

        monkeypatch.setattr(mail_mod, "_send_mail_impl", boom)
        ok = send_config_change_mail(_mail(), recipients=["a@b.com"], background=False)
        # 已提交发送，但内部失败被吞掉（不抛异常）
        assert ok is True

    def test_send_success_marks_true(self, smtp_env, monkeypatch):
        smtp_env()
        sent = []

        def fake_send(msg, to):
            sent.append((msg["Subject"], to))

        monkeypatch.setattr(mail_mod, "_send_mail_impl", fake_send)
        assert send_config_change_mail(_mail(), recipients=["a@b.com"], background=False) is True
        assert len(sent) == 1
        assert sent[0][1] == ["a@b.com"]

    def test_background_mode_returns_true(self, smtp_env, monkeypatch):
        smtp_env()
        monkeypatch.setattr(mail_mod, "_send_mail_impl", lambda msg, to: None)
        assert send_config_change_mail(_mail(), recipients=["a@b.com"], background=True) is True

    def test_notify_by_mail_wrapper(self, smtp_env, monkeypatch):
        smtp_env()
        captured = []
        monkeypatch.setattr(
            mail_mod, "_send_mail_impl", lambda msg, to: captured.append(msg["Subject"])
        )
        assert (
            notify_by_mail(
                action="config.update",
                keys=["agent.enabled"],
                actor_email="x@y.com",
                high_impact=True,
                background=False,
            )
            is True
        )
        assert captured and "高影响" in captured[0]


class TestRecipients:
    def test_explicit_smtp_to_wins(self, smtp_env):
        smtp_env(to="a@b.com, c@d.com")
        assert resolve_recipients() == ["a@b.com", "c@d.com"]

    def test_falls_back_to_admins_when_empty(self, smtp_env, db, client, auth):
        smtp_env(to="")
        recipients = resolve_recipients(db)
        assert auth["account"]["email"] in recipients

    def test_no_db_and_no_explicit_returns_empty(self, smtp_env):
        smtp_env(to="")
        assert resolve_recipients(None) == []
