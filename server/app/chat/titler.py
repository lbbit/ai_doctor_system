"""会话标题生成（首轮问答后用 LLM 概述，便于历史会话检索）。

设计约束（AGENTS.md AI 底座原则）：
- **成本优先**：使用 ``extraction`` profile，仅首轮调用 1 次，不随轮次增长；
- **失败可降级**：任何异常/坏输出都返回 ``None``，由调用方保留原截断标题，
  绝不让标题生成阻塞或破坏问诊主流程；
- **确定性清洗**：长度、字符、标点由代码约束，不交给模型保证；
- **幂等**：调用方在标题已被语义化后不再调用（见 orchestration）。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("app.chat.titler")

MIN_TITLE_CHARS = 2
MAX_TITLE_CHARS = 24

TITLER_PROMPT = """你是医疗问诊系统的会话标题生成器。根据用户提问与助手回答，生成一个极短的会话标题，便于用户日后在列表中检索。

规则：
1. 6–14 个汉字（中文），不含标点、引号、书名号、表情。
2. 必须点明**主题对象 + 意图**，例如「血脂异常咨询」「儿童发热处理」「降压药副作用」「体检报告解读」。
3. 不要出现「咨询」「问题」「对话」「会话」「助手」等空洞词，除非确实无法概括。
4. 不要复述整句提问，不要包含人名、医院名、具体数值。
5. **只输出标题本身**，不要引号、不要解释、不要前后缀。"""

# 模型偶尔会带引号/前缀/换行，统一清洗
_STRIP_PREFIX = re.compile(
    r"^\s*(标题|title|会话标题|输出)\s*[:：]\s*", flags=re.I
)
_PUNCT = re.compile(
    r"[，。、；：！？,.;:!?\"'“”‘’《》（）()\[\]【】<>…—\-_/\\|~`]+"
)
_EMPTY_WORDS = (
    "咨询",
    "问题",
    "对话",
    "会话",
    "助手",
    "问答",
)


def clean_title(raw: str) -> Optional[str]:
    """清洗模型输出：去前缀/引号/标点/换行，约束长度。

    返回 ``None`` 表示不可用（空、纯标点、只剩空洞词）。
    """
    if not raw or not isinstance(raw, str):
        return None
    # 只取第一行：模型偶尔会输出说明
    text = raw.strip().splitlines()[0] if raw.strip() else ""
    text = _STRIP_PREFIX.sub("", text).strip()
    text = text.strip("\"'“”‘’《》【】[]()（） ")
    text = _PUNCT.sub("", text)
    text = re.sub(r"\s+", "", text)
    if not text:
        return None
    # 只剩空洞词 → 无信息量
    if text in _EMPTY_WORDS:
        return None
    if len(text) < MIN_TITLE_CHARS:
        return None
    return text[:MAX_TITLE_CHARS]


def _sanitize_for_outbound(text: str, sanitizer=None) -> str:
    """出域前脱敏（R0 T005）；脱敏失败返回空串，绝不退回原文。"""
    if not text:
        return ""
    if sanitizer is not None:
        try:
            return sanitizer(text) or ""
        except Exception:  # noqa: BLE001
            logger.warning("title.sanitize_failed")
            return ""
    try:
        from app.sanitizer.patterns import sanitize_text

        return sanitize_text(text)[0]
    except Exception:  # noqa: BLE001
        logger.warning("title.sanitize_fallback_failed")
        return ""


def generate_title(
    llm,
    question: str,
    answer: str,
    *,
    enabled: bool = True,
    sanitizer=None,
) -> Optional[str]:
    """调用 LLM 生成短标题；任何失败返回 ``None``（调用方保留原值）。

    参数 ``llm`` 为 ``LLMClient`` 或测试替身；``answer`` 只取前 500 字，
    避免把长回答整段送模型（成本与延迟）。

    隐私（R0 T005）：标题生成是**出域**调用，提问与回答都含用户身份信息，
    必须先脱敏。未提供 ``sanitizer`` 时使用通用模式脱敏；脱敏失败返回
    ``None``（不退回原文），宁可没有标题也不外泄标识信息。
    """
    if not enabled:
        return None
    if llm is None or not getattr(llm, "available", lambda: False)():
        return None
    q = (question or "").strip()
    if not q:
        return None
    a = (answer or "").strip()[:500]

    safe_q = _sanitize_for_outbound(q, sanitizer)
    safe_a = _sanitize_for_outbound(a, sanitizer)
    if not safe_q and not safe_a:
        return None

    try:
        raw = llm.chat(
            [
                {"role": "system", "content": TITLER_PROMPT},
                {
                    "role": "user",
                    "content": f"用户提问：{safe_q[:300]}\n\n助手回答：{safe_a}",
                },
            ],
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 - 标题失败不得影响问诊
        logger.warning("title.generate_failed reason=%s", type(exc).__name__)
        return None
    title = clean_title(raw)
    if title is None:
        logger.warning("title.unusable rawLen=%s", len(raw or ""))
    return title
