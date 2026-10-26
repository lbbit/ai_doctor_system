import { describe, expect, it } from 'vitest';
import { parseSseBlock } from '../src/api/chat';

describe('SSE 块解析（问诊流式协议）', () => {
  it('解析 meta/delta 事件', () => {
    const evt = parseSseBlock('event: meta\ndata: {"conversationId":"c1","assistantMessageId":"a1"}');
    expect(evt?.event).toBe('meta');
    expect((evt?.data as { conversationId: string }).conversationId).toBe('c1');
  });

  it('解析 citations/redflag/draft/done/error', () => {
    expect(parseSseBlock('event: delta\ndata: {"content":"你好"}')?.event).toBe('delta');
    expect(
      parseSseBlock('event: citations\ndata: {"traceId":"t1","refs":[]}')?.event,
    ).toBe('citations');
    expect(parseSseBlock('event: redflag\ndata: {"code":"RF_X","severity":"high"}')?.event).toBe('redflag');
    expect(parseSseBlock('event: draft\ndata: {"id":"d1"}')?.event).toBe('draft');
    expect(parseSseBlock('event: done\ndata: {"messageId":"m1"}')?.event).toBe('done');
    expect(parseSseBlock('event: error\ndata: {"code":"LLM_TIMEOUT"}')?.event).toBe('error');
  });

  it('多行 data JSON 拼接', () => {
    const evt = parseSseBlock('event: delta\ndata: {"content":\ndata: "hi"}');
    expect(evt?.data).toEqual({ content: 'hi' });
  });

  it('非法块返回 null', () => {
    expect(parseSseBlock('')).toBeNull();
    expect(parseSseBlock(': keep-alive')).toBeNull();
    expect(parseSseBlock('event: delta\ndata: not-json')).toBeNull();
  });
});
