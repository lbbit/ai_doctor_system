"""Agent 工具轮规划器：决定是否需要外部资料。

刻意采用「**前置工具轮 + 单次生成**」而非自由 ReAct：
- 与现有 SSE 单流架构兼容，不需改写流式协议；
- 轮数、调用数、超时三条硬上限，成本与延迟可控；
- 规划失败（坏 JSON / 模型不可用 / 误判）一律降级为"不调工具"，
  绝不阻断问诊（AGENTS.md 第 4 条）。

规划输出严格要求 JSON，且**只接受白名单工具名**，避免模型幻觉出任意工具。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.agent.tools import ToolCall

logger = logging.getLogger("app.agent.planner")

ALLOWED_TOOLS = ("web_search",)

PLANNER_PROMPT = """你是医疗问诊系统的工具调度器。判断回答用户问题是否需要检索外部资料。

可调用工具：
- web_search：检索公开医学资料（指南、共识、药品说明书、百科）。参数 {"query": "检索关键词"}

规则：
1. 只回答用户自身档案/记录已足够的问题（如"我上次血压多少"）→ 不需要检索。
2. 涉及最新指南、标准参考值、药物通用信息、疾病科普，且档案中无依据 → 需要检索。
3. 检索关键词要具体（疾病名 + 主题），不要照抄用户原话。
4. 最多 2 次检索，宁少勿多。
5. **只输出 JSON**，不要解释。

输出格式：
{"need_search": true, "calls": [{"name": "web_search", "args": {"query": "高血压 成人 诊断标准"}}]}
或
{"need_search": false, "calls": []}"""


@dataclass
class Plan:
    need_search: bool = False
    calls: List[ToolCall] = field(default_factory=list)
    degraded: bool = False
    reason: str = ""

    @property
    def empty(self) -> bool:
        return not self.calls


def _extract_json(raw: str) -> Optional[dict]:
    """从模型输出中提取 JSON（容忍 ```json 包裹与前后噪声）。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def parse_plan(raw: str, max_calls: int = 3) -> Plan:
    """解析规划输出；任何异常都降级为"不调工具"。"""
    data = _extract_json(raw)
    if data is None:
        return Plan(need_search=False, calls=[], degraded=True, reason="bad_json")
    if not data.get("need_search"):
        return Plan(need_search=False, calls=[])

    calls: List[ToolCall] = []
    for item in data.get("calls") or []:
        if len(calls) >= max_calls:
            break
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name not in ALLOWED_TOOLS:
            logger.warning("agent.planner.reject_tool name=%s", name[:40])
            continue
        args = item.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        query = str(args.get("query") or "").strip()
        if not query:
            continue
        calls.append(ToolCall(name=name, args={"query": query[:200]}))
    return Plan(need_search=bool(calls), calls=calls)


def plan_tools(
    llm,
    question: str,
    *,
    max_calls: int = 3,
    enabled: bool = True,
) -> Plan:
    """调用 LLM 规划工具；禁用/不可用/失败均返回空计划。

    性能约束（§19）：实测规划调用用 chat 模型需 ~6s，是首字延迟最大瓶颈。
    规划本身是**低难度判断**，因此：
    1. 调用方应传入 ``extraction`` profile 的客户端（成本与延迟双降）；
    2. 提示词与输入尽量短（只给问题，不给档案上下文）；
    3. 命中明显规则时**完全跳过模型**（见 ``rule_based_plan``）。
    """
    if not enabled:
        return Plan(need_search=False, calls=[], reason="disabled")
    if llm is None or not getattr(llm, "available", lambda: False)():
        return Plan(need_search=False, calls=[], reason="llm_unavailable")
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": question[:200]},
            ],
            json_mode=True,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - 规划失败不得阻断问诊
        logger.warning("agent.planner.failed reason=%s", type(exc).__name__)
        return Plan(need_search=False, calls=[], degraded=True, reason="llm_error")
    return parse_plan(raw, max_calls=max_calls)


# 明显"必须查外部资料"的意图信号（指南/标准/通用知识类）
_NEED_SEARCH_SIGNALS = (
    "标准", "指南", "共识", "参考值", "参考范围", "正常值", "诊断标准",
    "最新", "推荐", "说明书", "适应症", "禁忌", "副作用", "不良反应",
    "怎么治", "如何治疗", "治疗原则", "什么病", "是什么原因", "病因",
)
# 明显"只问自己数据"的信号（无需外部检索）
_NO_SEARCH_SIGNALS = (
    "我上次", "我的记录", "我的档案", "我最近测", "历史", "趋势",
    "帮我记", "录入", "记一下", "我上传", "这份报告",
)


# 检索词质量：整句（含疑问词/标点）会让检索源返回 0 条（实测
# "高尿酸血症的饮食禁忌有哪些？" 直接查 → no_results）。
# 因此规则规划必须做**关键词提炼**，而不是把原句丢给搜索引擎。
_QUERY_STOPWORDS = (
    "有哪些", "是什么", "是多少", "为什么", "怎么办", "怎么样", "怎么", "如何",
    "什么", "哪些", "多少", "应该", "可以", "需要", "注意", "请问", "一下",
    "吗", "呢", "的", "？", "?", "了", "我", "要", "该", "是否", "能不能",
)
_MEDICAL_SUFFIXES = (
    "血症", "血压", "血糖", "血脂", "尿酸", "胆固醇", "甘油三酯",
    "糖尿病", "高血压", "高血脂", "贫血", "炎症", "感染", "结石",
)


def _build_query(question: str) -> str:
    """从自然语言问题中提炼检索关键词（确定性，可测试）。

    策略：
    1. 去掉疑问词/语气词/标点；
    2. 保留医学术语与核心名词；
    3. 长度受控（检索源对长查询支持差）。
    """
    text = (question or "").strip()
    for word in _QUERY_STOPWORDS:
        text = text.replace(word, " ")
    text = re.sub(r"[，。！？、；：“”‘’（）【】\s]+", " ", text)
    parts = [p for p in text.split() if len(p) >= 2]
    query = " ".join(parts).strip()
    if not query:
        query = question.strip()[:40]
    return query[:60]


def rule_based_plan(question: str) -> Optional[Plan]:
    """确定性规划：命中明确意图时**不调用模型**，直接给出结论。

    为什么放在模型前面（AGENTS.md 第 3/6 条）：能用确定性规则低代价收敛的，
    先跑规则，再交给 AI 处理剩余歧义。实测这一步可省掉 ~6s 首字延迟。

    返回 ``None`` 表示"规则无法判定"，交由模型处理。
    """
    q = (question or "").strip()
    if not q:
        return Plan(need_search=False, calls=[], reason="empty")
    # 先判"问自己的数据"：优先级最高（用户明显在问档案）
    if any(sig in q for sig in _NO_SEARCH_SIGNALS):
        return Plan(need_search=False, calls=[], reason="rule_self_data")
    if any(sig in q for sig in _NEED_SEARCH_SIGNALS):
        # 提取关键词而非整句：整句会让检索源返回 0 条
        query = _build_query(q)
        return Plan(
            need_search=True,
            calls=[ToolCall(name="web_search", args={"query": query})],
            reason="rule_needs_search",
        )
    return None
