"""多源搜索并行化：锁定"不累加超时"的性能不变量。"""

import time

from app.agent.search import CompositeSearchProvider, SearchResult


class _SlowProvider:
    def __init__(self, name: str, delay: float, results=None):
        self.name = name
        self.delay = delay
        self._results = results or []
        self.calls = 0

    def search(self, query, max_results=5):
        self.calls += 1
        time.sleep(self.delay)
        return self._results[:max_results]


class _FailingProvider:
    def __init__(self, name: str, delay: float = 0.0):
        self.name = name
        self.delay = delay

    def search(self, query, max_results=5):
        time.sleep(self.delay)
        raise OSError("boom")


def test_parallel_is_faster_than_sum():
    """两个各 0.3s 的源：总耗时应在 0.5s 内（串行会是 0.6s+）。"""
    p1 = _SlowProvider("a", 0.3, [SearchResult(title="A", url="https://a.com")])
    p2 = _SlowProvider("b", 0.3, [SearchResult(title="B", url="https://b.com")])
    started = time.perf_counter()
    merged = CompositeSearchProvider([p1, p2]).search("q", max_results=5)
    elapsed = time.perf_counter() - started
    assert len(merged) == 2
    assert elapsed < 0.5, f"应为并发（<0.5s），实际 {elapsed:.2f}s"
    assert p1.calls == 1 and p2.calls == 1


def test_slow_source_does_not_block_fast_results():
    """慢源（0.5s）不应延迟快源结果可用性（仍返回两者，但总时长为最慢）。"""
    fast = _SlowProvider("fast", 0.05, [SearchResult(title="F", url="https://f.com")])
    slow = _SlowProvider("slow", 0.4, [SearchResult(title="S", url="https://s.com")])
    started = time.perf_counter()
    merged = CompositeSearchProvider([fast, slow]).search("q", max_results=5)
    elapsed = time.perf_counter() - started
    assert [r.title for r in merged] == ["F", "S"]
    assert elapsed < 0.6


def test_failing_source_isolated():
    ok = _SlowProvider("ok", 0.05, [SearchResult(title="OK", url="https://ok.com")])
    bad = _FailingProvider("bad", 0.05)
    merged = CompositeSearchProvider([ok, bad]).search("q", max_results=5)
    assert [r.title for r in merged] == ["OK"]


def test_order_deterministic_regardless_of_completion():
    """慢的主源 + 快的补充源：结果顺序仍按配置优先级（主源在前）。"""
    primary = _SlowProvider("primary", 0.25, [SearchResult(title="P", url="https://p.com")])
    secondary = _SlowProvider("secondary", 0.02, [SearchResult(title="S", url="https://s.com")])
    merged = CompositeSearchProvider([primary, secondary]).search("q", max_results=5)
    assert [r.title for r in merged] == ["P", "S"]


def test_single_provider_no_thread_overhead():
    p = _SlowProvider("only", 0.05, [SearchResult(title="A", url="https://a.com")])
    merged = CompositeSearchProvider([p]).search("q", max_results=5)
    assert [r.title for r in merged] == ["A"]
    assert p.calls == 1


def test_empty_providers():
    assert CompositeSearchProvider([]).search("q") == []
