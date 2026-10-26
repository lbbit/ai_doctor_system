"""时间语义单一权威（R1 T101）。

背景：长期健康档案里"什么时候发生"经常只有精度不同的答案（"2024年"、
"2024-03"、某次住院的区间），而系统此前把精度散落在各处
（`occurred_at_precision` 只有 day/month/year/unknown，缺 datetime/range），
区间比较、时区、"上次/当时"的锚点语义各自为政，容易产生"同一条记录
在不同查询里落在不同时间窗"的不一致。

本模块定义**唯一**的时间语义：

| 精度 | 内部表示 | 区间 | 比较规则 |
|---|---|---|---|
| `datetime` | 带时区时刻（UTC 存储，保留原始时区） | [t, t] | 精确到秒 |
| `day` | 当地某日的 00:00 | [start, start+1d) | 按当地日 |
| `month` | 当地自然月 | [月初, 次月初) | 与某日**不**做精确先后比较 |
| `year` | 当地自然年 | [年初, 次年初) | 同上 |
| `range` | 明确区间（住院、疗程） | [start, end) | start<end 必填 |
| `unknown` | 无 | 空 | 不进入限定时间窗；单独置"时间未知" |

关键规则：
1. **精度优先于数值**：比较两个区间用"可能区间相交"，不伪造精确先后。
2. **未知不伪造**：没有日期就是 unknown，**不填 1 月 1 日**。
3. **稳定排序**：排序键 = (start, end, id)，保证分页不重不漏。
4. **同一函数**被写入、查询、锚点解析共用，避免"各写各的"。

术语：
- 发生时间 = 医疗事实发生/检查时间（`occurred_*`）
- 报告时间 = 报告签发时间（`reported_at`）
- 录入时间 = 系统写入时间（`recorded_at`）
三者不可互相覆盖。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# ---- 精度枚举 ----
DATETIME = "datetime"
DAY = "day"
MONTH = "month"
YEAR = "year"
RANGE = "range"
UNKNOWN = "unknown"

PRECISIONS = (DATETIME, DAY, MONTH, YEAR, RANGE, UNKNOWN)


@dataclass(frozen=True)
class TimeSpan:
    """一个带精度的时间区间（半开区间 [start, end)）。"""

    start: Optional[datetime]
    end: Optional[datetime]
    precision: str = UNKNOWN
    timezone_name: str = "Asia/Shanghai"

    @property
    def known(self) -> bool:
        return self.start is not None and self.end is not None

    def overlaps(self, other: "TimeSpan") -> bool:
        """两个可能区间是否相交（用于 month/year 与某日比较）。"""
        if not self.known or not other.known:
            return False
        return self.start < other.end and other.start < self.end

    def contains(self, moment: datetime) -> bool:
        if not self.known:
            return False
        return self.start <= moment < self.end


def _local(
    year: int, month: int, day: int, tz_name: str = "Asia/Shanghai"
) -> datetime:
    """构造带时区的本地时刻（避免 naive/aware 混用引发比较异常）。"""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - 无 tzdata 时退化为固定 +08:00
        tz = timezone(timedelta(hours=8))
    return datetime(year, month, day, tzinfo=tz)


def day_span(d: date, tz_name: str = "Asia/Shanghai") -> TimeSpan:
    """某一天 → [当日 00:00, 次日 00:00)。"""
    start = _local(d.year, d.month, d.day, tz_name)
    return TimeSpan(start, start + timedelta(days=1), DAY, tz_name)


def month_span(year: int, month: int, tz_name: str = "Asia/Shanghai") -> TimeSpan:
    """某个自然月 → [月初, 次月初)。"""
    start = _local(year, month, 1, tz_name)
    last = calendar.monthrange(year, month)[1]
    end = _local(year + (month == 12), 1 if month == 12 else month + 1, 1, tz_name)
    return TimeSpan(start, end, MONTH, tz_name)


def year_span(year: int, tz_name: str = "Asia/Shanghai") -> TimeSpan:
    """某个自然年 → [年初, 次年初)。"""
    start = _local(year, 1, 1, tz_name)
    end = _local(year + 1, 1, 1, tz_name)
    return TimeSpan(start, end, YEAR, tz_name)


def range_span(
    start: datetime, end: datetime, tz_name: str = "Asia/Shanghai"
) -> TimeSpan:
    """明确区间（住院/疗程）→ [start, end)；start 必须早于 end。"""
    if end <= start:
        raise ValueError("range 的 end 必须晚于 start")
    return TimeSpan(start, end, RANGE, tz_name)


def datetime_span(moment: datetime, tz_name: str = "Asia/Shanghai") -> TimeSpan:
    """精确时刻 → [t, t+1s)。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_local(2000, 1, 1, tz_name).tzinfo)
    return TimeSpan(moment, moment + timedelta(seconds=1), DATETIME, tz_name)


def unknown_span() -> TimeSpan:
    """未知时间：不得伪造为 1 月 1 日。"""
    return TimeSpan(None, None, UNKNOWN)


def span_from_parts(
    *,
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    precision: Optional[str] = None,
    tz_name: str = "Asia/Shanghai",
) -> TimeSpan:
    """按精度构造区间（校验精度与必填项的一致性）。

    规则：
    - 显式给出 start/end → range（要求 start<end）；
    - 给出 year+month+day → day；
    - 给出 year+month → month；
    - 只给 year → year；
    - 都没有 → unknown。
    """
    if precision == RANGE or (start is not None and end is not None):
        if start is None or end is None:
            raise ValueError("range 精度必须同时给出 start 与 end")
        return range_span(start, end, tz_name)
    if year and month and day:
        return day_span(date(year, month, day), tz_name)
    if year and month:
        return month_span(year, month, tz_name)
    if year:
        return year_span(year, tz_name)
    return unknown_span()


def span_for_date(
    d: Optional[date], precision: str = DAY, tz_name: str = "Asia/Shanghai"
) -> TimeSpan:
    """已有 `occurred_at` 日期 + 精度 → 区间。"""
    if d is None:
        return unknown_span()
    if precision == MONTH:
        return month_span(d.year, d.month, tz_name)
    if precision == YEAR:
        return year_span(d.year, tz_name)
    if precision == UNKNOWN:
        return unknown_span()
    return day_span(d, tz_name)


def filter_window(
    start: Optional[datetime],
    end: Optional[datetime],
    tz_name: str = "Asia/Shanghai",
) -> TimeSpan:
    """查询时间窗 → 区间（半开）。"""
    if start is None and end is None:
        return unknown_span()
    if start is None:
        start = _local(1, 1, 1, tz_name)
    if end is None:
        end = _local(9999, 12, 31, tz_name)
    return TimeSpan(start, end, RANGE, tz_name)


def matches_window(span: TimeSpan, window: TimeSpan) -> bool:
    """记录是否落在查询窗内。

    规则：未知时间的记录**不进入**限定时间窗（不静默包含或排除），
    由调用方用"时间未知"分组单独呈现。
    """
    if not window.known:
        return True
    if not span.known:
        return False
    return span.overlaps(window)


def sort_key(span: TimeSpan, record_id: str) -> tuple:
    """稳定排序键：未知时间排在最后（升序）。

    降序排序时把未知项反转到末尾，用 `reverse_unknown=True` 实现。
    """
    if not span.known:
        return (1, 0, 0, record_id)
    return (
        0,
        span.start.timestamp(),
        span.end.timestamp(),
        record_id,
    )


def parse_precision(value: Optional[str]) -> str:
    """归一化精度字符串；非法值一律降级为 unknown（不得静默当成 day）。"""
    if not value:
        return UNKNOWN
    v = str(value).strip().lower()
    return v if v in PRECISIONS else UNKNOWN


def to_utc(moment: Optional[datetime]) -> Optional[datetime]:
    """转 UTC 存储（naive 视为本地 +08:00）。"""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_local(2000, 1, 1).tzinfo)
    return moment.astimezone(timezone.utc)
