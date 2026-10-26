/** 确认卡片的批量决策与筛选逻辑（PRD AC-6.1：低置信/异常必确认，高置信可快速通过）。 */

import type { DerivedRecord, ObsDraft } from '../types/api';

export const CONFIDENCE_THRESHOLD = 0.85;

/** 需要人工关注的指标：低置信，或标记为异常（H/L/A）。 */
export function needsAttention(obs: ObsDraft): boolean {
  const lowConf = (obs.confidence ?? 0) < CONFIDENCE_THRESHOLD;
  const abnormal = Boolean(obs.abnormal_flag && obs.abnormal_flag !== 'N');
  return lowConf || abnormal;
}

/** 可一键快速通过的指标引用：高置信且非异常。 */
export function bulkConfirmRefs(record: DerivedRecord): string[] {
  return record.payload.observations
    .map((obs, i) => ({ obs, ref: `obs:${i}` }))
    .filter(({ obs }) => !needsAttention(obs))
    .map(({ ref }) => ref);
}

export interface ConfirmStats {
  total: number;
  attention: number;
  quick: number;
}

export function confirmStats(record: DerivedRecord): ConfirmStats {
  const obs = record.payload.observations;
  const attention = obs.filter(needsAttention).length;
  return {
    total: obs.length + record.payload.events.length,
    attention,
    quick: obs.length - attention,
  };
}

/** 筛选视图：all=全部；attention=只看需人工确认项。 */
export type ConfirmFilter = 'all' | 'attention';

export function filterObservations(
  observations: ObsDraft[],
  filter: ConfirmFilter,
): Array<{ obs: ObsDraft; index: number }> {
  return observations
    .map((obs, index) => ({ obs, index }))
    .filter(({ obs }) => (filter === 'attention' ? needsAttention(obs) : true));
}

/**
 * 可批量拒绝的指标引用：高置信且非异常的**描述型**行。
 *
 * 真实报告里大量行是「皮肤：未见明显异常」「病史：无」这类描述，不是可趋势化的
 * 数值指标；把它们一次性拒绝能显著减少确认负担。真实数值项（身高/血压等）即便
 * 高置信也不在此列，避免误删。
 */
const DESCRIPTION_VALUES = [
  '未见明显异常',
  '未见异常',
  '无异常',
  '无特殊',
  '无',
  '正常',
  '齐',
  '放弃检查',
  '未查',
];

export function bulkRejectRefs(record: DerivedRecord): string[] {
  return record.payload.observations
    .map((obs, i) => ({ obs, ref: `obs:${i}` }))
    .filter(({ obs }) => {
      if (needsAttention(obs)) return false;
      if (typeof obs.value === 'number') return false;
      const text = String(obs.value ?? '').trim();
      if (!text) return false;
      const hasRef = obs.ref_low != null || obs.ref_high != null || Boolean(obs.ref_text);
      if (hasRef) return false;
      return DESCRIPTION_VALUES.some((d) => text === d || text.includes(d));
    })
    .map(({ ref }) => ref);
}

export type ObsGroupKey = 'abnormal' | 'lowConfidence' | 'unmapped' | 'normal';

export const OBS_GROUP_LABELS: Record<ObsGroupKey, string> = {
  abnormal: '异常指标',
  lowConfidence: '低置信（需核对）',
  unmapped: '字典未命中',
  normal: '正常指标',
};

export interface ObsGroup {
  key: ObsGroupKey;
  label: string;
  items: Array<{ obs: ObsDraft; index: number }>;
}

/**
 * 按临床意义分组（顺序即关注度）：异常 → 低置信 → 字典未命中 → 正常。
 * 每条只归入第一个命中的分组，避免重复；index 始终是原始下标（决策 ref 不能错位）。
 */
export function groupObservations(observations: ObsDraft[]): ObsGroup[] {
  const buckets: Record<ObsGroupKey, Array<{ obs: ObsDraft; index: number }>> = {
    abnormal: [],
    lowConfidence: [],
    unmapped: [],
    normal: [],
  };

  observations.forEach((obs, index) => {
    const abnormal = Boolean(obs.abnormal_flag && obs.abnormal_flag !== 'N');
    const lowConf = (obs.confidence ?? 0) < CONFIDENCE_THRESHOLD;
    let key: ObsGroupKey = 'normal';
    if (abnormal) key = 'abnormal';
    else if (lowConf) key = 'lowConfidence';
    else if (!obs.code) key = 'unmapped';
    buckets[key].push({ obs, index });
  });

  return (Object.keys(buckets) as ObsGroupKey[])
    .filter((key) => buckets[key].length > 0)
    .map((key) => ({ key, label: OBS_GROUP_LABELS[key], items: buckets[key] }));
}

export function groupSummary(observations: ObsDraft[]): Record<ObsGroupKey, number> {
  const summary: Record<ObsGroupKey, number> = {
    abnormal: 0,
    lowConfidence: 0,
    unmapped: 0,
    normal: 0,
  };
  for (const group of groupObservations(observations)) {
    summary[group.key] = group.items.length;
  }
  return summary;
}

