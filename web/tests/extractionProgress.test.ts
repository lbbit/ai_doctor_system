import { describe, expect, it } from 'vitest';
import {
  estimateHint,
  formatElapsed,
  listStatusNote,
  progressPercent,
  stageHint,
} from '../src/components/extractionProgress';
import type { ExtractionJob } from '../src/types/api';

function job(patch: Partial<ExtractionJob> = {}): ExtractionJob {
  return {
    id: 'job-1',
    documentId: 'doc-1',
    state: 'running',
    attempt: 0,
    parser: null,
    error: null,
    stage: 'extracting',
    progress: { done: 3, total: 20 },
    elapsedSeconds: 42,
    stale: false,
    startedAt: '2026-10-21T03:00:00Z',
    finishedAt: null,
    ...patch,
  };
}

describe('抽取进度展示（用户反馈：看不到进度）', () => {
  it('页级进度百分比按已完成/总页数计算', () => {
    expect(progressPercent(job({ progress: { done: 5, total: 20 } }))).toBe(25);
    expect(progressPercent(job({ progress: { done: 20, total: 20 } }))).toBe(100);
    expect(progressPercent(job({ progress: { done: 0, total: 0 } }))).toBeNull();
    expect(progressPercent(null)).toBeNull();
  });

  it('阶段提示包含已完成页数与总页数', () => {
    const hint = stageHint(job());
    expect(hint).toContain('3/20');
  });

  it('各阶段有可读中文说明', () => {
    expect(stageHint(job({ stage: 'loading' }))).toContain('读取原件');
    expect(stageHint(job({ stage: 'rendering' }))).toContain('渲染');
    expect(stageHint(job({ stage: 'mapping' }))).toContain('字典');
    expect(stageHint(null)).toContain('排队');
  });

  it('耗时格式化', () => {
    expect(formatElapsed(9)).toBe('9 秒');
    expect(formatElapsed(65)).toBe('1 分 5 秒');
    expect(formatElapsed(120)).toBe('2 分');
  });

  it('多页任务给出预计耗时区间，避免用户以为卡死', () => {
    const hint = estimateHint(job({ progress: { done: 0, total: 20 } }));
    expect(hint).toContain('20 页');
    expect(hint).toContain('预计');
    expect(estimateHint(job({ state: 'succeeded' }))).toBeNull();
  });

  it('列表行显示处理子状态', () => {
    expect(listStatusNote('pending')).toContain('排队');
    expect(listStatusNote('extracting', job())).toContain('15%');
    expect(listStatusNote('extracting', job({ progress: { done: 0, total: 0 } }))).toContain(
      '识别中',
    );
    expect(listStatusNote('confirmed', job())).toBeNull();
  });
});
