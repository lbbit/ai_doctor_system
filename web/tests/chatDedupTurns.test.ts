import { describe, expect, it } from 'vitest';
import { shouldClearOptimistic } from '../src/components/chatDedup';

const streamed = [
  { id: 'u1', role: 'user', content: '我的血红蛋白最近怎么样？' },
  { id: 'a1', role: 'assistant', content: '第一次回答……' },
];

const secondTurn = [
  ...streamed,
  { id: 'u2', role: 'user', content: '帮我记一下：今天血压 148/96' },
];

describe('SSE done 后清理本地乐观消息', () => {
  it('服务端已有本轮 assistant 时清 live（含上一条回答已落库的场景）', () => {
    const r = shouldClearOptimistic(streamed, {
      assistantId: 'a1',
      userEcho: '我的血红蛋白最近怎么样？',
      content: '第一次回答……',
    });
    expect(r.clearLive).toBe(true);
    expect(r.clearUserEcho).toBe(true);
  });

  it('第二轮提问：服务端只回拉了上一轮时，本轮 live 保留（还在流式中）', () => {
    const r = shouldClearOptimistic(secondTurn, {
      assistantId: 'a2',
      userEcho: '帮我记一下：今天血压 148/96',
      content: '',
    });
    expect(r.clearLive).toBe(false);
    expect(r.clearUserEcho).toBe(false);
  });

  it('第二轮完成并落库后清 live', () => {
    const r = shouldClearOptimistic(
      [...secondTurn, { id: 'a2', role: 'assistant', content: '记好了' }],
      { assistantId: 'a2', userEcho: '帮我记一下：今天血压 148/96', content: '记好了' },
    );
    expect(r.clearLive).toBe(true);
    expect(r.clearUserEcho).toBe(true);
  });

  it('服务端回拉失败（空列表）时不允许清理，避免回答消失', () => {
    const r = shouldClearOptimistic([], {
      assistantId: 'a1',
      userEcho: '问题',
      content: '回答',
    });
    expect(r.clearLive).toBe(false);
    expect(r.clearUserEcho).toBe(false);
  });
});
