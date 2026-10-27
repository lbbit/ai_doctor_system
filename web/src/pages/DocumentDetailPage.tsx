import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import * as pdfjs from 'pdfjs-dist';
import ConfirmCard from '../components/ConfirmCard';
import MarkdownMessage from '../components/MarkdownMessage';
import { stripSourceAnchors } from '../components/markdownTokens';
import {
  documentStatusLabels,
  estimateHint,
  formatElapsed,
  progressPercent,
  STAGE_LABELS,
  stageHint,
} from '../components/extractionProgress';
import { documentsApi } from '../api/documents';
import { useSubjectStore } from '../stores/subjectStore';
import {
  DerivedRecord,
  formatOccurredAt,
  RECORD_TYPE_LABELS,
  TIME_PRECISION_LABELS,
} from '../types/api';

// pdfjs worker（Vite 下用 CDN 源）
pdfjs.GlobalWorkerOptions.workerSrc = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

/** F-13/F-05/F-06 文档详情：原件预览 + 抽取草稿确认卡片。 */
export default function DocumentDetailPage() {
  const { documentId = '' } = useParams();
  const current = useSubjectStore((s) => s.current);
  const [dr, setDr] = useState<DerivedRecord | null>(null);
  const [contentUrl, setContentUrl] = useState<string | null>(null);
  const [pageUrls, setPageUrls] = useState<Array<{ pageNo: number; url: string }>>([]);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ['document', documentId, current?.id],
    queryFn: () => documentsApi.detail(documentId, current!.id),
    enabled: Boolean(documentId && current?.id),
    refetchInterval: (query) => {
      const d = query.state.data;
      return d && (d.document.status === 'pending' || d.document.status === 'extracting')
        ? 3000
        : false;
    },
  });

  const drQuery = useQuery({
    queryKey: ['derived-record', documentId, current?.id],
    queryFn: () => documentsApi.derivedRecord(documentId, current!.id),
    enabled: Boolean(documentId && current?.id),
    retry: false,
    // 重新抽取完成后（状态回到 awaiting_review）自动拉取新草稿
    refetchInterval: () => {
      const d = detail.data?.document;
      if (!d) return false;
      return d.status === 'extracting' || d.status === 'pending' ? 3000 : false;
    },
  });

  useEffect(() => {
    if (drQuery.data) setDr(drQuery.data);
  }, [drQuery.data]);

  // R1 T107 四区数据：副本 / 已确认结构化事实 / 版本历史
  // 仅在文档已产出草稿或已确认后拉取（pending/extracting 期无意义）。
  const settled =
    !!detail.data &&
    !['pending', 'extracting'].includes(detail.data.document.status);

  const mdQuery = useQuery({
    queryKey: ['doc-markdown', documentId, current?.id],
    queryFn: () => documentsApi.markdown(documentId, current!.id),
    enabled: Boolean(documentId && current?.id && settled),
    retry: false,
  });
  const recordsQuery = useQuery({
    queryKey: ['doc-records', documentId, current?.id],
    queryFn: () => documentsApi.records(documentId, current!.id),
    enabled: Boolean(documentId && current?.id && settled),
    retry: false,
  });
  const versionsQuery = useQuery({
    queryKey: ['doc-versions', documentId, current?.id],
    queryFn: () => documentsApi.versions(documentId, current!.id),
    enabled: Boolean(documentId && current?.id && settled),
    retry: false,
  });

  // 原件 URL（带时效）
  useEffect(() => {
    if (!documentId || !current?.id) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await documentsApi.contentUrl(documentId, current.id);
        if (!cancelled) setContentUrl(res.url);
        const pages = await documentsApi.pageUrls(documentId, current.id);
        if (!cancelled) setPageUrls(pages);
      } catch {
        if (!cancelled) setContentUrl(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [documentId, current?.id, detail.data?.document.status]);

  if (!current) return <div className="empty-state">请先选择档案对象</div>;
  if (detail.isLoading) return <p className="muted">加载中…</p>;
  if (detail.isError || !detail.data) {
    return (
      <div className="empty-state">
        <p>{(detail.error as Error | null)?.message ?? '文档不存在或无权访问'}</p>
        <Link to="/app/archive">返回档案列表</Link>
      </div>
    );
  }

  const doc = detail.data.document;
  const job = detail.data.job;
  const inFlight = doc.status === 'pending' || doc.status === 'extracting';
  const pct = progressPercent(job);
  const stageText = job?.stage ? STAGE_LABELS[job.stage] ?? job.stage : '排队中';
  const labels = documentStatusLabels(doc);

  return (
    <div className="doc-detail">
      <header className="doc-detail__header">
        <Link to="/app/archive" className="btn btn--ghost btn--sm">
          ← 返回
        </Link>
        <h2>{doc.fileName}</h2>
        <span className={`doc-status doc-status--${doc.status}`}>{labels.primary}</span>
        {labels.lifecycle && (
          <span
            className="doc-status doc-status--archived"
            title="已归档：原件仍保留，可在原处查看；取消归档即可恢复"
          >
            {labels.lifecycle}
          </span>
        )}
        {labels.needsReconcile && (
          <span
            className="doc-status doc-status--failed"
            title="该记录的处理状态无法从历史数据推断，需要人工确认"
          >
            状态待核对
          </span>
        )}
      </header>

      {inFlight && (
        <section className="progress-card" aria-live="polite">
          <div className="progress-card__head">
            <strong>{stageText}</strong>
            <span className="muted small">
              已用时 {formatElapsed(job?.elapsedSeconds ?? 0)}
              {job?.attempt ? ` · 第 ${job.attempt + 1} 次尝试` : ''}
            </span>
          </div>
          <div
            className={`progress-bar ${pct == null ? 'progress-bar--indeterminate' : ''}`}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct ?? undefined}
          >
            <div className="progress-bar__fill" style={pct == null ? undefined : { width: `${pct}%` }} />
          </div>
          <p className="small progress-card__hint">{stageHint(job)}</p>
          {estimateHint(job) && <p className="small muted">{estimateHint(job)}</p>}
          {job?.stale && (
            <p className="notice notice--warning">
              该任务已超出预期处理时间，服务端会自动回收并重试；如超过 5 分钟仍未变化，可点击下方“重新抽取”。
            </p>
          )}
          <p className="small muted">
            页面每 3 秒自动刷新；可以关闭页面稍后回来看结果。
          </p>
        </section>
      )}

      {doc.status === 'failed' && (
        <div className="notice notice--warning">
          <p>解析失败：{doc.failureReason ?? '未知原因'}</p>
          <button
            type="button"
            className="btn btn--sm btn--primary"
            onClick={() =>
              void documentsApi
                .retry(doc.id, current.id)
                .then(() => detail.refetch())
                .catch((e) => setMessage((e as Error).message))
            }
          >
            重试
          </button>
        </div>
      )}

      {doc.status === 'awaiting_review' && dr && (
        <>
          {dr.payload.page_errors && dr.payload.page_errors.length > 0 && (
            <div className="notice notice--warning">
              <p>
                部分页面未能识别（共 {dr.payload.page_count ?? '?'} 页，草稿基于已识别页生成）：
              </p>
              <ul className="small">
                {dr.payload.page_errors.map((msg) => (
                  <li key={msg}>{msg}</li>
                ))}
              </ul>
              <p className="small">可稍后点击“重新抽取”补全缺失页。</p>
            </div>
          )}
          <ConfirmCard
            docId={doc.id}
            subjectId={current.id}
            docStatus={doc.status}
            derivedRecord={dr}
            onConfirmed={() => {
              setMessage('已确认入库');
              void detail.refetch();
              void drQuery.refetch();
            }}
            onReextracted={() => {
              setMessage('已提交重新抽取，可在上方查看进度');
              void detail.refetch();
            }}
          />
        </>
      )}

      {doc.status === 'confirmed' && (
        <p className="notice notice--success">该报告已确认入库（AC-6.4 幂等，不可重复确认）。</p>
      )}
      {doc.status === 'parsed_no_data' && (
        <p className="notice notice--info">该报告已解析，但全部字段被拒绝，未产生事件。</p>
      )}

      {message && <p className="notice notice--info">{message}</p>}

      {/* 来源元数据（R1 T101）：发生时间按精度展示，未知不伪装 */}
      <section className="panel doc-meta">
        <h3>记录信息</h3>
        <dl className="doc-meta__grid">
          <div>
            <dt>发生时间</dt>
            <dd>{formatOccurredAt(doc)}</dd>
          </div>
          <div>
            <dt>时间精度</dt>
            <dd>{TIME_PRECISION_LABELS[doc.timePrecision ?? 'unknown']}</dd>
          </div>
          <div>
            <dt>记录类型</dt>
            <dd>{doc.recordType ? RECORD_TYPE_LABELS[doc.recordType] ?? doc.recordType : '—'}</dd>
          </div>
          <div>
            <dt>来源机构</dt>
            <dd>{doc.facility ?? '—'}</dd>
          </div>
          {doc.department && (
            <div>
              <dt>科室</dt>
              <dd>{doc.department}</dd>
            </div>
          )}
        </dl>
      </section>

      {/* ① 原件预览（证据层，永不删除） */}
      <section className="panel">
        <h3>① 原件预览</h3>
        {doc.kind === 'IMAGE' && contentUrl && (
          <img className="doc-preview__image" src={contentUrl} alt={doc.fileName} />
        )}
        {doc.kind === 'PDF' && contentUrl && <PdfPreview url={contentUrl} onError={setPdfError} />}
        {doc.kind === 'TEXT' && mdQuery.data?.available && (
          <p className="muted small">纯文本记录，见下方"Markdown 副本"。</p>
        )}
        {!contentUrl && doc.kind !== 'TEXT' && (
          <p className="muted">原件链接生成中…（失效会自动刷新）</p>
        )}
        {pdfError && <p className="notice notice--warning">{pdfError}</p>}
        {pageUrls.length > 0 && (
          <div className="doc-preview__pages">
            {pageUrls.map((p) => (
              <img key={p.pageNo} src={p.url} alt={`第 ${p.pageNo} 页`} />
            ))}
          </div>
        )}
      </section>

      {/* ② Markdown 副本（派生、可重建、版本化） */}
      <section className="panel">
        <h3>② Markdown 副本</h3>
        {!settled && <p className="muted">处理完成后生成副本。</p>}
        {settled && mdQuery.isLoading && <p className="muted">加载副本中…</p>}
        {settled && mdQuery.data && !mdQuery.data.available && (
          <p className="muted">该文档尚无 Markdown 副本（可能解析失败或为纯图像件）。</p>
        )}
        {settled && mdQuery.data?.available && (
          <>
            {mdQuery.data.stale && (
              <p className="notice notice--warning">
                源文件已变化，此副本可能过期，建议重新抽取。
              </p>
            )}
            <p className="muted small">
              生成器 {mdQuery.data.generator} · 版本 v{mdQuery.data.version} ·
              副本为派生产物，以原件为准
            </p>
            <article className="doc-markdown">
              <MarkdownMessage text={stripSourceAnchors(mdQuery.data.content ?? '')} />
            </article>
          </>
        )}
      </section>

      {/* ③ 结构化记录（已确认的当前有效事实） */}
      <section className="panel">
        <h3>③ 结构化记录（已确认）</h3>
        {settled && recordsQuery.isLoading && <p className="muted">加载中…</p>}
        {settled && recordsQuery.data && (
          <>
            {recordsQuery.data.events.length === 0 &&
              recordsQuery.data.observations.length === 0 && (
                <p className="muted">
                  暂无已确认记录。{doc.status === 'awaiting_review' ? '请在上方确认草稿。' : ''}
                </p>
              )}
            {recordsQuery.data.events.length > 0 && (
              <ul className="doc-records__events">
                {recordsQuery.data.events.map((e) => (
                  <li key={e.id}>
                    {e.occurredAt?.slice(0, 10)} · {e.title ?? e.eventType}
                  </li>
                ))}
              </ul>
            )}
            {recordsQuery.data.observations.length > 0 && (
              <div className="table-scroll">
                <table className="doc-records__table">
                  <thead>
                    <tr>
                      <th>项目</th>
                      <th>结果</th>
                      <th>单位</th>
                      <th>异常</th>
                      <th>日期</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recordsQuery.data.observations.map((o) => (
                      <tr key={o.id}>
                        <td>{o.displayName}</td>
                        <td>{o.valueNum ?? o.valueText ?? ''}</td>
                        <td>{o.unit ?? ''}</td>
                        <td>
                          {o.abnormalFlag === 'H'
                            ? '偏高'
                            : o.abnormalFlag === 'L'
                              ? '偏低'
                              : o.abnormalFlag === 'A'
                                ? '异常'
                                : ''}
                        </td>
                        <td>{o.occurredAt?.slice(0, 10)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      {/* ④ 处理与版本历史 */}
      <section className="panel">
        <h3>④ 处理与版本历史</h3>
        {settled && versionsQuery.isLoading && <p className="muted">加载中…</p>}
        {settled && versionsQuery.data && (
          <>
            <h4 className="small">抽取草稿版本</h4>
            <ul className="doc-versions__list">
              {versionsQuery.data.drafts.map((d) => (
                <li key={d.id}>
                  v{d.version} · {d.parser} ·{' '}
                  {d.confirmedAt
                    ? `已确认 ${d.confirmedAt.slice(0, 10)}`
                    : '未确认'}
                </li>
              ))}
            </ul>
            <h4 className="small">派生资产</h4>
            <ul className="doc-versions__list">
              {versionsQuery.data.assets.map((a) => (
                <li key={a.id}>
                  {a.role} v{a.version} · {a.generator}
                  {a.stale ? ' ·（源已变化，待重生成）' : ''}
                </li>
              ))}
              {versionsQuery.data.assets.length === 0 && (
                <li className="muted">无派生资产</li>
              )}
            </ul>
          </>
        )}
      </section>
    </div>
  );
}

function PdfPreview({ url, onError }: { url: string; onError: (msg: string) => void }) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const task = pdfjs.getDocument(url);
        const pdf = await task.promise;
        if (cancelled) return;
        const page = await pdf.getPage(1);
        const viewport = page.getViewport({ scale: 1.2 });
        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('canvas 初始化失败');
        await page.render({ canvasContext: ctx, viewport }).promise;
        const container = document.getElementById('pdf-canvas-host');
        if (container && !cancelled) {
          container.innerHTML = '';
          canvas.style.maxWidth = '100%';
          container.appendChild(canvas);
        }
      } catch (err) {
        if (!cancelled) {
          const msg = 'PDF 预览失败（链接可能过期，请稍候刷新页面）：' + (err as Error).message;
          setError(msg);
          onError(msg);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url, onError]);
  if (error) return null;
  return <div id="pdf-canvas-host" className="doc-preview__pdf" />;
}
