import { describe, expect, it } from 'vitest';
import {
  excludeResolvedDrafts,
  isRenderableMessage,
} from '../src/components/chatDedup';

describe('草稿卡片与空气泡去重（真实 E2E 缺陷）', () => {
  it('本地流式草稿在服务端回拉后不再渲染', () => {
    const liveDrafts = [{ id: 'd1' }, { id: 'd2' }];
    expect(excludeResolvedDrafts(liveDrafts, ['d1'])).toEqual([{ id: 'd2' }]);
  });

  it('服务端没有回拉时保留全部本地草稿', () => {
    const liveDrafts = [{ id: 'd1' }];
    expect(excludeResolvedDrafts(liveDrafts, [])).toEqual([{ id: 'd1' }]);
  });

  it('空 assistant 气泡不渲染（占位行由 live 负责展示）', () => {
    expect(isRenderableMessage({ role: 'assistant', content: '' })).toBe(false);
    expect(isRenderableMessage({ role: 'assistant', content: '   ' })).toBe(false);
    expect(isRenderableMessage({ role: 'assistant', content: '有内容' })).toBe(true);
  });

  it('user 消息即使为空也保持渲染（避免用户输入消失）', () => {
    expect(isRenderableMessage({ role: 'user', content: '' })).toBe(true);
  });
});
