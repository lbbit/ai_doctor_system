import { describe, expect, it } from 'vitest';
import {
  firstTokenHint,
  formatWait,
  shouldUpdateStage,
  stageDisplay,
  stageRank,
} from '../src/components/chatStatus';

describe('stageDisplay', () => {
  it('已知阶段返回文案与图标', () => {
    const d = stageDisplay('tool_running');
    expect(d).not.toBeNull();
    expect(d!.label).toContain('检索');
    expect(d!.external).toBe(true);
  });

  it('generating 不是外部动作', () => {
    expect(stageDisplay('generating')!.external).toBe(false);
  });

  it('未知/空阶段返回 null', () => {
    expect(stageDisplay(null)).toBeNull();
    expect(stageDisplay(undefined)).toBeNull();
    expect(stageDisplay('unknown_stage')).toBeNull();
  });

  it('每个阶段都有图标与提示', () => {
    for (const s of ['thinking', 'planning', 'tool_running', 'tool_done', 'generating', 'heartbeat']) {
      const d = stageDisplay(s);
      expect(d).not.toBeNull();
      expect(d!.icon).toBeTruthy();
      expect(d!.hint).toBeTruthy();
    }
  });
});

describe('stageRank', () => {
  it('按流程递增', () => {
    expect(stageRank('thinking')).toBeLessThan(stageRank('planning'));
    expect(stageRank('planning')).toBeLessThan(stageRank('tool_running'));
    expect(stageRank('tool_done')).toBeLessThan(stageRank('generating'));
  });

  it('空返回 -1', () => {
    expect(stageRank(null)).toBe(-1);
  });

  it('未知阶段返回 -1', () => {
    expect(stageRank('nope')).toBe(-1);
  });
});

describe('shouldUpdateStage', () => {
  it('相同阶段不更新（避免无谓重渲染）', () => {
    expect(shouldUpdateStage('generating', 'generating')).toBe(false);
  });

  it('推进到新阶段更新', () => {
    expect(shouldUpdateStage('thinking', 'planning')).toBe(true);
    expect(shouldUpdateStage('tool_running', 'tool_done')).toBe(true);
  });

  it('heartbeat 只覆盖 generating 或空', () => {
    expect(shouldUpdateStage('generating', 'heartbeat')).toBe(true);
    expect(shouldUpdateStage(null, 'heartbeat')).toBe(true);
    // 不应让心跳掩盖具体的检索阶段
    expect(shouldUpdateStage('tool_running', 'heartbeat')).toBe(false);
  });

  it('heartbeat 之后可被具体阶段覆盖', () => {
    expect(shouldUpdateStage('heartbeat', 'tool_done')).toBe(true);
  });

  it('空 next 不更新', () => {
    expect(shouldUpdateStage('thinking', '')).toBe(false);
  });
});

describe('firstTokenHint', () => {
  it('已有内容时不提示', () => {
    expect(firstTokenHint(30000, true)).toBeNull();
  });

  it('8s 以内不提示', () => {
    expect(firstTokenHint(3000, false)).toBeNull();
    expect(firstTokenHint(7999, false)).toBeNull();
  });

  it('8s 起给准备资料提示', () => {
    expect(firstTokenHint(8000, false)).toContain('稍候');
  });

  it('15s 起给更明确的安抚', () => {
    expect(firstTokenHint(15000, false)).toContain('请再稍等');
  });
});

describe('formatWait', () => {
  it('秒级', () => {
    expect(formatWait(0)).toBe('0s');
    expect(formatWait(1200)).toBe('1s');
    expect(formatWait(59000)).toBe('59s');
  });

  it('分钟级补零', () => {
    expect(formatWait(60000)).toBe('1m00s');
    expect(formatWait(65000)).toBe('1m05s');
  });

  it('负数不产生负值', () => {
    expect(formatWait(-500)).toBe('0s');
  });
});
