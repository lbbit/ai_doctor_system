"""多解析器结果融合（表格确定性结果优先，视觉/OCR 补漏）。

实测数据（2026-10-21）：
- 2020-09.pdf：table 116 项 / 15 页（7.7 项/页，覆盖充分）
- 2021_0907.pdf：table 55 项 / 21 页（2.6 项/页，表格只覆盖部分页）
- 同日 VLM 逐页：127 项

单一解析器都有盲区：表格准确但可能漏页；视觉覆盖全但也可能漏读。
因此当表格覆盖不足时，继续用视觉识别，并把两边的指标按“名称+单位”合并，
同名字段保留表格（确定性、参考区间更完整），其余由视觉补齐。
"""

from typing import Dict, List, Optional, Tuple

from app.preprocessing.textnorm import normalize_text

# 每页至少多少项才认为表格覆盖充分（低于则继续跑视觉补充）
MIN_OBS_PER_PAGE = 3


def table_coverage_sufficient(
    observation_count: int, page_count: int, threshold: int = MIN_OBS_PER_PAGE
) -> bool:
    """表格结果是否足以单独作为抽取结果。"""
    if observation_count <= 0:
        return False
    pages = max(1, page_count)
    return observation_count >= pages * threshold


def _obs_key(obs: Dict) -> Tuple[str, str]:
    """同名指标判重键：名称 + 单位（不同单位是不同项目，如 mmol/L 与 g/L）。"""
    name = str(obs.get("raw_name") or "").strip()
    unit = str(obs.get("unit") or "").strip().lower()
    return name, unit


def _is_blank_value(value: object) -> bool:
    """数值是否"没读到"（None / 空串 / 纯空白）。0 和 0.0 算有值。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def merge_payloads(
    primary: Dict,
    secondary: Optional[Dict],
    *,
    primary_parser: str = "table",
    secondary_parser: str = "vlm",
) -> Dict:
    """合并两次解析结果：primary（确定性优先）在前，secondary 补漏。"""
    if not secondary:
        return dict(primary)

    merged_obs: List[Dict] = list(primary.get("observations") or [])
    index: Dict[Tuple[str, str], int] = {
        _obs_key(o): i for i, o in enumerate(merged_obs)
    }

    added = 0
    for obs in secondary.get("observations") or []:
        key = _obs_key(obs)
        if key in index:
            # 同名同单位：保留 primary，但补齐 primary 缺失的参考区间/标记
            target = merged_obs[index[key]]
            # 数值兜底：primary 没读到数值（如 VLM 漏读）而 secondary 读到了，
            # 必须用 secondary 补上——否则 vlm_first 下"视觉漏读"会让表格里
            # 本来正确的数值直接丢失（真实风险，非假设）。
            if _is_blank_value(target.get("value")) and not _is_blank_value(
                obs.get("value")
            ):
                target["value"] = obs["value"]
                target["value_filled_from"] = secondary_parser
            for field in ("ref_low", "ref_high", "ref_text"):
                if target.get(field) in (None, "") and obs.get(field) not in (None, ""):
                    target[field] = obs[field]
            if not target.get("abnormal_flag") and obs.get("abnormal_flag"):
                target["abnormal_flag"] = obs["abnormal_flag"]
            continue
        index[key] = len(merged_obs)
        merged_obs.append(obs)
        added += 1

    merged_events: List[Dict] = list(primary.get("events") or [])
    seen_events = {
        (e.get("type"), e.get("title"), e.get("occurred_at")) for e in merged_events
    }
    for ev in secondary.get("events") or []:
        key = (ev.get("type"), ev.get("title"), ev.get("occurred_at"))
        if key in seen_events:
            continue
        seen_events.add(key)
        merged_events.append(ev)

    return {
        "report_date": primary.get("report_date") or secondary.get("report_date"),
        "doc_type_suggest": (
            primary.get("doc_type_suggest") or secondary.get("doc_type_suggest") or ""
        ),
        "events": merged_events,
        "observations": merged_obs,
        "mock": bool(primary.get("mock") or secondary.get("mock")),
        "merged_from": [primary_parser, secondary_parser],
        "supplemented": added,
    }


def _dedupe_key(obs: Dict) -> Tuple[str, str, str]:
    """去重键：标准 code（优先）或归一化名称 + 单位 + 检查日期。

    - 用 code 可以把「体重指数 / 身体质量指数」这类别名归并；
    - 单位不同是不同项目（10^9/L 与 /uL）；
    - 日期不同是趋势数据点，绝不能合并。
    """
    code = str(obs.get("code") or "").strip()
    if not code:
        name = str(obs.get("raw_name") or obs.get("display_name") or "").strip()
        code = f"name:{normalize_text(name).replace(' ', '')}"
    unit = str(obs.get("unit") or "").strip().lower()
    date = str(obs.get("occurred_at") or "")
    return code, unit, date


def _richness(obs: Dict) -> Tuple[int, float]:
    """同一键下选择「信息更完整」的一条：有值 > 有参考区间 > 置信度高。"""
    has_value = 0 if obs.get("value") in (None, "") else 1
    has_ref = 1 if (
        obs.get("ref_low") is not None
        or obs.get("ref_high") is not None
        or obs.get("ref_text")
    ) else 0
    try:
        conf = float(obs.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return has_value * 2 + has_ref, conf


def dedupe_observations(observations: List[Dict]) -> Tuple[List[Dict], int]:
    """同一指标多条时保留最完整的一条，返回 (结果, 丢弃条数)。

    真实缺陷（2021_0907.pdf）：融合后 144 项里有 11 组重复，用户在确认卡片
    会看到「体重指数 36」「体重指数 32.4」两个互相矛盾的条目。
    """
    best: Dict[Tuple[str, str, str], Dict] = {}
    order: List[Tuple[str, str, str]] = []
    dropped = 0

    for obs in observations:
        key = _dedupe_key(obs)
        if key not in best:
            best[key] = obs
            order.append(key)
            continue
        dropped += 1
        if _richness(obs) > _richness(best[key]):
            best[key] = obs

    return [best[key] for key in order], dropped
