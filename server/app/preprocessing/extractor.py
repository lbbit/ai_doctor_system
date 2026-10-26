"""VLM/规则抽取结果的 JSON schema 校验与归一化（F-05 AC-5.1）。

手写轻量校验（不引入 jsonschema 依赖）：结构、类型、confidence 钳制。
"""

import logging
import re
from typing import Any, Optional

from app.llm.extraction import load_schema

logger = logging.getLogger("app.extractor")

_EVENT_TYPES = {
    "visit", "hospitalization", "surgery", "checkup",
    "medication", "vaccine", "self_reported", "other",
}
_FLAGS = {"N", "L", "H", "A"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clamp_conf(value: Any, default: float = 0.5) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _opt_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if m:
            try:
                return float(m.group())
            except ValueError:
                return None
    return None


def _opt_date(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()[:10]
    if _DATE_RE.match(value):
        try:
            import datetime as _dt

            _dt.date.fromisoformat(value)
            return value
        except ValueError:
            return None
    # 容忍 2026/03/12
    m = re.match(r"^(\d{4})[/年.](\d{1,2})[/月.](\d{1,2})", str(value).strip())
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _norm_obs(raw: dict) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    raw_name = str(raw.get("raw_name") or raw.get("name") or "").strip()
    if not raw_name:
        return None
    value = raw.get("value")
    if isinstance(value, str):
        value = value.strip() or None
    flag = raw.get("abnormal_flag")
    if flag is not None and str(flag).upper() in _FLAGS:
        flag = str(flag).upper()
    elif flag is not None:
        flag = None
    bbox = raw.get("bbox")
    if not isinstance(bbox, dict):
        bbox = None
    return {
        "raw_name": raw_name[:128],
        "value": value,
        "unit": (str(raw.get("unit")).strip() or None) if raw.get("unit") else None,
        "ref_low": _opt_float(raw.get("ref_low")),
        "ref_high": _opt_float(raw.get("ref_high")),
        "ref_text": str(raw.get("ref_text")).strip() or None if raw.get("ref_text") else None,
        "abnormal_flag": flag,
        "occurred_at": _opt_date(raw.get("occurred_at")),
        "confidence": _clamp_conf(raw.get("confidence")),
        "bbox": bbox,
    }


def _norm_event(raw: dict) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    etype = str(raw.get("type") or "other").strip()
    if etype not in _EVENT_TYPES:
        etype = "other"
    title = str(raw.get("title") or "").strip() or None
    narrative = str(raw.get("narrative") or "").strip() or None
    if not title and not narrative:
        return None
    return {
        "type": etype,
        "title": title or "（未命名事件）",
        "narrative": narrative,
        "diagnosis": str(raw.get("diagnosis") or "").strip() or None,
        "department": str(raw.get("department") or "").strip() or None,
        "occurred_at": _opt_date(raw.get("occurred_at")),
        "confidence": _clamp_conf(raw.get("confidence"), 0.6),
    }


def validate_derived_record(raw: Any, doc_type: str = "") -> dict:
    """校验 + 归一化抽取结果为 DerivedRecord.payload 结构。

    结构错误抛 ValueError（调用方降级到下一解析器/failed）。
    """
    if not isinstance(raw, dict):
        raise ValueError("抽取结果不是 JSON 对象")
    # schema 存在性自检（提示词与代码同步）
    _ = load_schema("derived_record.schema.json")

    observations = []
    for item in raw.get("observations") or []:
        obs = _norm_obs(item)
        if obs:
            obs["status"] = "proposed"  # 字段级状态：proposed → confirmed|corrected|rejected
            obs["code"] = None
            obs["dictionary_id"] = None
            obs["display_name"] = obs["raw_name"]
            observations.append(obs)

    events = []
    for item in raw.get("events") or []:
        ev = _norm_event(item)
        if ev:
            events.append(ev)

    payload = {
        "report_date": _opt_date(raw.get("report_date")),
        "doc_type_suggest": str(raw.get("doc_type_suggest") or "").strip() or doc_type,
        "events": events,
        "observations": observations,
        "mock": bool(raw.get("mock", False)),
    }
    return payload
