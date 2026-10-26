"""规则化规划：能用规则收敛的不调模型（§19 性能优化）。"""

import pytest

from app.agent.planner import rule_based_plan


class TestSelfDataSignals:
    @pytest.mark.parametrize(
        "question",
        [
            "我上次血压多少",
            "我的记录里血糖最高是多少",
            "帮我看看我的档案里的血脂",
            "我最近测的体温是多少",
            "历史趋势怎么样",
            "帮我记一下今天体温 36.5",
            "我上传的这份报告说了什么",
        ],
    )
    def test_self_data_no_search(self, question):
        plan = rule_based_plan(question)
        assert plan is not None
        assert plan.need_search is False
        assert plan.calls == []
        assert plan.reason == "rule_self_data"


class TestNeedsSearchSignals:
    @pytest.mark.parametrize(
        "question",
        [
            "成人高血压的诊断标准是多少",
            "血脂的参考范围是什么",
            "二甲双胍有什么副作用",
            "中国糖尿病防治指南推荐怎么吃",
            "感冒一般怎么治疗",
            "高尿酸血症是什么原因引起的",
        ],
    )
    def test_needs_search(self, question):
        plan = rule_based_plan(question)
        assert plan is not None
        assert plan.need_search is True
        assert len(plan.calls) == 1
        assert plan.calls[0].name == "web_search"
        assert plan.calls[0].args["query"]
        assert plan.reason == "rule_needs_search"


class TestAmbiguous:
    @pytest.mark.parametrize(
        "question",
        [
            "你好",
            "最近有点不舒服",
            "我应该注意什么",
        ],
    )
    def test_returns_none_for_llm(self, question):
        """规则无法判定 → 返回 None，交模型处理（不误判）。"""
        assert rule_based_plan(question) is None

    def test_empty_question(self):
        plan = rule_based_plan("   ")
        assert plan is not None
        assert plan.need_search is False
        assert plan.reason == "empty"


class TestPriority:
    def test_self_data_wins_over_search_signal(self):
        """同时含两类信号时，问自己数据优先（避免不必要的外部请求）。"""
        plan = rule_based_plan("我的记录里的血糖和诊断标准对比一下")
        assert plan is not None
        assert plan.need_search is False

    def test_query_truncated(self):
        long_q = "高血压诊断标准" + "很长的补充说明" * 20
        plan = rule_based_plan(long_q)
        assert plan is not None
        assert len(plan.calls[0].args["query"]) <= 80
