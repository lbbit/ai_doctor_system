import { describe, expect, it } from 'vitest';
import {
  bulkConfirmRefs,
  confirmStats,
  filterObservations,
  needsAttention,
} from '../src/components/confirmBulk';
import type { DerivedRecord, ObsDraft } from '../src/types/api';

function obs(patch: Partial<ObsDraft>): ObsDraft {
  return {
    raw_name: 'x',
    value: 1,
    unit: null,
    ref_low: null,
    ref_high: null,
    ref_text: null,
    abnormal_flag: 'N',
    occurred_at: '2026-03-12',
    confidence: 0.95,
    bbox: null,
    status: 'proposed',
    code: null,
    dictionary_id: null,
    display_name: 'x',
    ...patch,
  };
}

const record: DerivedRecord = {
  id: 'dr-1',
  version: 1,
  parser: 'vlm',
  confidenceSummary: null,
  payload: {
    report_date: '2026-03-12',
    doc_type_suggest: 'lab_report',
    events: [],
    observations: [
      obs({ raw_name: '白细胞计数', confidence: 0.95, abnormal_flag: 'N' }),
      obs({ raw_name: '血小板计数', confidence: 0.95, abnormal_flag: 'L' }),
      obs({ raw_name: '矫正视力', confidence: 0.5, abnormal_flag: null }),
      obs({ raw_name: '身高', confidence: 0.95, abnormal_flag: null }),
    ],
  },
};

describe('批量确认高置信正常项（PRD AC-6.1 高置信可快速通过）', () => {
  it('异常或低置信项必须人工确认', () => {
    expect(needsAttention(obs({ confidence: 0.95, abnormal_flag: 'H' }))).toBe(true);
    expect(needsAttention(obs({ confidence: 0.5, abnormal_flag: 'N' }))).toBe(true);
    expect(needsAttention(obs({ confidence: 0.95, abnormal_flag: 'N' }))).toBe(false);
    expect(needsAttention(obs({ confidence: 0.95, abnormal_flag: null }))).toBe(false);
  });

  it('批量确认只覆盖高置信且非异常项', () => {
    expect(bulkConfirmRefs(record)).toEqual(['obs:0', 'obs:3']);
  });

  it('统计需人工确认数量（避免用户被 100+ 条淹没）', () => {
    const stats = confirmStats(record);
    expect(stats.total).toBe(4);
    expect(stats.attention).toBe(2);
    expect(stats.quick).toBe(2);
  });

  it('筛选“只看需确认”只保留异常/低置信项，且索引保持原位置', () => {
    const filtered = filterObservations(record.payload.observations, 'attention');
    expect(filtered.map((f) => f.index)).toEqual([1, 2]);
    expect(filtered.map((f) => f.obs.raw_name)).toEqual(['血小板计数', '矫正视力']);
    expect(filterObservations(record.payload.observations, 'all')).toHaveLength(4);
  });
});
