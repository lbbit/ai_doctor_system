"""检索词提炼（规则规划质量）+ 流式空返回降级。"""

import pytest

from app.agent.planner import _build_query, rule_based_plan
from app.config import resolver


class TestBuildQuery:
    """整句直接检索会 no_results（实测），必须提炼关键词。"""

    @pytest.mark.parametrize(
        "question,must_contain,must_not_contain",
        [
            ("高尿酸血症的饮食禁忌有哪些？", "高尿酸血症", "有哪些"),
            ("高血压的诊断标准是什么？", "高血压", "是什么"),
            ("二甲双胍有什么副作用", "二甲双胍", "有什么"),
            ("血脂的参考范围是多少", "血脂", "是多少"),
            ("糖尿病怎么控制血糖？", "糖尿病", "怎么"),
        ],
    )
    def test_strips_question_words(self, question, must_contain, must_not_contain):
        query = _build_query(question)
        assert must_contain in query
        assert must_not_contain not in query
        assert "？" not in query and "?" not in query

    def test_removes_punctuation(self):
        assert "，" not in _build_query("高血压，高血脂，怎么办？")

    def test_keeps_medical_terms(self):
        query = _build_query("请问高尿酸血症患者能吃海鲜吗")
        assert "高尿酸血症" in query

    def test_length_bounded(self):
        long_q = "高血压" * 50
        assert len(_build_query(long_q)) <= 60

    def test_fallback_when_all_filtered(self):
        """全被过滤时不返回空串。"""
        query = _build_query("是怎样的")
        assert query.strip()


class TestRulePlanQueryQuality:
    def test_query_is_keywords_not_sentence(self):
        plan = rule_based_plan("高尿酸血症的饮食禁忌有哪些？")
        assert plan is not None
        query = plan.calls[0].args["query"]
        assert "高尿酸血症" in query
        assert "有哪些" not in query
        assert len(query) < 40


@pytest.fixture(autouse=True)
def _reset():
    resolver.invalidate()
    yield
    resolver.invalidate()
