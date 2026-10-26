import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import CitationTag from '../components/CitationTag';
import MarkdownMessage from '../components/MarkdownMessage';
import StageIndicator from '../components/StageIndicator';
import DraftCard from '../components/DraftCard';
import RedFlagBanner from '../components/RedFlagBanner';
import { excludeResolvedDrafts, isRenderableMessage } from '../components/chatDedup';
import { shouldUpdateStage } from '../components/chatStatus';
import { chatApi, formatSanitizerSummary } from '../api/chat';
import { documentsApi } from '../api/documents';
import { useChatStream } from '../hooks/useSSE';
import { useSubjectStore } from '../stores/subjectStore';
import type {
  ChatMessage,
  CitationRef,
  Draft,
  SseEvent,
} from '../types/api';

interface LiveState {
  assistantId: string;
  content: string;
  refs: CitationRef[];
  sanitizer: string | null;
  redFlags: Array<{ code: string; severity: string; message: string }>;
  drafts: Array<Draft & { subjectName: string; subjectAvatar: string | null }>;
  error: { code: string; message: string } | null;
  /** 当前阶段代码（thinking/planning/tool_running/tool_done/generating/heartbeat） */
  stage: string | null;
  /** 阶段详情文案（由后端下发，缺省时用本地映射） */
  stageDetail: string | null;
  /** 本轮开始时间（毫秒），用于展示已等待时长 */
  startedAt: number;
  /** 首字是否已到达（用于区分“还在等”与“正在输出”） */
  hasContent: boolean;
}

/** F-09/F-10/F-12 问诊页（A-7 会话锁定 Subject；E-8 切换守卫）。 */
export default function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const current = useSubjectStore((s) => s.current);
  const setGuardActive = useSubjectStore((s) => s.setGuardActive);
  const [activeId, setActiveId] = useState<string | null>(conversationId ?? null);
  const [input, setInput] = useState('');
  const [userEcho, setUserEcho] = useState<string | null>(null);
  const [lastSent, setLastSent] = useState('');
  const [live, setLive] = useState<LiveState | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  /** messageId → 引用 refs（citations 事件缓存，历史消息按需拉 trace） */
  const refsCache = useRef(new Map<string, CitationRef[]>());

  const convsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () => chatApi.listConversations('active'),
    refetchInterval: 15_000,
  });

  const { status, error: streamError, send, cancel } = useChatStream(activeId);
  const queryClient = useQueryClient();

  // ---- 确保存在绑定当前 Subject 的活跃会话（A-7 / E-8）----
  useEffect(() => {
    if (!current) return;
    let cancelled = false;
    void (async () => {
      const convs = convsQuery.data ?? [];
      if (activeId) {
        const conv = convs.find((c) => c.id === activeId);
        if (conv && conv.subjectId !== current.id) {
          // 会话中途切换 Subject：归档旧会话，为当前档案新建（E-8）
          await chatApi.closeConversation(activeId);
          const fresh = await chatApi.createConversation(current.id);
          if (!cancelled) {
            setActiveId(fresh.id);
            navigate(`/app/chat/${fresh.id}`, { replace: true });
            void convsQuery.refetch();
          }
        }
        return;
      }
      const existing = convs.find((c) => c.subjectId === current.id && c.status === 'active');
      if (existing) {
        if (!cancelled) {
          setActiveId(existing.id);
          navigate(`/app/chat/${existing.id}`, { replace: true });
        }
        return;
      }
      const conv = await chatApi.createConversation(current.id);
      if (!cancelled) {
        setActiveId(conv.id);
        navigate(`/app/chat/${conv.id}`, { replace: true });
        void convsQuery.refetch();
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.id, activeId, convsQuery.data]);

  // 切换守卫：存在活跃会话时开启（E-8）
  useEffect(() => {
    setGuardActive(Boolean(activeId));
    return () => setGuardActive(false);
  }, [activeId, setGuardActive]);

  const detailQuery = useQuery({
    queryKey: ['conversation', activeId],
    queryFn: () => chatApi.conversationDetail(activeId!),
    enabled: Boolean(activeId),
    // 流式结束后需要立刻反映服务端消息，禁用窗口聚焦才刷新的缓存策略
    staleTime: 0,
  });

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [detailQuery.data?.messages.length, live?.content]);

  // 渲染期守卫（而非 effect 清理）：当服务端已包含同一条消息时，本地乐观块不再渲染。
  // 这是纯计算，不依赖 setState 的时序，因此不会出现"清理晚了一步导致渲染两遍"。
  const serverMessages = useMemo(
    () => detailQuery.data?.messages ?? [],
    [detailQuery.data?.messages],
  );
  const liveVisible = useMemo(() => {
    if (!live) return null;
    const id = live.assistantId;
    const persisted =
      Boolean(id) &&
      serverMessages.some((m) => m.id === id && (m.content ?? '').trim().length > 0);
    // 已落库：不渲染本地块（由服务端消息负责）；否则继续展示流式内容
    return persisted ? null : live;
  }, [live, serverMessages]);

  const echoVisible = useMemo(() => {
    if (!userEcho) return null;
    const text = userEcho.trim();
    // 服务端已记录本轮提问时不再重复渲染本地区块：
    // 用"最后一条 user 消息"判断，避免历史里同文本消息造成误判。
    const lastUser = [...serverMessages].reverse().find((m) => m.role === 'user');
    return lastUser && lastUser.content.trim() === text ? null : userEcho;
  }, [userEcho, serverMessages]);

  // 等待计时器：仅在"本轮进行中且尚未出字"时每秒跳动，让用户看到确实在跑
  const [waitedMs, setWaitedMs] = useState(0);
  useEffect(() => {
    if (!live || live.hasContent) {
      setWaitedMs(0);
      return;
    }
    setWaitedMs(Date.now() - live.startedAt);
    const timer = window.setInterval(() => {
      setWaitedMs(Date.now() - live.startedAt);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [live?.startedAt, live?.hasContent, live]);

  const doSend = async (textOverride?: string) => {
    const text = (textOverride ?? input).trim();
    if (!text || !activeId || status === 'streaming') return;
    if (!textOverride) setInput('');
    setLastSent(text);
    setUserEcho(text);
    setLive({
      assistantId: '',
      content: '',
      refs: [],
      sanitizer: null,
      redFlags: [],
      drafts: [],
      error: null,
      stage: 'thinking',
      stageDetail: null,
      startedAt: Date.now(),
      hasContent: false,
    });

    try {
      await send(text, (evt: SseEvent) => {
        switch (evt.event) {
          case 'meta':
            setLive((l) => (l ? { ...l, assistantId: evt.data.assistantMessageId } : l));
            break;
          case 'delta':
            setLive((l) =>
              l
                ? {
                    ...l,
                    content: l.content + evt.data.content,
                    stage: 'generating',
                    hasContent: true,
                  }
                : l,
            );
            break;
          case 'citations':
            refsCache.current.set(evt.data.traceId, evt.data.refs);
            setLive((l) =>
              l
                ? { ...l, refs: evt.data.refs, sanitizer: formatSanitizerSummary(evt.data.sanitizer) }
                : l,
            );
            break;
          case 'redflag':
            setLive((l) =>
              l ? { ...l, redFlags: [...l.redFlags, evt.data] } : l,
            );
            break;
          case 'draft':
            setLive((l) =>
              l
                ? {
                    ...l,
                    drafts: [
                      ...l.drafts,
                      { ...evt.data, subjectName: evt.data.subjectName, subjectAvatar: evt.data.subjectAvatar },
                    ],
                  }
                : l,
            );
            break;
          case 'error':
            setLive((l) => (l ? { ...l, error: evt.data } : l));
            break;
          case 'status':
            // 阶段提示：不论是否已有内容都更新（检索/整理阶段用户最需要反馈），
            // 但 heartbeat 不得掩盖已知的具体阶段。
            setLive((l) =>
              l && shouldUpdateStage(l.stage, evt.data.stage)
                ? { ...l, stage: evt.data.stage, stageDetail: evt.data.detail ?? null }
                : l,
            );
            break;
          case 'done':
            refsCache.current.set(
              evt.data.messageId,
              refsCache.current.get(evt.data.traceId) ?? [],
            );
            break;
          default:
            break;
        }
      });
    } finally {
      // 无论流正常结束、被取消还是抛错，都以服务端为唯一事实来源：
      // 清掉本地乐观状态并刷新会话详情，避免同一问答渲染两遍。
      setLive(null);
      setUserEcho(null);
      const refreshed = await detailQuery.refetch();
      // react-query 在部分场景（结构未变）不会立刻重渲染，直接写回缓存保证一致。
      if (refreshed.data) {
        queryClient.setQueryData(['conversation', activeId], refreshed.data);
      }
      void convsQuery.refetch();
    }
  };

  const onCite = async (label: string, messageId?: string) => {
    try {
      let ref: CitationRef | undefined = live?.refs.find((r) => r.label === label);
      if (!ref && messageId) {
        const cached = refsCache.current.get(messageId);
        ref = cached?.find((r) => r.label === label);
        if (!ref) {
          const trace = await chatApi.messageTrace(messageId);
          ref = trace.refs.find((r) => r.label === label);
        }
      }
      if (!ref) {
        setNotice('该引用未找到对应依据');
        return;
      }
      if (ref.url) {
        window.open(ref.url, '_blank', 'noopener');
        return;
      }
      if (ref.documentId) {
        const { url } = await documentsApi.contentUrl(ref.documentId);
        window.open(url, '_blank', 'noopener');
      }
    } catch (err) {
      setNotice((err as Error).message);
    }
  };

  const messages: ChatMessage[] = useMemo(() => {
    const server = (detailQuery.data?.messages ?? []).filter(isRenderableMessage);
    const merged: ChatMessage[] = [...server];
    if (echoVisible) {
      merged.push({ id: '__user', role: 'user', content: echoVisible, createdAt: null, redFlagHitId: null, errorState: null });
    }
    return merged;
  }, [detailQuery.data?.messages, echoVisible]);

  const draftsByMessage = useMemo(() => {
    const map = new Map<string, Array<Draft & { subjectName: string; subjectAvatar: string | null }>>();
    for (const d of detailQuery.data?.drafts ?? []) {
      const arr = map.get(d.messageId) ?? [];
      arr.push({ ...d, subjectName: current?.name ?? '', subjectAvatar: current?.avatar ?? null });
      map.set(d.messageId, arr);
    }
    return map;
  }, [detailQuery.data?.drafts, current]);

  /** 服务端已回拉的草稿 id（避免与 live.drafts 重复渲染同一张卡片） */
  const resolvedDraftIds = useMemo(
    () => (detailQuery.data?.drafts ?? []).map((d) => d.id),
    [detailQuery.data?.drafts],
  );
  const liveDrafts = useMemo(
    () => excludeResolvedDrafts(liveVisible?.drafts ?? [], resolvedDraftIds),
    [liveVisible?.drafts, resolvedDraftIds],
  );

  if (!current) {
    return <div className="empty-state">请先选择档案对象（顶部切换器）后再开始问诊</div>;
  }

  return (
    <div className="chat-page">
      <aside className="chat-sidebar">
        <header className="chat-sidebar__header">
          <h3>会话</h3>
          <button
            type="button"
            className="btn btn--sm btn--primary"
            onClick={() => {
              void chatApi.createConversation(current.id).then((conv) => {
                setActiveId(conv.id);
                navigate(`/app/chat/${conv.id}`, { replace: true });
                void convsQuery.refetch();
              });
            }}
          >
            新建
          </button>
        </header>
        <ul className="chat-conv-list">
          {(convsQuery.data ?? [])
            .filter((c) => c.subjectId === current.id)
            .map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={c.id === activeId ? 'active' : ''}
                  onClick={() => {
                    setActiveId(c.id);
                    navigate(`/app/chat/${c.id}`);
                  }}
                >
                  <span className="chat-conv-list__title">{c.title || '新会话'}</span>
                  <span className="muted small">{c.lastMessageAt?.slice(0, 16) ?? ''}</span>
                </button>
              </li>
            ))}
        </ul>
        {activeId && (
          <footer className="chat-sidebar__footer">
            <button
              type="button"
              className="btn btn--sm btn--ghost"
              onClick={() =>
                void chatApi.closeConversation(activeId).then(() => {
                  setActiveId(null);
                  void convsQuery.refetch();
                })
              }
            >
              结束当前会话
            </button>
          </footer>
        )}
      </aside>

      <section className="chat-main">
        <div className="chat-stream" ref={scrollRef}>
          {messages.length === 0 && !liveVisible && (
            <div className="empty-state">
              <p>
                就「{current.name}」的档案提问吧，例如：血糖最近怎么样 / 上次体检异常项。
              </p>
              <p className="muted small">回答会带依据，点击引用可回看原始报告。</p>
            </div>
          )}

          {messages.map((m) => (
            <div key={m.id} className={`chat-msg chat-msg--${m.role}`}>
              <div className="chat-msg__bubble">
                {m.role === 'assistant' ? (
                  m.content.trim() ? (
                    <MarkdownMessage
                      text={m.content}
                      onCite={(label) => void onCite(label, m.id === '__user' ? undefined : m.id)}
                    />
                  ) : null
                ) : (
                  <CitationTag text={m.content} />
                )}
                {m.errorState && <span className="muted small">（{m.errorState}）</span>}
              </div>
              {(draftsByMessage.get(m.id) ?? []).map((d) => (
                <DraftCard
                  key={d.id}
                  draft={d}
                  subjectName={d.subjectName}
                  subjectAvatar={d.subjectAvatar}
                  onResolved={() => void detailQuery.refetch()}
                />
              ))}
            </div>
          ))}

          {liveVisible && (
            <div className="chat-msg chat-msg--assistant">
              {liveVisible.redFlags.map((rf) => (
                <RedFlagBanner key={rf.code} code={rf.code} severity={rf.severity as 'high'} message={rf.message} />
              ))}
              <div className="chat-msg__bubble">
                {liveVisible.content ? (
                  <>
                    <MarkdownMessage
                      text={liveVisible.content}
                      onCite={(label) => void onCite(label, liveVisible.assistantId || undefined)}
                    />
                    {/* 只要本轮还在进行（有 live 块）就显示光标，
                        不完全依赖 status，避免 done 前的最后几帧闪烁 */}
                    {(status === 'streaming' || liveVisible.hasContent) && (
                      <span className="typing-caret" aria-hidden="true" />
                    )}
                  </>
                ) : (
                  <StageIndicator
                    stage={liveVisible.stage}
                    stageDetail={liveVisible.stageDetail}
                    waitedMs={waitedMs}
                    hasContent={liveVisible.hasContent}
                  />
                )}
                {liveVisible.error && <span className="chat-msg__error">（{liveVisible.error.message}）</span>}
              </div>
              {liveVisible.sanitizer && <p className="chat-msg__sanitizer">{liveVisible.sanitizer}</p>}
              {liveDrafts.map((d) => (
                <DraftCard
                  key={d.id}
                  draft={d}
                  subjectName={d.subjectName}
                  subjectAvatar={d.subjectAvatar}
                  onResolved={() => {
                    setLive((l) => (l ? { ...l, drafts: l.drafts.filter((x) => x.id !== d.id) } : l));
                    void detailQuery.refetch();
                  }}
                />
              ))}
            </div>
          )}
        </div>

        {notice && (
          <p className="notice notice--info">
            {notice}
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setNotice(null)}>
              知道了
            </button>
          </p>
        )}

        <footer className="chat-input">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void doSend();
              }
            }}
            placeholder={activeId ? '输入问题，Enter 发送；说“帮我记一下…”可录入档案' : '请先选择档案对象'}
            disabled={!activeId || status === 'streaming'}
            rows={2}
          />
          {status === 'streaming' ? (
            <button type="button" className="btn btn--ghost" onClick={cancel}>
              停止
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--primary"
              disabled={!activeId || !input.trim()}
              onClick={() => void doSend()}
            >
              发送
            </button>
          )}
        </footer>
        {streamError && status === 'error' && (
          <p className="notice notice--warning">
            {streamError.message}
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => void doSend(lastSent)}>
              重试
            </button>
          </p>
        )}
      </section>
    </div>
  );
}
