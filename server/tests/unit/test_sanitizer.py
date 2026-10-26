"""脱敏单测（AC-14.2/14.3：出域请求无明文姓名与证件号）。"""

from app.sanitizer.patterns import age_bracket, sanitize_text
from app.sanitizer.pipeline import sanitize_for_llm


def test_id_card_and_phone_masked():
    text = "患者身份证 110101199001011234，电话 13812345678"
    out, replaced = sanitize_text(text)
    assert "110101199001011234" not in out
    assert "13812345678" not in out
    assert "***证件号***" in out
    assert "***电话***" in out
    kinds = {r.type for r in replaced}
    assert "id_card" in kinds and "phone" in kinds


def test_name_replaced_with_relation():
    text = "我父亲张三今天血压有点高"
    out, replaced = sanitize_text(
        text, names=["张三"], relation_map={"张三": "父亲"}
    )
    assert "张三" not in out
    assert "父亲" in out


def test_birthday_to_age_bracket():
    text = "出生日期是1950年3月12日"
    out, replaced = sanitize_text(text, birthday_iso="1950-03-12")
    assert "1950年3月12日" not in out
    assert any(r.type == "birthday" for r in replaced)
    assert "岁" in out


def test_age_bracket_adult():
    assert "-" in age_bracket("1980-01-01")


def test_email_masked():
    out, _ = sanitize_text("联系邮箱 test@example.com 谢谢")
    assert "test@example.com" not in out


def test_no_plaintext_in_replaced_summary():
    """替换摘要不含明文（Trace 脱敏摘要安全）。"""
    _, replaced = sanitize_text(
        "李四 13800001111", names=["李四"], relation_map={"李四": "母亲"}
    )
    joined = " ".join(r.repl for r in replaced)
    assert "李四" not in joined and "13800001111" not in joined


def test_pipeline_switch(monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "pii_masking_enabled", False)
    res = sanitize_for_llm("13812345678", names=[])
    assert "13812345678" in res.text  # 开关关闭原样
    monkeypatch.setattr(settings, "pii_masking_enabled", True)
    res2 = sanitize_for_llm("13812345678", names=[])
    assert "13812345678" not in res2.text
