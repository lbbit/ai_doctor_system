import { useMemo, useState } from 'react';
import { documentsApi } from '../api/documents';
import {
  CONFIDENCE_THRESHOLD,
  bulkConfirmRefs,
  bulkRejectRefs,
  confirmStats,
  groupObservations,
  type ConfirmFilter,
} from './confirmBulk';
import type {
  ConfirmResult,
  Decision,
  DerivedRecord,
  ExtractionJob,
  ObsDraft,
} from '../types/api';
import { ABNORMAL_LABELS } from '../types/api';

export interface ConfirmCardProps {
  docId: string;
  /** 当前档案：所有写请求都带它，服务端做跨档校验（K4 零串档） */
  subjectId: string;
  docStatus: string;
  derivedRecord: DerivedRecord;
  onConfirmed: (result: ConfirmResult) => void;
  /** 重新抽取已异步入队（服务端 worker 处理），由详情页接管进度展示 */
  onReextracted: (job?: ExtractionJob) => void;
}

type Decisions = Record<string, Decision>;

/** F-06 HITL 确认卡片：低置信/异常必确认，高置信正常项可一键通过（AC-6.1）。 */
export default function ConfirmCard({
  docId,
  subjectId,
  derivedRecord,
  onConfirmed,
  onReextracted,
}: ConfirmCardProps) {
  const { payload, version, confidenceSummary } = derivedRecord;
  const [decisions, setDecisions] = useState<Decisions>({});
  const [editFor, setEditFor] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reextracting, setReextracting] = useState(false);
  const [filter, setFilter] = useState<ConfirmFilter>('all');

  const stats = useMemo(() => confirmStats(derivedRecord), [derivedRecord]);
  const groups = useMemo(
    () =>
      filter === 'attention'
        ? groupObservations(payload.observations).filter(
            (g) => g.key === 'abnormal' || g.key === 'lowConfidence',
          )
        : groupObservations(payload.observations),
    [payload.observations, filter],
  );
  const visibleCount = groups.reduce((sum, g) => sum + g.items.length, 0);
  const rejectable = useMemo(() => bulkRejectRefs(derivedRecord), [derivedRecord]);

  const fieldRefs = [
    ...payload.events.map((_, i) => `event:${i}`),
    ...payload.observations.map((_, i) => `obs:${i}`),
  ];
  const undecided = fieldRefs.filter((ref) => !decisions[ref]);

  const setDecision = (ref: string, decision: Decision) => {
    setDecisions((prev) => ({ ...prev, [ref]: decision }));
    setError(null);
  };

  /** 一键确认全部高置信且非异常项（异常/低置信仍留给人工）。 */
  const confirmAllQuick = () => {
    const refs = bulkConfirmRefs(derivedRecord);
    setDecisions((prev) => {
      const next = { ...prev };
      for (const ref of refs) {
        if (!next[ref]) next[ref] = { fieldRef: ref, action: 'confirm' };
      }
      return next;
    });
    setError(null);
  };

  const confirmAllEvents = () => {
    setDecisions((prev) => {
      const next = { ...prev };
      payload.events.forEach((_, i) => {
        const ref = `event:${i}`;
        if (!next[ref]) next[ref] = { fieldRef: ref, action: 'confirm' };
      });
      return next;
    });
    setError(null);
  };

  /** 批量拒绝描述型行（“未见明显异常”等），不碰真实数值与异常项。 */
  const rejectAllDescriptions = () => {
    setDecisions((prev) => {
      const next = { ...prev };
      for (const ref of rejectable) {
        if (!next[ref]) next[ref] = { fieldRef: ref, action: 'reject' };
      }
      return next;
    });
    setError(null);
  };

  const submit = async () => {
    if (undecided.length > 0) {
      setError(`还有 ${undecided.length} 个字段未决策：${undecided.join('、')}`);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await documentsApi.confirm(
        docId,
        version,
        fieldRefs.map((ref) => decisions[ref]),
        subjectId,
      );
      onConfirmed(result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const reextract = async () => {
    setReextracting(true);
    setError(null);
    try {
      const { job } = await documentsApi.reextract(docId, subjectId);
      setDecisions({});
      setEditFor(null);
      onReextracted(job);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setReextracting(false);
    }
  };

  return (
    <section className="confirm-card">
      <header className="confirm-card__header">
        <h3>抽取草稿（v{version} · {derivedRecord.parser}）</h3>
        <button type="button" className="btn btn--ghost" onClick={reextract} disabled={reextracting}>
          {reextracting ? '重新抽取中…' : '重新抽取'}
        </button>
      </header>

      {confidenceSummary?.mock && (
        <div className="notice notice--warning">
          <p>
            <strong>未获得 AI 识别结果</strong>
            ：这份报告没有可用的文本层，且视觉模型本轮未返回结果（常见原因：网关超时 / 模型额度不足）。
            当前显示的是**占位记录，不含任何医学数值**。
          </p>
          <p className="small">
            建议：① 先点右上角“重新抽取”再试一次（会逐页识别，可在详情页看到进度）；
            ② 若多次失败，请把报告内容手动录入，或拒绝占位记录后重新上传更清晰的扫描件。
          </p>
        </div>
      )}

      {payload.observations.length === 0 && !confidenceSummary?.mock && (
        <p className="notice notice--info">
          本报告未识别到数值型指标（可能是影像/病历类报告）。可确认下方事件，或重新抽取。
        </p>
      )}
      {confidenceSummary?.belowThreshold && confidenceSummary.belowThreshold.length > 0 && (
        <p className="notice notice--warning">
          以下字段置信度低于 {CONFIDENCE_THRESHOLD}，必须确认或修正：{' '}
          {confidenceSummary.belowThreshold.slice(0, 12).join('、')}
          {confidenceSummary.belowThreshold.length > 12
            ? ` 等 ${confidenceSummary.belowThreshold.length} 项`
            : ''}
        </p>
      )}

      {derivedRecord.comparison && derivedRecord.comparison.previousVersion > 0 && (
        <p className="notice notice--info">
          本次为第 {version} 版草稿（上一版 v{derivedRecord.comparison.previousVersion}）：
          新增 {derivedRecord.comparison.added} 项、减少 {derivedRecord.comparison.removed} 项、
          数值变化 {derivedRecord.comparison.changed} 项。
          {derivedRecord.comparison.addedNames.length > 0 && (
            <>
              {' '}
              新增示例：{derivedRecord.comparison.addedNames.slice(0, 5).join('、')}
            </>
          )}
        </p>
      )}

      <div className="confirm-toolbar">
        <span className="muted small">
          共 {stats.total} 条，其中需你确认 {stats.attention} 条、可直接通过 {stats.quick} 条
        </span>
        <div className="confirm-toolbar__actions">
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => setFilter(filter === 'all' ? 'attention' : 'all')}
            disabled={stats.attention === 0}
          >
            {filter === 'all' ? `只看需确认（${stats.attention}）` : '显示全部'}
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={confirmAllEvents}
            disabled={payload.events.length === 0}
          >
            事件全部确认
          </button>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={rejectAllDescriptions}
            disabled={rejectable.length === 0}
            title="“未见明显异常/无/正常”等描述行，不是可趋势化的数值指标"
          >
            拒绝描述行（{rejectable.length}）
          </button>
          <button
            type="button"
            className="btn btn--sm btn--primary"
            onClick={confirmAllQuick}
            disabled={stats.quick === 0}
          >
            一键确认高置信项（{stats.quick}）
          </button>
        </div>
      </div>

      {payload.events.length > 0 && <h4>事件</h4>}
      {payload.events.map((ev, i) => {
        const ref = `event:${i}`;
        const decision = decisions[ref];
        return (
          <div key={ref} className={`field-card ${decision ? `field-card--${decision.action}` : ''}`}>
            <div className="field-card__head">
              <strong>{ev.title || '（未命名事件）'}</strong>
              <span className="muted small">
                {ev.occurred_at ?? payload.report_date ?? '日期未知'} · 置信度{' '}
                {ev.confidence != null ? ev.confidence.toFixed(2) : '—'}
              </span>
            </div>
            {ev.narrative && <p className="small">{ev.narrative}</p>}
            <DecisionButtons
              refKey={ref}
              decision={decision}
              onChange={setDecision}
            />
          </div>
        );
      })}

      {payload.observations.length > 0 && (
        <h4>
          指标（{payload.observations.length} 项
          {filter === 'attention' ? `，当前显示 ${visibleCount} 项需确认` : ''}）
        </h4>
      )}
      {filter === 'attention' && visibleCount === 0 && (
        <p className="notice notice--success">
          所有指标都是高置信且非异常，可直接点“确认入库”。
        </p>
      )}
      {groups.map((group) => (
        <section key={group.key} className="obs-group">
          <h5 className="obs-group__title">
            {group.label}
            <span className="muted small">（{group.items.length}）</span>
          </h5>
          {group.items.map(({ obs, index: i }) => {
            const ref = `obs:${i}`;
            const decision = decisions[ref];
            const lowConf = obs.confidence != null && obs.confidence < CONFIDENCE_THRESHOLD;
            const editing = editFor === ref;
            const abnormal = obs.abnormal_flag && obs.abnormal_flag !== 'N';
            return (
              <div
                key={ref}
                className={`field-card ${lowConf ? 'field-card--lowconf' : ''} ${
                  abnormal ? 'field-card--abnormal' : ''
                } ${decision ? `field-card--${decision.action}` : ''}`}
              >
                <div className="field-card__head">
                  <strong>{obs.display_name || obs.raw_name}</strong>
                  {obs.display_name && obs.display_name !== obs.raw_name && (
                    <span className="muted small">原文：{obs.raw_name}</span>
                  )}
                  {obs.code ? (
                    <span className="muted small">
                      {obs.code} · {obs.unit ?? ''}
                    </span>
                  ) : (
                    <span className="badge badge--warn">字典未命中，已按原文保存</span>
                  )}
                  <span className="muted small">
                    置信度 {obs.confidence != null ? obs.confidence.toFixed(2) : '—'}
                  </span>
                </div>
                <div className="field-card__value">
                  <span className={abnormal ? 'value value--abnormal' : 'value'}>
                    {obs.value ?? '—'} {obs.unit ?? ''}
                  </span>
                  {(obs.ref_low != null || obs.ref_high != null || obs.ref_text) && (
                    <span className="muted small">
                      参考：{obs.ref_low ?? '−'} ~ {obs.ref_high ?? '∞'}
                      {obs.ref_text ? `（${obs.ref_text}）` : ''}
                    </span>
                  )}
                  {obs.abnormal_flag && (
                    <span className={`badge badge--flag-${obs.abnormal_flag}`}>
                      {ABNORMAL_LABELS[obs.abnormal_flag]}
                    </span>
                  )}
                </div>

                {editing ? (
                  <ObsEditor
                    obs={obs}
                    onSave={(value) => {
                      setDecision(ref, { fieldRef: ref, action: 'correct', value });
                      setEditFor(null);
                    }}
                    onCancel={() => setEditFor(null)}
                  />
                ) : (
                  <DecisionButtons
                    refKey={ref}
                    decision={decision}
                    onChange={setDecision}
                    onCorrect={() => setEditFor(ref)}
                  />
                )}
              </div>
            );
          })}
        </section>
      ))}

      {error && <p className="form-error">{error}</p>}

      <footer className="confirm-card__footer">
        <span className="muted small">
          已决策 {fieldRefs.length - undecided.length} / {fieldRefs.length}
          {undecided.length > 0 && '（P0 全部字段必须显式决策）'}
        </span>
        <button
          type="button"
          className="btn btn--primary"
          disabled={submitting || undecided.length > 0}
          onClick={submit}
        >
          {submitting ? '入库中…' : '确认入库'}
        </button>
      </footer>
    </section>
  );
}

function DecisionButtons({
  refKey,
  decision,
  onChange,
  onCorrect,
}: {
  refKey: string;
  decision?: Decision;
  onChange: (ref: string, d: Decision) => void;
  onCorrect?: () => void;
}) {
  return (
    <div className="decision-buttons">
      <button
        type="button"
        className={`btn btn--sm ${decision?.action === 'confirm' ? 'btn--primary' : 'btn--ghost'}`}
        onClick={() => onChange(refKey, { fieldRef: refKey, action: 'confirm' })}
      >
        确认
      </button>
      {onCorrect && (
        <button
          type="button"
          className={`btn btn--sm ${decision?.action === 'correct' ? 'btn--primary' : 'btn--ghost'}`}
          onClick={onCorrect}
        >
          修正
        </button>
      )}
      <button
        type="button"
        className={`btn btn--sm ${decision?.action === 'reject' ? 'btn--danger' : 'btn--ghost'}`}
        onClick={() => onChange(refKey, { fieldRef: refKey, action: 'reject' })}
      >
        拒绝
      </button>
    </div>
  );
}

function ObsEditor({
  obs,
  onSave,
  onCancel,
}: {
  obs: ObsDraft;
  onSave: (value: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(obs.display_name || obs.raw_name || '');
  const [value, setValue] = useState(String(obs.value ?? ''));
  const [unit, setUnit] = useState(obs.unit ?? '');
  const [refLow, setRefLow] = useState(obs.ref_low != null ? String(obs.ref_low) : '');
  const [refHigh, setRefHigh] = useState(obs.ref_high != null ? String(obs.ref_high) : '');
  const [flag, setFlag] = useState(obs.abnormal_flag ?? '');

  return (
    <div className="obs-editor">
      <label>
        指标名
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="如 红细胞压积"
        />
      </label>
      <label>
        数值
        <input value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      <label>
        单位
        <input value={unit} onChange={(e) => setUnit(e.target.value)} />
      </label>
      <label>
        参考下限
        <input value={refLow} onChange={(e) => setRefLow(e.target.value)} />
      </label>
      <label>
        参考上限
        <input value={refHigh} onChange={(e) => setRefHigh(e.target.value)} />
      </label>
      <label>
        标记
        <select value={flag} onChange={(e) => setFlag(e.target.value)}>
          <option value="">未标记</option>
          <option value="N">正常</option>
          <option value="L">偏低</option>
          <option value="H">偏高</option>
          <option value="A">异常</option>
        </select>
      </label>
      <div className="obs-editor__actions">
        <button type="button" className="btn btn--sm btn--ghost" onClick={onCancel}>
          取消
        </button>
        <button
          type="button"
          className="btn btn--sm btn--primary"
          onClick={() => {
            const patch: Record<string, unknown> = {
              value,
              unit,
              ref_low: refLow === '' ? null : Number(refLow),
              ref_high: refHigh === '' ? null : Number(refHigh),
              abnormal_flag: flag || null,
            };
            // 改名才提交：后端据此重新匹配字典（带出标准 code/参考区间）
            const renamed = name.trim();
            if (renamed && renamed !== (obs.display_name || obs.raw_name)) {
              patch.display_name = renamed;
            }
            onSave(patch);
          }}
        >
          保存修正
        </button>
      </div>
    </div>
  );
}
