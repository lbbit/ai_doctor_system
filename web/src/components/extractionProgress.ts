/** 抽取阶段与进度的展示映射（F-05 体验：让用户看见“在做什么、还剩多少”）。 */

import type { DocStatus, ExtractionJob } from '../types/api';

export const STAGE_LABELS: Record<string, string> = {
  loading: '读取原件',
  rendering: '渲染 PDF 页面',
  extracting: 'AI 逐页识别指标',
  mapping: '匹配指标字典',
  done: '已完成',
  failed: '已失败',
};

/** 阶段说明：给用户一个“现在在干什么”的句子。 */
export function stageHint(job: ExtractionJob | null | undefined): string {
  if (!job) return '已排队，等待开始处理…';
  switch (job.stage) {
    case 'loading':
      return '正在从对象存储读取原件…';
    case 'rendering':
      return 'PDF 页数较多，正在把每页渲染成图片以便逐页识别…';
    case 'extracting':
      return job.progress.total > 0
        ? `正在逐页识别：已完成 ${job.progress.done}/${job.progress.total} 页，请勿关闭页面`
        : '正在逐页识别指标…';
    case 'mapping':
      return '正在把识别结果与指标字典匹配…';
    case 'done':
      return '处理完成';
    case 'failed':
      return '处理中断';
    default:
      return job.state === 'queued' ? '已排队，等待开始…' : '正在处理…';
  }
}

/** 进度百分比（0~100）；无页数信息时返回 null（改为不确定进度条）。 */
export function progressPercent(job: ExtractionJob | null | undefined): number | null {
  if (!job) return null;
  const { done, total } = job.progress;
  if (!total || total <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((done / total) * 100)));
}

export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m} 分 ${rest} 秒` : `${m} 分`;
}

/** 处理中状态的预估上限说明（页数 × 单页耗时经验值），避免用户以为卡死。 */
export function estimateHint(job: ExtractionJob | null | undefined): string | null {
  if (!job || job.state !== 'running') return null;
  const total = job.progress.total;
  if (!total) return null;
  // 实测单页视觉识别约 6~20s（含并发），给出区间而非精确承诺
  const low = Math.max(10, Math.ceil((total * 6) / 3));
  const high = Math.ceil((total * 20) / 3);
  return `预计 ${formatElapsed(low)} ~ ${formatElapsed(high)}（${total} 页）`;
}

/** 列表行副标题：处理中的文档显示阶段，避免只看到一个静态标签。 */
export function listStatusNote(status: DocStatus, job?: ExtractionJob | null): string | null {
  if (status === 'pending') return '排队中，通常几秒内开始';
  if (status !== 'extracting' || !job) return null;
  const pct = progressPercent(job);
  if (pct != null) return `识别中 ${pct}%（${job.progress.done}/${job.progress.total} 页）`;
  return '识别中…';
}
