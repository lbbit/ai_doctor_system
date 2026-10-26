/**
 * 状态展示：处理状态与生命周期正交（R0 T004）。
 *
 * 背景：此前 `status` 同时表达"抽取进度"与"归档"，归档会把"已确认"覆盖成
 * "已归档"，导致用户看不到该记录其实已经确认入库。R0 起两个维度分开显示。
 */
import { describe, expect, it } from 'vitest';
import { documentStatusLabels } from '../src/components/extractionProgress';

describe('文档状态展示（处理状态 × 生命周期）', () => {
  it('处理状态显示为用户可读文案', () => {
    expect(
      documentStatusLabels({ processingStatus: 'pending', lifecycleStatus: 'active' })
        .primary,
    ).toBe('待处理');
    expect(
      documentStatusLabels({
        processingStatus: 'processing',
        lifecycleStatus: 'active',
      }).primary,
    ).toBe('抽取中');
    expect(
      documentStatusLabels({
        processingStatus: 'awaiting_review',
        lifecycleStatus: 'active',
      }).primary,
    ).toBe('待确认');
    expect(
      documentStatusLabels({ processingStatus: 'failed', lifecycleStatus: 'active' })
        .primary,
    ).toBe('解析失败');
  });

  it('processed 区分已确认与已解析无事件', () => {
    expect(
      documentStatusLabels({
        processingStatus: 'processed',
        contentStatus: 'confirmed',
        lifecycleStatus: 'active',
      }).primary,
    ).toBe('已确认');
    expect(
      documentStatusLabels({
        processingStatus: 'processed',
        contentStatus: 'no_data',
        lifecycleStatus: 'active',
      }).primary,
    ).toBe('已解析无事件');
  });

  it('归档不再抹掉"已确认"：两个维度同时显示', () => {
    const labels = documentStatusLabels({
      processingStatus: 'processed',
      contentStatus: 'confirmed',
      lifecycleStatus: 'archived',
      status: 'archived',
    });
    expect(labels.primary).toBe('已确认');
    expect(labels.lifecycle).toBe('已归档');
  });

  it('在用档案不显示生命周期标签', () => {
    const labels = documentStatusLabels({
      processingStatus: 'processed',
      contentStatus: 'confirmed',
      lifecycleStatus: 'active',
    });
    expect(labels.lifecycle).toBeNull();
  });

  it('历史数据只有旧字段时按兼容语义降级', () => {
    // 旧服务端：confirmed 视为 processed+confirmed，archived 视为生命周期归档
    const labels = documentStatusLabels({ status: 'confirmed' });
    expect(labels.primary).toBe('已确认');
    expect(labels.lifecycle).toBeNull();
  });

  it('needs_reconcile 会被标出（历史行处理状态不可推断）', () => {
    const labels = documentStatusLabels({
      processingStatus: 'pending',
      lifecycleStatus: 'archived',
      needsReconcile: true,
    });
    expect(labels.needsReconcile).toBe(true);
  });
});
