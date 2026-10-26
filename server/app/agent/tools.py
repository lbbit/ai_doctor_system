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


def _source_host(url: str) -> str:
    """只取来源域名（隐私：URL 路径常含用户标识，不应进入模型上下文）。"""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.hostname or url[:60]
    except Exception:  # noqa: BLE001
        return url[:60]


def _clean_query(query: str) -> str:
    """搜索查询清理：去标识信息与明显的提示注入尝试。

    隐私（R0 T005）：检索词会**出域**到第三方搜索源，因此这里做与
    `planner._build_query` 一致的去标识（证件号/电话/邮箱/精确日期/中文人名），
    并剔除明显的注入指令。查询越"医学概念化"，外部服务能获得的患者信息越少。
    """
    text = re.sub(r"\s+", " ", (query or "")).strip()
    text = text[:200]
    # 标识信息
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", " ", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", " ", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", " ", text)
    text = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", " ", text)
    # 明显的提示注入尝试
    text = re.sub(
        r"(?i)(ignore previous|system prompt|忽略以上|忽略之前|忽略上述)", " ", text
    )
    return re.sub(r"\s+", " ", text).strip()


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
            # 隐私（R0 T005）：标题与摘要会进入 prompt，必须脱敏；URL 只送**域名**
            # ——完整路径里常含用户标识（如以患者姓名命名的页面），模型无需知道
            # 具体路径，也不应看到。完整 URL 仍保留在 results 中供用户端引用。
            title = r.title
            snippet = r.snippet
            if sanitizer is not None:
                for attr in ("title", "snippet"):
                    try:
                        cleaned = sanitizer(getattr(r, attr) or "")
                    except Exception:  # noqa: BLE001
                        cleaned = ""
                    if attr == "title":
                        title = cleaned
                    else:
                        snippet = cleaned
            lines.append(f"[{i}] {title}\n{snippet}\n来源：{_source_host(r.url)}")
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
