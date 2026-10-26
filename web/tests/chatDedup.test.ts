import { describe, expect, it } from 'vitest';
import { shouldClearOptimistic } from '../src/components/chatDedup';

/**
 * 真实缺陷（2026-10-21 E2E）：一次提问后，同一条回答在界面上渲染了两次。
 * 原因：SSE `done` 之后没有清掉本地乐观消息（live / userEcho），
 * 而服务端回拉的历史消息又包含同一条，两者同时渲染。
 */

const server = [
  { id: 'u1', role: 'user', content: '我的血红蛋白最近怎么样？' },
  { id: 'a1', role: 'assistant', content: '档案中目前能查到的血红蛋白记录只有 1 次……' },
];

describe('问诊消息去重', () => {
  it('服务端已有该 assistant 消息时，应清掉本地 live', () => {
    const r = shouldClearOptimistic(server, { assistantId: 'a1', userEcho: null, content: '' });
    expect(r.clearLive).toBe(true);
    expect(r.clearUserEcho).toBe(false);
  });

  it('服务端已有同内容的 user 消息时，应清掉本地回显', () => {
    const r = shouldClearOptimistic(server, {
      assistantId: 'a1',
      userEcho: '我的血红蛋白最近怎么样？',
      content: '',
    });
    expect(r.clearUserEcho).toBe(true);
  });

  it('服务端尚未返回（流式中）时，保留本地消息', () => {
    const r = shouldClearOptimistic([], {
      assistantId: 'a1',
      userEcho: '问题',
      content: '部分内容',
    });
    expect(r.clearLive).toBe(false);
    expect(r.clearUserEcho).toBe(false);
  });

  it('assistantId 为空（meta 未到）时不清 live', () => {
    const r = shouldClearOptimistic(server, { assistantId: '', userEcho: null, content: 'x' });
    expect(r.clearLive).toBe(false);
  });

  it('同内容但角色不同的用户消息不算重复', () => {
    const r = shouldClearOptimistic(
      [{ id: 'a9', role: 'assistant', content: '同一句话' }],
      { assistantId: null, userEcho: '同一句话', content: '' },
    );
    expect(r.clearUserEcho).toBe(false);
  });
});
