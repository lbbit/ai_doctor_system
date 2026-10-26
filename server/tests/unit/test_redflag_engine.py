"""红旗引擎单测（A-1/AC-11.x：词表+逻辑+obs 条件；不交给模型）。"""

from types import SimpleNamespace

from app.redflag.engine import match_rules


def _rule(**kw):
    base = dict(
        id="r1",
        code="RF_TEST",
        name="测试规则",
        keywords=["胸痛"],
        keyword_logic="any",
        context_logic=None,
        severity="high",
        message="立即就医",
        action="refer_now",
        enabled=True,
        version=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_keyword_any_hit():
    rules = [_rule(keywords=["胸痛", "心口疼"], keyword_logic="any")]
    hits = match_rules(rules, "我胸口疼，胸痛持续5分钟")
    assert len(hits) == 1
    assert hits[0]["code"] == "RF_TEST"
    assert hits[0]["severity"] == "high"


def test_keyword_any_miss():
    rules = [_rule(keywords=["胸痛", "心口疼"])]
    assert match_rules(rules, "今天天气不错") == []


def test_keyword_all():
    rules = [_rule(keywords=["头痛", "呕吐"], keyword_logic="all")]
    assert match_rules(rules, "头痛") == []
    assert len(match_rules(rules, "头痛还呕吐")) == 1


def test_context_tree_logic():
    rules = [
        _rule(
            keywords=["胸痛"],
            context_logic={
                "all": [
                    {"kw": "胸痛"},
                    {"any": [{"kw": "放射"}, {"kw": "左臂"}]},
                ]
            },
        )
    ]
    # 树未满足：单独胸痛不命中（有 context_logic 时按树判定）
    assert match_rules(rules, "胸口有些闷，胸痛一点点") == []
    assert len(match_rules(rules, "胸痛并向左臂放射")) == 1


def test_obs_condition_deterministic():
    rules = [
        _rule(
            keywords=["血压"],
            context_logic={
                "all": [
                    {"kw": "血压"},
                    {"any": [{"obs": {"code": "BP_SYS", "gte": 180}}]},
                ]
            },
        )
    ]
    assert match_rules(rules, "血压情况如何", {"BP_SYS": 150}) == []
    hits = match_rules(rules, "血压情况如何", {"BP_SYS": 185})
    assert len(hits) == 1


def test_disabled_rule_skipped():
    rules = [_rule(enabled=False)]
    assert match_rules(rules, "胸痛") == []


def test_no_context_uses_keyword_logic_only():
    rules = [_rule(keywords=["晕倒"])]
    hits = match_rules(rules, "他突然晕倒了")
    assert hits and hits[0]["matchedTerms"] == ["晕倒"]
