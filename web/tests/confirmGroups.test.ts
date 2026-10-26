import { describe, expect, it } from 'vitest';
import {
  bulkRejectRefs,
  groupObservations,
  groupSummary,
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
    abnormal_flag: null,
    occurred_at: '2026-03-12',
    confidence: 0.95,
    bbox: null,
    status: 'proposed',
    code: 'X',
    dictionary_id: 'd1',
    display_name: 'x',
    ...patch,
  };
}

const record: DerivedRecord = {
  id: 'dr-1',
  version: 2,
  parser: 'table',
  confidenceSummary: null,
  payload: {
    report_date: '2026-03-12',
    doc_type_suggest: 'lab_report',
    events: [],
    observations: [
      obs({ raw_name: '血小板计数', code: 'PLT', abnormal_flag: 'L' }),
      obs({ raw_name: '总胆固醇', code: 'TC', abnormal_flag: 'H' }),
      obs({ raw_name: '矫正视力', code: null, dictionary_id: null, confidence: 0.5 }),
      obs({ raw_name: '身高', code: null, dictionary_id: null, confidence: 0.95 }),
      obs({ raw_name: '尿白细胞', code: 'URINE_WBC', value: '阴性', abnormal_flag: 'N' }),
      obs({ raw_name: '皮肤', code: null, dictionary_id: null, value: '未见明显异常' }),
    ],
  },
};

describe('按临床意义分组（避免 135 条逐条点）', () => {
  it('异常项排在最前，其次低置信、字典未命中、正常', () => {
    const groups = groupObservations(record.payload.observations);
    expect(groups.map((g) => g.key)).toEqual([
      'abnormal',
      'lowConfidence',
      'unmapped',
      'normal',
    ]);
    expect(groups[0].items.map((i) => i.obs.raw_name)).toEqual(['血小板计数', '总胆固醇']);
    expect(groups[1].items.map((i) => i.obs.raw_name)).toEqual(['矫正视力']);
    expect(groups[2].items.map((i) => i.obs.raw_name)).toEqual(['身高', '皮肤']);
    expect(groups[3].items.map((i) => i.obs.raw_name)).toEqual(['尿白细胞']);
  });

  it('空分组被省略', () => {
    const groups = groupObservations([obs({ raw_name: '身高', code: 'HGT', abnormal_flag: 'N' })]);
    expect(groups.map((g) => g.key)).toEqual(['normal']);
  });

  it('索引保持原始 position（决策 ref 不能错位）', () => {
    const groups = groupObservations(record.payload.observations);
    expect(groups[0].items.map((i) => i.index)).toEqual([0, 1]);
    expect(groups[1].items.map((i) => i.index)).toEqual([2]);
    expect(groups[2].items.map((i) => i.index)).toEqual([3, 5]);
    expect(groups[3].items.map((i) => i.index)).toEqual([4]);
  });

  it('分组摘要用于工具栏', () => {
    const summary = groupSummary(record.payload.observations);
    expect(summary).toEqual({ abnormal: 2, lowConfidence: 1, unmapped: 2, normal: 1 });
  });
});

describe('批量拒绝（清理描述型噪声行）', () => {
  it('只拒绝描述型行，异常/低置信/真实检验值不动', () => {
    expect(bulkRejectRefs(record)).toEqual(['obs:5']);
  });
});
