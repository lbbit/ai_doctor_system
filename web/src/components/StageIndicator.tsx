/**
 * 问诊阶段指示器（§19）。
 *
 * 目标：在"等待首字"与"工具执行"期间提供**具体**进度与动效，
 * 避免用户面对长时间静止界面产生"卡死了"的判断。
 */

import { formatWait, firstTokenHint, stageDisplay } from './chatStatus';

export interface StageIndicatorProps {
  stage: string | null;
  stageDetail?: string | null;
  waitedMs: number;
  hasContent: boolean;
}

export default function StageIndicator({
  stage,
  stageDetail,
  waitedMs,
  hasContent,
}: StageIndicatorProps) {
  const display = stageDisplay(stage);
  const hint = firstTokenHint(waitedMs, hasContent);

  if (!display) {
    return (
      <div className="stage-indicator">
        <span className="stage-indicator__dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className="stage-indicator__label">处理中…</span>
      </div>
    );
  }

  return (
    <div
      className={`stage-indicator${display.external ? ' stage-indicator--external' : ''}`}
      role="status"
      aria-live="polite"
    >
      <span className="stage-indicator__icon" aria-hidden="true">
        {display.icon}
      </span>
      <span className="stage-indicator__label">{stageDetail || display.label}</span>
      <span className="stage-indicator__dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {!hasContent && waitedMs >= 1000 && (
        <span className="stage-indicator__timer">{formatWait(waitedMs)}</span>
      )}
      {hint && <div className="stage-indicator__hint">{hint}</div>}
    </div>
  );
}
