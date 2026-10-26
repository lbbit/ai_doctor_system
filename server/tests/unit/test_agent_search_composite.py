"""多源搜索合并与 DDG Instant Answer 补充源。"""

import json

import pytest

from app.agent.search import (
    CompositeSearchProvider,
    DuckDuckGoInstantAnswerProvider,
    FakeSearchProvider,
    SearchResult,
)


def _fake_urlopen(payload):
    class FakeResp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda *a, **k: FakeResp()


class TestDdgInstantAnswer:
    def test_parses_abstract(self, monkeypatch):
        payload = {
            "Heading": "Metformin",
            "AbstractText": "Metformin is a first-line medication for type 2 diabetes.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Metformin",
            "RelatedTopics": [],
        }
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))
        results = DuckDuckGoInstantAnswerProvider().search("metformin")
        assert len(results) == 1
        assert results[0].title == "Metformin"
        assert "first-line" in results[0].snippet

    def test_parses_related_topics(self, monkeypatch):
        payload = {
            "Heading": "",
            "AbstractText": "",
            "AbstractURL": "",
            "RelatedTopics": [
                {"Text": "高血压 - 动脉血压持续升高的慢性病", "FirstURL": "https://e.com/1"},
                {"Text": "降压药 - 用于降低血压的药物", "FirstURL": "https://e.com/2"},
            ],
        }
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))
        results = DuckDuckGoInstantAnswerProvider().search("高血压")
        assert len(results) == 2
        assert results[0].title == "高血压"
        assert "慢性病" in results[0].snippet

    def test_empty_payload_returns_empty(self, monkeypatch):
        payload = {"Heading": "", "AbstractText": "", "RelatedTopics": []}
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))
        assert DuckDuckGoInstantAnswerProvider().search("x") == []

    def test_network_failure_degrades(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("dns failure")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert DuckDuckGoInstantAnswerProvider(timeout_seconds=2).search("x") == []

    def test_unsafe_url_skipped(self, monkeypatch):
        payload = {
            "Heading": "Bad",
            "AbstractText": "text",
            "AbstractURL": "javascript:alert(1)",
            "RelatedTopics": [],
        }
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))
        assert DuckDuckGoInstantAnswerProvider().search("x") == []


class TestCompositeProvider:
    def test_primary_fills_first(self):
        primary = FakeSearchProvider(
            [
                SearchResult(title="A", url="https://a.com", snippet="sa"),
                SearchResult(title="B", url="https://b.com", snippet="sb"),
            ]
        )
        secondary = FakeSearchProvider(
            [SearchResult(title="C", url="https://c.com", snippet="sc")]
        )
        merged = CompositeSearchProvider([primary, secondary]).search("q", max_results=5)
        assert [r.title for r in merged] == ["A", "B", "C"]

    def test_parallel_calls_all_sources(self):
        """并发设计：即使主源已够用，也会同时发起补充源请求。

        这是**有意**的权衡：串行时最坏耗时 = 各源超时之和（实测 12.6s），
        并发后 ≈ 最慢单源，用户等待时间显著缩短；代价是少量额外请求。
        """
        primary_full = FakeSearchProvider(
            [SearchResult(title=f"P{i}", url=f"https://p{i}.com") for i in range(5)]
        )
        side = FakeSearchProvider([SearchResult(title="X", url="https://x.com")])
        merged = CompositeSearchProvider([primary_full, side]).search("q", max_results=5)
        assert len(merged) == 5
        assert side.calls == ["q"], "并发时会调用补充源"
        # 主源优先：结果里不含补充源条目
        assert all(r.title.startswith("P") for r in merged)

    def test_dedupe_by_url(self):
        p1 = FakeSearchProvider([SearchResult(title="A", url="https://same.com")])
        p2 = FakeSearchProvider(
            [
                SearchResult(title="A2", url="https://same.com"),
                SearchResult(title="B", url="https://b.com"),
            ]
        )
        merged = CompositeSearchProvider([p1, p2]).search("q", max_results=5)
        assert len(merged) == 2
        assert merged[0].title == "A"

    def test_one_source_failing_does_not_break(self):
        class Broken:
            name = "broken"

            def search(self, query, max_results=5):
                raise RuntimeError("boom")

        good = FakeSearchProvider([SearchResult(title="G", url="https://g.com")])
        merged = CompositeSearchProvider([Broken(), good]).search("q", max_results=5)
        assert len(merged) == 1
        assert merged[0].title == "G"

    def test_respects_max_results(self):
        p1 = FakeSearchProvider(
            [SearchResult(title=f"A{i}", url=f"https://a{i}.com") for i in range(10)]
        )
        p2 = FakeSearchProvider(
            [SearchResult(title=f"B{i}", url=f"https://b{i}.com") for i in range(10)]
        )
        merged = CompositeSearchProvider([p1, p2]).search("q", max_results=3)
        assert len(merged) == 3
