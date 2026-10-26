import { describe, expect, it } from 'vitest';
import { shouldClearOptimistic } from '../src/components/chatDedup';

describe('占位 assistant 行（取消/中断）不得触发清理', () => {
  it('服务端 assistant content 为空时，保留本地流式内容', () => {
    const server = [
      { id: 'u1', role: 'user', content: '问题' },
      { id: 'a1', role: 'assistant', content: '' }, // 取消/中断留下的占位行
    ];
    const r = shouldClearOptimistic(server, {
      assistantId: 'a1',
      userEcho: '问题',
      content: '正在输出的回答',
    });
    expect(r.clearLive).toBe(false);
    expect(r.clearUserEcho).toBe(false);
  });

  it('服务端 assistant 有内容时才清理', () => {
    const server = [
      { id: 'u1', role: 'user', content: '问题' },
      { id: 'a1', role: 'assistant', content: '完整回答' },
    ];
    const r = shouldClearOptimistic(server, {
      assistantId: 'a1',
      userEcho: '问题',
      content: '完整回答',
    });
    expect(r.clearLive).toBe(true);
    expect(r.clearUserEcho).toBe(true);
  });

  it('空白内容（只含空格/换行）同样视为占位', () => {
    const server = [
      { id: 'a1', role: 'assistant', content: '   \n  ' },
    ];
    const r = shouldClearOptimistic(server, { assistantId: 'a1', userEcho: 'x', content: 'y' });
    expect(r.clearLive).toBe(false);
  });
});
