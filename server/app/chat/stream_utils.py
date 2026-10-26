"""流式引用缓冲（§19 / docs/superpowers/plans/2026-10-22-streaming.md）。

问题：`extract_citations` 需要完整文本才能判断"清单外引用"，但流式期间
不能等全文——否则等于不流式。若先把原文发出去、之后再剥除，会导致
**已展示内容被删改**（用户可见跳变，且不诚实）。

方案：只对 `[...]` 片段做缓冲。
- 普通文本立即放行；
- 遇到 `[` 进入缓冲，直到 `]` 或超长上限后再判定（保留 or 剥除），
  判定结果一次性放行。

保证不变量：**已放行内容永不改动**。
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger("app.chat.stream_utils")

# 超过该长度仍未闭合的 '[' 视为普通文本（防止缓冲无界增长）
MAX_CITE_BUFFER = 200

# 与 citations._CITE_RE 保持一致（编号可选，全半角冒号兼容）
_CITE_RE = re.compile(r"^\[依据\s*(\d*)\s*[:：]\s*([^\]]{1,200})\]$")


class CitationBuffer:
    """流式引用标记缓冲器。

    用法::

        buf = CitationBuffer(pool_labels={"2021 化验单"})
        for token in stream:
            for chunk in buf.feed(token):
                send(chunk)          # 这些内容可以安全展示
        for chunk in buf.flush():
            send(chunk)

    ``pool_labels`` 为允许的引用标签集合；为 ``None`` 时**全部保留**
    （用于测试与"无引用清单"场景，避免误删）。
    """

    def __init__(self, pool_labels: Optional[set[str]] = None) -> None:
        self._pool = pool_labels
        self._pending = ""          # 正在缓冲的 '[...]' 片段（不含已放行部分）
        self._in_cite = False
        self.stripped: List[str] = []   # 被剥除的标签（供日志/测试）

    # ---------------------------------------------------------------- 内部

    def _label_allowed(self, label: str) -> bool:
        if self._pool is None:
            return True
        if label in self._pool:
            return True
        # 与 extract_citations 的兜底一致：前缀/包含匹配
        return any(
            known in label or label in known for known in self._pool
        )

    def _judge(self, raw: str) -> str:
        """判定一个完整引用标记：保留原样或剥除为空。"""
        match = _CITE_RE.match(raw)
        if match is None:
            # 不是合法引用标记（例如 [1] 或 [备注]），按普通文本保留
            return raw
        label = match.group(2).strip()
        if self._label_allowed(label):
            return raw
        self.stripped.append(label)
        logger.info("citation.stripped label=%s", label[:80])
        return ""

    # ---------------------------------------------------------------- 对外

    def feed(self, token: str) -> List[str]:
        """喂入一个增量片段，返回**当前可安全展示**的片段列表。"""
        if not token:
            return []
        out: List[str] = []

        for ch in token:
            if not self._in_cite:
                if ch == "[":
                    self._in_cite = True
                    self._pending = "["
                else:
                    # 连续普通字符合并，减少下游渲染次数
                    if out and not out[-1].startswith("[") and len(out[-1]) < 200:
                        out[-1] += ch
                    else:
                        out.append(ch)
                continue

            # 缓冲模式
            self._pending += ch
            if ch == "]":
                judged = self._judge(self._pending)
                self._pending = ""
                self._in_cite = False
                if judged:
                    out.append(judged)
                continue
            if ch == "[":
                # 嵌套 '['：前一段没有闭合，视为普通文本放行，重新开始缓冲
                head = self._pending[:-1]
                if head:
                    out.append(head)
                self._pending = "["
                continue
            if len(self._pending) > MAX_CITE_BUFFER:
                # 超长未闭合：判定为普通文本，全部放行
                out.append(self._pending)
                self._pending = ""
                self._in_cite = False

        return out

    def flush(self) -> List[str]:
        """流结束：把残留缓冲一次性放行（未闭合的 '[' 按普通文本处理）。"""
        if not self._pending:
            return []
        rest = self._pending
        self._pending = ""
        self._in_cite = False
        return [rest] if rest else []

    @property
    def pending(self) -> str:
        return self._pending


def iter_stream_chunks(
    tokens: "object",
    pool_labels: Optional[set[str]] = None,
) -> Tuple[List[str], List[str]]:
    """便利函数：消费 token 迭代器，返回 (可展示片段列表, 被剥除标签列表)。

    主要用于测试与不需要实时回调的场景。
    """
    buf = CitationBuffer(pool_labels)
    chunks: List[str] = []
    for token in tokens:  # type: ignore[union-attr]
        chunks.extend(buf.feed(token))
    chunks.extend(buf.flush())
    return chunks, buf.stripped
