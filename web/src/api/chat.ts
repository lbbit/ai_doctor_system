import client, { ApiError } from './client';
import type {
  ChatMessage,
  CitationRef,
  Conversation,
  Draft,
  SseEvent,
} from '../types/api';

export const chatApi = {
  async listConversations(status: 'active' | 'archived' | '' = 'active'): Promise<Conversation[]> {
    const { data } = await client.get<Conversation[]>('chat/conversations', {
      params: { status },
    });
    return data;
  },

  async createConversation(subjectId: string, title?: string): Promise<Conversation> {
    const { data } = await client.post<Conversation>('chat/conversations', {
      subjectId,
      title,
    });
    return data;
  },

  async conversationDetail(convId: string): Promise<{
    conversation: Conversation;
    messages: ChatMessage[];
    drafts: Draft[];
  }> {
    const { data } = await client.get(`chat/conversations/${convId}`);
    return data;
  },

  async closeConversation(convId: string): Promise<Conversation> {
    const { data } = await client.post<Conversation>(`chat/conversations/${convId}/close`);
    return data;
  },

  async messageTrace(messageId: string): Promise<{
    traceId: string | null;
    layers: Record<string, unknown>;
    refs: CitationRef[];
  }> {
    const { data } = await client.get(`chat/messages/${messageId}/trace`);
    return data;
  },

  async confirmDraft(draftId: string, payload?: unknown): Promise<{
    eventId: string | null;
    observationIds: string[];
  }> {
    const { data } = await client.post(`chat/drafts/${draftId}/confirm`, { payload });
    return data;
  },

  async discardDraft(draftId: string): Promise<{ status: string }> {
    const { data } = await client.post(`chat/drafts/${draftId}/discard`);
    return data;
  },

  /**
   * 发送消息（SSE 流式）。
   * 用 fetch + ReadableStream（EventSource 不支持 POST/自定义头）。
   */
  async sendMessage(
    convId: string,
    content: string,
    onEvent: (evt: SseEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const token = localStorage.getItem('aidoctor_token');
    const resp = await fetch(`/api/chat/conversations/${convId}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ content }),
      signal,
    });
    if (!resp.ok || !resp.body) {
      let code = 'NETWORK_ERROR';
      let message = '发送失败';
      try {
        const body = await resp.json();
        code = body?.code || code;
        message = body?.message || message;
      } catch {
        /* ignore */
      }
      throw new ApiError(code, message, resp.status);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() ?? '';
      for (const block of blocks) {
        const evt = parseSseBlock(block);
        if (evt) onEvent(evt);
      }
    }
    if (buffer.trim()) {
      const evt = parseSseBlock(buffer);
      if (evt) onEvent(evt);
    }
  },
};

/** 脱敏摘要 → 人类可读文本（SSE citations 事件 sanitizer 字段展示）。 */
export function formatSanitizerSummary(
  summary: Array<{ type: string; repl: string }> | undefined,
): string | null {
  if (!summary || summary.length === 0) return null;
  const labels: Record<string, string> = {
    id_card: '证件号',
    phone: '电话',
    email: '邮箱',
    birthday: '生日',
    name: '姓名',
    address: '地址',
  };
  const counts = new Map<string, number>();
  for (const item of summary) {
    const key = labels[item.type] ?? item.type;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return (
    '已脱敏：' +
    [...counts.entries()].map(([k, n]) => `${k}×${n}`).join('、')
  );
}

export function parseSseBlock(block: string): SseEvent | null {
  const lines = block.split('\n').filter(Boolean);
  let event = '';
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!event || dataLines.length === 0) return null;
  try {
    return { event: event as SseEvent['event'], data: JSON.parse(dataLines.join('\n')) } as SseEvent;
  } catch {
    return null;
  }
}
