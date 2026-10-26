/**
 * 发生时间展示（R1 T101）。
 *
 * 关键语义：长期档案以**发生时间**为主线，而非上传时间；
 * 时间精度不同则展示不同，且"未知"不得显示成 1 月 1 日。
 */
import { describe, expect, it } from 'vitest';
import { formatOccurredAt, TIME_PRECISION_LABELS } from '../src/types/api';

describe('发生时间展示', () => {
  it('按日精度显示日期', () => {
    expect(
      formatOccurredAt({
        occurredStart: '2026-03-12T00:00:00+08:00',
        timePrecision: 'day',
      }),
    ).toBe('2026-03-12');
  });

  it('按月精度只显示到月', () => {
    expect(
      formatOccurredAt({
        occurredStart: '2026-03-01T00:00:00+08:00',
        timePrecision: 'month',
      }),
    ).toBe('2026-03');
  });

  it('按年精度只显示到年', () => {
    expect(
      formatOccurredAt({
        occurredStart: '2026-01-01T00:00:00+08:00',
        timePrecision: 'year',
      }),
    ).toBe('2026 年');
  });

  it('区间精度显示起止', () => {
    expect(
      formatOccurredAt({
        occurredStart: '2026-01-05T00:00:00+08:00',
        occurredEnd: '2026-01-12T00:00:00+08:00',
        timePrecision: 'range',
      }),
    ).toBe('2026-01-05 ~ 2026-01-12');
  });

  it('未知时间显示"时间未知"，不得显示成 1 月 1 日', () => {
    expect(formatOccurredAt({ timePrecision: 'unknown' })).toBe('时间未知');
    expect(
      formatOccurredAt({ occurredStart: null, timePrecision: 'unknown' }),
    ).toBe('时间未知');
    // 关键：不得出现 0001-01-01 之类的假日期
    expect(formatOccurredAt({ timePrecision: 'unknown' })).not.toContain('0001');
  });

  it('缺 occurredStart 时也按未知处理', () => {
    expect(formatOccurredAt({ timePrecision: 'day' })).toBe('时间未知');
  });

  it('精度文案齐全', () => {
    expect(TIME_PRECISION_LABELS.unknown).toBe('时间未知');
    expect(TIME_PRECISION_LABELS.range).toBe('区间');
  });
});
