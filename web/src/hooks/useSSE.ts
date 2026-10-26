import { useCallback, useRef, useState } from 'react';
import { chatApi } from '../api/chat';
import type { SseEvent } from '../types/api';

export type StreamStatus = 'idle' | 'streaming' | 'error';

/** SSE 流封装：发送消息 + 取消 + 错误状态（E-5 重试由调用方触发）。 */
export function useChatStream(conversationId: string | null) {
  const [status, setStatus] = useState<StreamStatus>('idle');
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (content: string, onEvent: (evt: SseEvent) => void) => {
      if (!conversationId) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus('streaming');
      setError(null);
      try {
        await chatApi.sendMessage(conversationId, content, onEvent, controller.signal);
        setStatus('idle');
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setStatus('idle');
          return;
        }
        setStatus('error');
        const e = err as { code?: string; message?: string };
        setError({ code: e.code ?? 'INTERNAL', message: e.message ?? '发送失败' });
      } finally {
        abortRef.current = null;
      }
    },
    [conversationId],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setStatus('idle');
  }, []);

  return { status, error, send, cancel };
}
