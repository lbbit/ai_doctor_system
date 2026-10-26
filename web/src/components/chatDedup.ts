/**
 * 问诊消息去重（真实缺陷：一次提问后回答渲染两遍）。
 *
 * 页面同时存在两份数据源：
 * 1. 本地乐观消息：`userEcho`（用户刚发的）与 `live`（SSE 流式中的回答）；
 * 2. 服务端历史：`done` 之后 refetch 回来的 messages。
 *
 * 若不清掉本地副本，服务端消息与本地消息会同时渲染 → 用户看到重复回答。
 * 本模块只做纯判断，便于单测。
 */

export interface ServerMessageLike {
  id: string;
  role: string;
  content: string;
}

export interface OptimisticState {
  assistantId?: string | null;
  userEcho?: string | null;
  content?: string | null;
}

export interface ClearDecision {
  clearLive: boolean;
  clearUserEcho: boolean;
}

/** 判断服务端数据是否已覆盖本地乐观消息。 */
export function shouldClearOptimistic(
  serverMessages: ServerMessageLike[],
  optimistic: OptimisticState,
): ClearDecision {
  const assistantId = optimistic.assistantId ?? '';
  const userEcho = (optimistic.userEcho ?? '').trim();

  // 关键：只看"本轮 assistant 消息"是否已落库，且**必须已有内容**。
  // 1. 不能看 user 文本：第二轮提问时服务端历史里已经有第一轮的同样内容，
  //    用"存在同内容 user 消息"判断会把本轮问答误清掉。
  // 2. 不能只看 id 存在：取消/中断会留下 content="" 的占位 assistant 行，
  //    据此清理会让正在流式的回答直接消失。
  const hasAssistant = Boolean(assistantId)
    && serverMessages.some(
      (m) =>
        m.id === assistantId
        && m.role === 'assistant'
        && m.content.trim().length > 0,
    );

  return { clearLive: hasAssistant, clearUserEcho: hasAssistant && Boolean(userEcho) };
}

/**
 * 删除本地流式草稿卡中已被服务端回拉的同名卡片，避免同一草稿渲染两次。
 *
 * 真实缺陷（2026-10-21 E2E）：`live.drafts` 与 `draftsByMessage`（服务端）
 * 同时渲染同一张"待确认记录"卡片。
 */
export function excludeResolvedDrafts<T extends { id: string }>(
  liveDrafts: T[],
  resolvedIds: Iterable<string>,
): T[] {
  const taken = new Set(resolvedIds);
  return liveDrafts.filter((d) => !taken.has(d.id));
}

/**
 * 服务端历史中的空 assistant 气泡不渲染。
 *
 * 占位行在流式期间 content=""，若渲染会得到一个空气泡；
 * 此时回答由本地 `live` 展示，两者不该同时出现。
 */
export function isRenderableMessage(message: {
  role: string;
  content?: string | null;
}): boolean {
  if (message.role !== 'assistant') return true;
  return Boolean((message.content ?? '').trim());
}
