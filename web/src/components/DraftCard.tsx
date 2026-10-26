import { useState } from 'react';
import { chatApi } from '../api/chat';
import type { Draft } from '../types/api';

/** F-12 对话式草稿卡片：先草稿后入库（A-8）。 */
export interface DraftCardProps {
  draft: Draft;
  subjectName: string;
  subjectAvatar: string | null;
  onResolved: () => void;
}

export default function DraftCard({
  draft,
  subjectName,
  subjectAvatar,
  onResolved,
}: DraftCardProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(draft.payload.event?.title ?? '');
  const [narrative, setNarrative] = useState(draft.payload.event?.narrative ?? '');

  const confirm = async (payload?: unknown) => {
    setBusy(true);
    setError(null);
    try {
      await chatApi.confirmDraft(draft.id, payload);
      onResolved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    setBusy(true);
    setError(null);
    try {
      await chatApi.discardDraft(draft.id);
      onResolved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitEdited = () => {
    const payload = {
      event: draft.payload.event
        ? {
            ...draft.payload.event,
            title: title || draft.payload.event.title,
            narrative: narrative || null,
          }
        : null,
      observations: draft.payload.observations,
    };
    void confirm(payload);
  };

  if (draft.status === 'confirmed') {
    return <div className="draft-card draft-card--resolved">✓ 已入库到 {subjectName} 的时间线</div>;
  }
  if (draft.status === 'discarded') {
    return <div className="draft-card draft-card--resolved">已丢弃（未入库）</div>;
  }

  return (
    <div className="draft-card">
      <header className="draft-card__header">
        <span className="draft-card__avatar">{subjectAvatar || '👤'}</span>
        <strong>{subjectName} · 待确认记录</strong>
        <span className="muted small">确认后才写入时间线</span>
      </header>

      {editing ? (
        <div className="draft-card__edit">
          <label>
            标题
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            描述
            <textarea value={narrative} onChange={(e) => setNarrative(e.target.value)} rows={2} />
          </label>
        </div>
      ) : (
        <div className="draft-card__body">
          {draft.payload.event && (
            <p>
              <strong>{draft.payload.event.title}</strong>
              {draft.payload.event.narrative ? ` — ${draft.payload.event.narrative}` : ''}
              <span className="muted small">（{draft.payload.event.occurredAt}）</span>
            </p>
          )}
          {draft.payload.observations.length > 0 && (
            <ul>
              {draft.payload.observations.map((o, i) => (
                <li key={i}>
                  {o.displayName}：{o.value ?? '—'} {o.unit ?? ''}
                  {o.confidence != null && (
                    <span className="muted small"> （置信度 {o.confidence.toFixed(2)}）</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {error && <p className="form-error">{error}</p>}

      <footer className="draft-card__actions">
        {editing ? (
          <>
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setEditing(false)}>
              取消编辑
            </button>
            <button type="button" className="btn btn--sm btn--primary" disabled={busy} onClick={submitEdited}>
              编辑后入库
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn btn--sm btn--ghost" onClick={() => setEditing(true)}>
              编辑
            </button>
            <button type="button" className="btn btn--sm btn--danger" disabled={busy} onClick={discard}>
              丢弃
            </button>
            <button
              type="button"
              className="btn btn--sm btn--primary"
              disabled={busy}
              onClick={() => void confirm()}
            >
              确认入库
            </button>
          </>
        )}
      </footer>
    </div>
  );
}
