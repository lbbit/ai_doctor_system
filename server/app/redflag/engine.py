"""红旗规则匹配引擎（F-11/A-1；§6.3）：keyword + context_logic 树 + obs 确定性条件。

命中由规则引擎判定，**不交给模型**；obs 条件从 L1 观测序列取确定性数值（A-6）。
"""

import logging
import re
from typing import Any, Dict, List, Optional

from app.db.models import RedFlagRule

logger = logging.getLogger("app.redflag")


def _kw_match(kw: str, text: str) -> bool:
    """关键词匹配：子串优先，正则兜底（非法正则按子串）。"""
    if not kw:
        return False
    if kw in text:
        return True
    if re.search(r"[.^$*+?{}\[\]|\\]", kw):
        try:
            return bool(re.search(kw, text, re.IGNORECASE))
        except re.error:
            return False
    return False


def _eval_condition(node: Any, text: str, obs_values: Dict[str, float]) -> bool:
    """条件树求值：{"all":[...]} / {"any":[...]} / {"kw": "..."} / {"obs": {...}}。"""
    if node is None:
        return True
    if isinstance(node, list):
        return all(_eval_condition(n, text, obs_values) for n in node)
    if not isinstance(node, dict):
        return False
    if "all" in node:
        return all(_eval_condition(n, text, obs_values) for n in (node["all"] or []))
    if "any" in node:
        return any(_eval_condition(n, text, obs_values) for n in (node["any"] or []))
    if "kw" in node:
        return _kw_match(str(node["kw"]), text)
    if "obs" in node:
        cond = node["obs"] or {}
        code = cond.get("code")
        if code is None or code not in obs_values:
            return False
        value = obs_values[code]
        if value is None:
            return False
        ok = True
        if "gte" in cond:
            ok = ok and value >= float(cond["gte"])
        if "lte" in cond:
            ok = ok and value <= float(cond["lte"])
        if "gt" in cond:
            ok = ok and value > float(cond["gt"])
        if "lt" in cond:
            ok = ok and value < float(cond["lt"])
        if "eq" in cond:
            ok = ok and value == float(cond["eq"])
        return ok
    return False


def match_rules(
    rules: List[RedFlagRule],
    text: str,
    obs_values: Optional[Dict[str, float]] = None,
) -> List[dict]:
    """对文本跑全部启用规则，返回命中列表。

    判定语义（§6.3）：
    - 有 context_logic → 按条件树判定（树内 kw/obs 为叶子）；
      仅当树中无 kw 叶子时叠加 keyword 层。
    - 无 context_logic → keyword_logic any/all。
    """
    obs_values = obs_values or {}
    hits: List[dict] = []
    if not text:
        return hits

    for rule in rules:
        if not rule.enabled:
            continue
        keywords = list(rule.keywords or [])
        matched_terms = [kw for kw in keywords if _kw_match(kw, text)]

        ctx = rule.context_logic
        if ctx:
            tree_ok = _eval_condition(ctx, text, obs_values)
            tree_has_kw = "kw" in _flatten_keys(ctx)
            # 树判定为主；树不含 kw 叶子时叠加 keyword 层（避免仅凭数值误报）
            matched = tree_ok and (bool(matched_terms) or tree_has_kw)
        else:
            if rule.keyword_logic == "all":
                matched = bool(keywords) and len(matched_terms) == len(keywords)
            else:
                matched = bool(matched_terms)

        if matched:
            hits.append(
                {
                    "code": rule.code,
                    "name": rule.name,
                    "severity": rule.severity,
                    "message": rule.message,
                    "action": rule.action,
                    "matchedTerms": matched_terms or keywords[:1],
                    "ruleId": rule.id,
                    "version": rule.version,
                }
            )
    return hits


def _flatten_keys(node: Any) -> set:
    keys: set = set()
    if isinstance(node, dict):
        keys.update(node.keys())
        for v in node.values():
            keys |= _flatten_keys(v)
    elif isinstance(node, list):
        for v in node:
            keys |= _flatten_keys(v)
    return keys


def extract_obs_values(text_obs: Optional[Dict[str, float]]) -> Dict[str, float]:
    """L1 序列 → {code: 最新数值}（A-6：确定性数值，不问模型）。"""
    return {k: float(v) for k, v in (text_obs or {}).items() if v is not None}
