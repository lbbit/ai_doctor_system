"""Agent 工具执行层：超时、失败隔离、结果脱敏与清理。

关键约束：
- 单工具失败/超时**不得**中断问诊，只记录 toolErrors 并继续生成；
- 工具结果进入模型前统一走 sanitizer（防外部页面里的个人信息出域）；
- 结果长度受限，避免上下文膨胀与成本失控。
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from app.agent.search import SearchProvider, SearchResult, build_provider
from app.agent.skills import ClinicalSkill, render_skills_block, select_skills

logger = logging.getLogger("app.agent.tools")

MAX_TOTAL_CHARS = 3000


@dataclass
class ToolCall:
    name: str
    args: Dict[str, str] = field(default_factory=dict)


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    payload: str = ""
    error: str = ""
    results: List[SearchResult] = field(default_factory=list)


@dataclass
class ToolBundle:
    """一次问诊中所有工具执行结果。"""

    outcomes: List[ToolOutcome] = field(default_factory=list)
    skills: List[ClinicalSkill] = field(default_factory=list)

    @property
    def used_tools(self) -> List[str]:
        return [o.name for o in self.outcomes if o.ok]

    @property
    def tool_errors(self) -> List[str]:
        return [f"{o.name}:{o.error}" for o in self.outcomes if not o.ok]

    def render_for_prompt(self) -> str:
        """渲染为注入模型的资料块；空则返回空串（不产生空标题噪声）。"""
        blocks: List[str] = []
        skills_block = render_skills_block(self.skills)
        if skills_block:
            blocks.append(skills_block)
        search_blocks = [o.payload for o in self.outcomes if o.ok and o.payload]
        if search_blocks:
            blocks.append(
                "## 外部检索资料（仅作参考，需与档案交叉核对，引用时必须给出处）\n"
                + "\n\n".join(search_blocks)
            )
        text = "\n\n".join(blocks)
        return text[:MAX_TOTAL_CHARS]


def _clean_query(query: str) -> str:
    """搜索查询清理：去掉可能的注入指令与超长内容。"""
    text = re.sub(r"\s+", " ", (query or "")).strip()
    text = text[:200]
    # 去掉明显的提示注入尝试
    text = re.sub(r"(?i)(ignore previous|system prompt|忽略以上|忽略之前)", "", text)
    return text.strip()


def execute_tools(
    calls: List[ToolCall],
    *,
    query: str,
    timeout_seconds: int = 8,
    max_results: int = 5,
    provider: Optional[SearchProvider] = None,
    sanitizer: Optional[Callable[[str], str]] = None,
    max_calls: int = 3,
) -> ToolBundle:
    """执行工具调用列表（串行、限次、超时隔离、结果脱敏）。"""
    bundle = ToolBundle()
    # 临床 skill 总是按问题本体选择，不依赖模型是否调用
    bundle.skills = select_skills(query)

    if not calls:
        return bundle

    provider_cache: List[Optional[SearchProvider]] = [provider]
    for call in calls[:max_calls]:
        if call.name != "web_search":
            bundle.outcomes.append(
                ToolOutcome(name=call.name, ok=False, error="unknown_tool")
            )
            continue

        search_query = _clean_query(call.args.get("query") or query)
        if not search_query:
            bundle.outcomes.append(
                ToolOutcome(name=call.name, ok=False, error="empty_query")
            )
            continue

        if provider_cache[0] is None:
            provider_cache[0] = build_provider()
        active = provider_cache[0]
        if active is None:
            bundle.outcomes.append(
                ToolOutcome(name=call.name, ok=False, error="search_disabled")
            )
            continue

        try:
            results = _run_with_timeout(
                lambda: active.search(search_query, max_results=max_results),
                timeout_seconds,
            )
        except FuturesTimeout:
            logger.warning("tool.web_search.timeout query=%s", search_query[:40])
            bundle.outcomes.append(
                ToolOutcome(name=call.name, ok=False, error="timeout")
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 外部失败必须隔离
            logger.warning("tool.web_search.failed reason=%s", type(exc).__name__)
            bundle.outcomes.append(
                ToolOutcome(name=call.name, ok=False, error=type(exc).__name__)
            )
            continue

        if not results:
            bundle.outcomes.append(
                ToolOutcome(name=call.name, ok=False, error="no_results")
            )
            continue

        lines: List[str] = []
        for i, r in enumerate(results, start=1):
            snippet = r.snippet
            if sanitizer is not None:
                try:
                    snippet = sanitizer(snippet)
                except Exception:  # noqa: BLE001
                    pass
            lines.append(f"[{i}] {r.title}\n{snippet}\n来源：{r.url}")
        bundle.outcomes.append(
            ToolOutcome(
                name=call.name,
                ok=True,
                payload="\n\n".join(lines)[:MAX_TOTAL_CHARS],
                results=results,
            )
        )
    return bundle


def _run_with_timeout(fn, seconds: int):
    """在独立线程执行并限时；超时抛 FuturesTimeout（不泄漏线程引用）。"""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn)
        return future.result(timeout=max(1, seconds))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
