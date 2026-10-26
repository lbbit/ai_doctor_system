import { describe, expect, it } from 'vitest';
import type { DerivedRecord, Decision } from '../src/types/api';

/** 与 ConfirmCard 决策构建逻辑一致（确认前完整性校验）。 */
function buildDecisions(
  record: DerivedRecord,
  decided: Record<string, 'confirm' | 'correct' | 'reject'>,
  values: Record<string, Record<string, unknown>> = {},
): { decisions: Decision[]; missing: string[] } {
  const refs = [
    ...record.payload.events.map((_, i) => `event:${i}`),
    ...record.payload.observations.map((_, i) => `obs:${i}`),
  ];
  const decisions: Decision[] = [];
  const missing: string[] = [];
  for (const ref of refs) {
    const action = decided[ref];
    if (!action) {
      missing.push(ref);
      continue;
    }
    const decision: Decision = { fieldRef: ref, action };
    if (action === 'correct' && values[ref]) {
      decision.value = values[ref];
    }
    decisions.push(decision);
  }
  return { decisions, missing };
}

const record: DerivedRecord = {
  id: 'dr-1',
  version: 2,
  parser: 'rule',
  confidenceSummary: null,
  payload: {
    report_date: '2026-03-12',
    doc_type_suggest: 'lab_report',
    events: [
      { type: 'checkup', title: '体检', narrative: null, diagnosis: null, department: null, occurred_at: '2026-03-12', confidence: 0.9 },
    ],
    observations: [
      { raw_name: '血糖', display_name: '空腹血糖', code: 'GLU_FASTING', dictionary_id: 'd1', value: 7.2, unit: 'mmol/L', ref_low: 3.9, ref_high: 6.1, ref_text: null, abnormal_flag: 'H', occurred_at: '2026-03-12', confidence: 0.9, bbox: null, status: 'proposed' },
      { raw_name: '神秘指标', display_name: '神秘指标', code: null, dictionary_id: null, value: '阴性', unit: null, ref_low: null, ref_high: null, ref_text: '阴性', abnormal_flag: null, occurred_at: '2026-03-12', confidence: 0.4, bbox: null, status: 'proposed' },
    ],
  },
};

describe('HITL 确认流程（F-06）', () => {
  it('未决策字段进入 missing（P0 全部强制）', () => {
    const { missing } = buildDecisions(record, { 'event:0': 'confirm' });
    expect(missing).toEqual(['obs:0', 'obs:1']);
  });

  it('全部决策后无 missing，correct 携带编辑值', () => {
    const { decisions, missing } = buildDecisions(
      record,
      { 'event:0': 'confirm', 'obs:0': 'correct', 'obs:1': 'reject' },
      { 'obs:0': { value: 6.8, abnormal_flag: 'H' } },
    );
    expect(missing).toEqual([]);
    expect(decisions).toHaveLength(3);
    const corrected = decisions.find((d) => d.fieldRef === 'obs:0');
    expect(corrected?.action).toBe('correct');
    expect(corrected?.value).toEqual({ value: 6.8, abnormal_flag: 'H' });
    const rejected = decisions.find((d) => d.fieldRef === 'obs:1');
    expect(rejected?.action).toBe('reject');
    expect(rejected?.value).toBeUndefined();
  });

  it('低置信字段判定（AC-6.1 阈值 0.85）', () => {
    const threshold = 0.85;
    const low = record.payload.observations.filter(
      (o) => (o.confidence ?? 0) < threshold,
    );
    expect(low.map((o) => o.raw_name)).toEqual(['神秘指标']);
  });
});
