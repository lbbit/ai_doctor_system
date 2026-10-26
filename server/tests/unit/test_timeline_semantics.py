"""时间语义与文档来源元数据（R1 T101）。

背景：长期档案里日期精度差异极大（"2024年"/"2024-03"/住院区间），
系统此前把精度散落各处，区间比较、时区与"上次/当时"锚点各自为政。

期望语义：
1. 六种精度（datetime/day/month/year/range/unknown）各有确定的区间表示；
2. month/year 与某日**不做**虚假精确先后比较（用可能区间相交）；
3. 未知时间不伪造为 1 月 1 日，且不进入限定时间窗；
4. 稳定排序：未知时间排在最后，分页不重不漏；
5. 文档级来源元数据（类型、发生时间、机构、科室）可存可查，
   **用户填写的日期不被重新抽取覆盖**；
6. 不同标本/方法的同名指标不得被趋势混并。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.common import timeline as tl


# --------------------------------------------------------------------------
# 精度与区间
# --------------------------------------------------------------------------


def test_day_span_is_half_open_day():
    span = tl.day_span(date(2026, 3, 12))
    assert span.precision == tl.DAY
    assert span.start is not None and span.end is not None
    assert (span.end - span.start).days == 1


def test_month_span_covers_whole_month():
    span = tl.month_span(2026, 2)
    assert span.precision == tl.MONTH
    assert span.start is not None and span.end is not None
    # 2026 不是闰年：2 月 28 天
    assert (span.end - span.start).days == 28


def test_month_span_handles_year_boundary():
    span = tl.month_span(2026, 12)
    assert span.end is not None and span.end.year == 2027


def test_year_span_covers_whole_year():
    span = tl.year_span(2026)
    assert span.precision == tl.YEAR
    assert span.start is not None and span.end is not None
    assert (span.end - span.start).days == 365


def test_range_span_requires_end_after_start():
    with pytest.raises(ValueError):
        tl.range_span(
            datetime(2026, 3, 12, tzinfo=timezone.utc),
            datetime(2026, 3, 1, tzinfo=timezone.utc),
        )


def test_unknown_span_has_no_dates():
    """未知时间不得伪造为 1 月 1 日。"""
    span = tl.unknown_span()
    assert span.start is None and span.end is None
    assert span.known is False
    assert span.precision == tl.UNKNOWN


def test_span_from_parts_selects_precision():
    assert tl.span_from_parts(year=2026, month=3, day=12).precision == tl.DAY
    assert tl.span_from_parts(year=2026, month=3).precision == tl.MONTH
    assert tl.span_from_parts(year=2026).precision == tl.YEAR
    assert tl.span_from_parts().precision == tl.UNKNOWN


def test_span_from_parts_range_requires_both_ends():
    with pytest.raises(ValueError):
        tl.span_from_parts(
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            precision=tl.RANGE,
        )


# --------------------------------------------------------------------------
# 比较：精度优先，不做虚假先后
# --------------------------------------------------------------------------


def test_month_and_day_compare_by_overlap_not_order():
    """"2024年3月" 与 "2024-03-12" 只能说相交，不能说谁在前。"""
    month = tl.month_span(2024, 3)
    day = tl.day_span(date(2024, 3, 12))
    assert month.overlaps(day) is True
    # 不做精确先后比较：不提供 < / > 语义
    other = tl.day_span(date(2024, 4, 1))
    assert month.overlaps(other) is False


def test_unknown_never_overlaps():
    assert tl.unknown_span().overlaps(tl.day_span(date(2024, 3, 12))) is False


def test_matches_window_excludes_unknown():
    """限定时间窗时，未知时间记录不静默进入结果。"""
    window = tl.filter_window(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 31, tzinfo=timezone.utc),
    )
    assert tl.matches_window(tl.day_span(date(2024, 3, 12)), window) is True
    assert tl.matches_window(tl.day_span(date(2023, 3, 12)), window) is False
    assert tl.matches_window(tl.unknown_span(), window) is False


def test_matches_window_without_bounds_includes_everything():
    window = tl.filter_window(None, None)
    assert tl.matches_window(tl.unknown_span(), window) is True
    assert tl.matches_window(tl.day_span(date(2024, 3, 12)), window) is True


# --------------------------------------------------------------------------
# 稳定排序
# --------------------------------------------------------------------------


def test_sort_key_puts_unknown_last():
    known = tl.sort_key(tl.day_span(date(2024, 3, 12)), "a")
    unknown = tl.sort_key(tl.unknown_span(), "b")
    assert known < unknown


def test_sort_key_is_stable_for_same_time():
    k1 = tl.sort_key(tl.day_span(date(2024, 3, 12)), "a")
    k2 = tl.sort_key(tl.day_span(date(2024, 3, 12)), "b")
    assert k1 < k2  # 以 id 兜底，保证分页稳定


def test_parse_precision_downgrades_invalid_to_unknown():
    assert tl.parse_precision("day") == tl.DAY
    assert tl.parse_precision("DAY") == tl.DAY
    assert tl.parse_precision("week") == tl.UNKNOWN
    assert tl.parse_precision(None) == tl.UNKNOWN
    assert tl.parse_precision("") == tl.UNKNOWN


def test_span_for_date_respects_precision():
    assert tl.span_for_date(date(2024, 3, 12), tl.MONTH).precision == tl.MONTH
    assert tl.span_for_date(date(2024, 3, 12), tl.YEAR).precision == tl.YEAR
    assert tl.span_for_date(date(2024, 3, 12), tl.UNKNOWN).known is False
    assert tl.span_for_date(None).known is False
