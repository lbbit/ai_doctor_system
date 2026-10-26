"""联网搜索提供方（免费优先，可配置）。

默认 ``free``：使用 DuckDuckGo Lite（无需 key、无需注册）。
可选 ``searxng``：用户自建实例，隐私更好、可自定义结果源。
可选 ``none``：完全关闭联网。

设计约束（AGENTS.md AI 底座原则）：
- 外部结果一律视为**建议**，最终结论仍由模型基于档案 + 规则生成；
- 搜索失败必须可降级（返回空列表并记录原因），不得抛出中断问诊；
- 结果内容进入模型前必须脱敏（由 tools.py 统一处理）。
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger("app.agent.search")

USER_AGENT = "Mozilla/5.0 (compatible; AIDoctor/0.1; +https://localhost)"
MAX_SNIPPET_CHARS = 600


@dataclass
class SearchResult:
    """单条搜索结果（已去除 HTML 标签）。"""

    title: str
    url: str
    snippet: str = ""
    source: str = ""

    def to_prompt_line(self) -> str:
        return f"- {self.title}\n  {self.snippet}\n  来源：{self.url}"


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        ...


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(raw: str) -> str:
    """去标签 + 反转义 + 折叠空白，并截断到 MAX_SNIPPET_CHARS。"""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:MAX_SNIPPET_CHARS]


def is_safe_url(url: str) -> bool:
    """只允许 http/https 且无内嵌凭据（防 javascript:/data:/凭据泄露）。"""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    return bool(parsed.hostname)


def unwrap_ddg_redirect(url: str) -> str:
    """DDG Lite 结果可能是 //duckduckgo.com/l/?uddg=<encoded> 形式的跳转链接。"""
    if not url:
        return url
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url or "/l/?uddg=" in url:
        try:
            qs = parse_qs(urlparse(url).query)
            target = qs.get("uddg", [""])[0]
            if target:
                return unquote(target)
        except Exception:  # noqa: BLE001
            return url
    return url


class WikipediaProvider:
    """免费默认提供方：Wikipedia 搜索 API（无需 key、无配额声明、容器内实测可达）。

    选择依据（2026-10-22 容器内实测）：
    - DuckDuckGo Lite/HTML 在容器出口 IP 上被反爬拦截（返回 14KB 空壳页，0 结果）；
    - Wikipedia Action API 稳定返回结构化结果，且医学术语覆盖良好。

    局限：适合概念/疾病/指标释义，不适合"最新指南原文"。因此结果仅作参考，
    仍要求模型与档案交叉核对。
    """

    name = "free"

    def __init__(
        self,
        timeout_seconds: int = 4,
        languages: tuple[str, ...] = ("zh", "en"),
    ) -> None:
        self.timeout = timeout_seconds
        self.languages = languages

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []
        # 多语言**并行**：串行时 zh 失败（DNS/超时）还要再等 en 一轮，
        # 实测最坏 10s（5s+5s），是首字延迟的主要来源之一。
        if len(self.languages) == 1:
            return self._search_lang(self.languages[0], q, max_results)

        from concurrent.futures import ThreadPoolExecutor

        per_lang: Dict[str, List[SearchResult]] = {}
        with ThreadPoolExecutor(max_workers=len(self.languages)) as pool:
            futures = {
                pool.submit(self._search_lang, lang, q, max_results): lang
                for lang in self.languages
            }
            for future, lang in futures.items():
                try:
                    per_lang[lang] = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "search.free.lang_failed lang=%s reason=%s",
                        lang,
                        type(exc).__name__,
                    )
                    per_lang[lang] = []

        # 按 languages 声明顺序优先取结果（保证确定性）
        for lang in self.languages:
            found = per_lang.get(lang) or []
            if found:
                return found
        logger.warning("search.free.empty query=%s", q[:40])
        return []

    def _search_lang(self, lang: str, q: str, max_results: int) -> List[SearchResult]:
        try:
            import json
            import urllib.parse
            import urllib.request

            api = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": q[:200],
                    "format": "json",
                    "srlimit": str(max_results),
                    "utf8": "1",
                }
            )
            req = urllib.request.Request(
                api,
                headers={
                    # Wikimedia 要求可识别的 UA，否则 403
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 - 外部依赖不可靠，必须降级
            logger.warning(
                "search.free.failed lang=%s reason=%s", lang, type(exc).__name__
            )
            return []

        hits = ((data.get("query") or {}).get("search")) or []
        results: List[SearchResult] = []
        for hit in hits[:max_results]:
            if not isinstance(hit, dict):
                continue
            title = strip_html(str(hit.get("title") or ""))
            if not title:
                continue
            page_url = (
                f"https://{lang}.wikipedia.org/wiki/"
                + urllib.parse.quote(title.replace(" ", "_"))
            )
            if not is_safe_url(page_url):
                continue
            results.append(
                SearchResult(
                    title=title,
                    url=page_url,
                    snippet=strip_html(str(hit.get("snippet") or "")),
                    source=f"wikipedia:{lang}",
                )
            )
        if results:
            logger.info("search.free.ok lang=%s count=%s", lang, len(results))
        return results


class DuckDuckGoInstantAnswerProvider:
    """DDG Instant Answer API（无需 key，免费补充源）。

    实测特性（2026-10-22 容器内）：
    - 英文实体命中良好（如 ``metformin`` 返回 849 字符摘要 + 7 条相关话题）；
    - **中文查询返回空**，因此只作为补充源，不能单独使用；
    - 偶发 DNS 解析失败，必须降级为空列表。

    定位：与 Wikipedia 结果合并使用，提升英文药名/疾病名的覆盖率。
    """

    name = "free_ddg"

    def __init__(self, timeout_seconds: int = 4) -> None:
        self.timeout = timeout_seconds

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []
        try:
            import json
            import urllib.parse
            import urllib.request

            api = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
                {
                    "q": q[:200],
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                }
            )
            req = urllib.request.Request(
                api, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 - 外部依赖不可靠，必须降级
            logger.warning("search.ddg_ia.failed reason=%s", type(exc).__name__)
            return []

        results: List[SearchResult] = []
        heading = strip_html(str(data.get("Heading") or ""))
        abstract = strip_html(str(data.get("AbstractText") or ""))
        abstract_url = str(data.get("AbstractURL") or "")
        if heading and abstract and is_safe_url(abstract_url):
            results.append(
                SearchResult(
                    title=heading,
                    url=abstract_url,
                    snippet=abstract,
                    source=self.name,
                )
            )
        for topic in data.get("RelatedTopics") or []:
            if len(results) >= max_results:
                break
            if not isinstance(topic, dict):
                continue
            text = strip_html(str(topic.get("Text") or ""))
            url = str(topic.get("FirstURL") or "")
            if not text or not is_safe_url(url):
                continue
            # Text 形如 "标题 - 描述"，拆出标题以获得更清晰的引用
            title = text.split(" - ", 1)[0][:120]
            snippet = text.split(" - ", 1)[1] if " - " in text else text
            results.append(
                SearchResult(
                    title=title, url=url, snippet=snippet, source=self.name
                )
            )
        if results:
            logger.info("search.ddg_ia.ok count=%s", len(results))
        return results[:max_results]


class CompositeSearchProvider:
    """多源合并：主源（Wikipedia）+ 补充源（DDG IA），按 URL 去重。

    **并行执行**：串行时最坏耗时 = 各源超时之和（实测 12.6s，用户等待过久）。
    改为并发后总耗时 ≈ 最慢单源，且单源失败/超时不影响其余结果。

    合并顺序保持"主源优先"，仅按完成顺序收集后重新排序，因此结果确定。
    """

    name = "free"

    def __init__(self, providers: List[SearchProvider]) -> None:
        self.providers = [p for p in providers if p is not None]

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if not self.providers:
            return []
        if len(self.providers) == 1:
            try:
                return self.providers[0].search(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "search.composite.source_failed source=%s reason=%s",
                    self.providers[0].name,
                    type(exc).__name__,
                )
                return []

        # 并行执行：整体耗时由最慢单源决定，而非累加
        from concurrent.futures import ThreadPoolExecutor

        per_source: Dict[int, List[SearchResult]] = {}
        with ThreadPoolExecutor(max_workers=len(self.providers)) as pool:
            futures = {
                pool.submit(p.search, query, max_results): idx
                for idx, p in enumerate(self.providers)
            }
            for future, idx in futures.items():
                try:
                    per_source[idx] = future.result()
                except Exception as exc:  # noqa: BLE001 - 单源失败不影响整体
                    logger.warning(
                        "search.composite.source_failed source=%s reason=%s",
                        self.providers[idx].name,
                        type(exc).__name__,
                    )
                    per_source[idx] = []

        # 按原始优先级合并（保证结果确定性，不受返回顺序影响）
        merged: List[SearchResult] = []
        seen: set[str] = set()
        for idx in range(len(self.providers)):
            if len(merged) >= max_results:
                break
            for item in per_source.get(idx, []):
                key = item.url.strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                if len(merged) >= max_results:
                    break
        return merged


class SearxngProvider:
    """自建 SearXNG（JSON API）。"""

    name = "searxng"

    def __init__(self, base_url: str, timeout_seconds: int = 8) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout_seconds

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        q = (query or "").strip()
        if not q or not is_safe_url(self.base_url):
            return []
        try:
            import json
            import urllib.parse
            import urllib.request

            url = f"{self.base_url}/search?" + urllib.parse.urlencode(
                {"q": q[:300], "format": "json", "safesearch": "1"}
            )
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("search.searxng.failed reason=%s", type(exc).__name__)
            return []

        out: List[SearchResult] = []
        for item in (data.get("results") or [])[:max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not is_safe_url(url):
                continue
            title = strip_html(str(item.get("title") or ""))
            if not title:
                continue
            out.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=strip_html(str(item.get("content") or "")),
                    source=self.name,
                )
            )
        return out


def build_provider(db=None) -> Optional[SearchProvider]:
    """按配置构造提供方；``none`` 返回 None（表示不联网）。

    ``free`` 默认返回**多源合并**：Wikipedia（中文医学概念）+ DDG Instant Answer
    （英文药名/疾病实体补充），单源失败不影响整体。
    """
    from app.config import resolver

    provider = resolver.get_str(db, "agent.search_provider", "free")
    timeout = resolver.get_int(db, "agent.tool_timeout_seconds", 5)
    if provider == "none":
        return None
    if provider == "searxng":
        base = resolver.get_str(db, "agent.searxng_base_url", "")
        if not base:
            logger.warning("search.searxng.missing_base_url fallback=free")
        else:
            return SearxngProvider(base, timeout_seconds=timeout)
    return CompositeSearchProvider(
        [
            WikipediaProvider(timeout_seconds=timeout),
            DuckDuckGoInstantAnswerProvider(timeout_seconds=timeout),
        ]
    )


class FakeSearchProvider:
    """测试替身（AGENTS.md 第 7 条：AI 能力必须有离线替身）。"""

    name = "fake"

    def __init__(self, results: Optional[List[SearchResult]] = None, raises: bool = False):
        self._results = results or []
        self._raises = raises
        self.calls: List[str] = []

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        self.calls.append(query)
        if self._raises:
            raise TimeoutError("fake search timeout")
        return self._results[:max_results]
