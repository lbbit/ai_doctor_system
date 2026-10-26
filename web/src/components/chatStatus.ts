/**
 * 问诊阶段 → 展示映射（§19）。
 *
 * 设计目标：让用户在"等待首字"和"工具执行"期间看到**具体**进度，
 * 而不是笼统的"思考中…"。纯函数，便于单测。
 */

export type ChatStage =
  | 'thinking'
  | 'planning'
  | 'tool_running'
  | 'tool_done'
  | 'generating'
  | 'heartbeat';

export interface StageDisplay {
  label: string;
  icon: string;
  /** 是否表示"正在做外部动作"（检索等），需要更强的等待暗示 */
  external: boolean;
  /** 该阶段大致耗时量级，用于文案提示 */
  hint: string;
}

const DISPLAY: Record<ChatStage, StageDisplay> = {
  thinking: {
    label: '正在分析问题',
    icon: '🧠',
    external: false,
    hint: '正在读取你的健康档案',
  },
  planning: {
    label: '判断是否需要查资料',
    icon: '🧭',
    external: false,
    hint: '决定是否检索外部医学资料',
  },
  tool_running: {
    label: '正在检索外部资料',
    icon: '🔎',
    external: true,
    hint: '联网检索通常需要几秒',
  },
  tool_done: {
    label: '正在整理资料',
    icon: '📚',
    external: true,
    hint: '把检索结果与你的档案对照',
  },
  generating: {
    label: '正在生成回答',
    icon: '✍️',
    external: false,
    hint: '首个字即将出现',
  },
  heartbeat: {
    label: '仍在生成中',
    icon: '⏳',
    external: false,
    hint: '回答较长，请稍候',
  },
};

export function stageDisplay(stage: string | null | undefined): StageDisplay | null {
  if (!stage) return null;
  const key = stage as ChatStage;
  return DISPLAY[key] ?? null;
}

/** 阶段顺序（用于判断"是否能前进"，防止乱序事件导致回退）。 */
export const STAGE_ORDER: ChatStage[] = [
  'thinking',
  'planning',
  'tool_running',
  'tool_done',
  'generating',
  'heartbeat',
];

export function stageRank(stage: string | null | undefined): number {
  if (!stage) return -1;
  const idx = STAGE_ORDER.indexOf(stage as ChatStage);
  return idx;
}

/**
 * 是否应该用新阶段覆盖当前阶段。
 *
 * - heartbeat 不覆盖 generating（否则文案会来回跳）
 * - 阶段可重复出现（tool_running 可多次），只要不是"回退到更早且更具体"的阶段
 */
export function shouldUpdateStage(
  current: string | null,
  next: string,
): boolean {
  if (!next) return false;
  if (current === next) return false;
  if (next === 'heartbeat') return current === 'generating' || current === null;
  if (current === 'heartbeat') return true;
  return true;
}

/** 首字等待提示：超过阈值仍未出现内容时，给出更明确的安抚文案。 */
export function firstTokenHint(elapsedMs: number, hasContent: boolean): string | null {
  if (hasContent) return null;
  if (elapsedMs >= 15000) return '比平时慢一些，仍在处理，请再稍等…';
  if (elapsedMs >= 8000) return '首次回答需要准备资料，请稍候';
  return null;
}

/** 格式化已等待秒数（用于指示器右侧）。 */
export function formatWait(elapsedMs: number): string {
  const sec = Math.max(0, Math.floor(elapsedMs / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m${s.toString().padStart(2, '0')}s`;
}
