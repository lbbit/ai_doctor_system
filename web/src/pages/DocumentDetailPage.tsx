import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import * as pdfjs from 'pdfjs-dist';
import ConfirmCard from '../components/ConfirmCard';
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
import { DerivedRecord } from '../types/api';

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

      <section className="panel">
        <h3>原件预览</h3>
        {doc.kind === 'IMAGE' && contentUrl && (
          <img className="doc-preview__image" src={contentUrl} alt={doc.fileName} />
        )}
        {doc.kind === 'PDF' && contentUrl && <PdfPreview url={contentUrl} onError={setPdfError} />}
        {!contentUrl && <p className="muted">原件链接生成中…（失效会自动刷新）</p>}
        {pdfError && <p className="notice notice--warning">{pdfError}</p>}
        {pageUrls.length > 0 && (
          <div className="doc-preview__pages">
            {pageUrls.map((p) => (
              <img key={p.pageNo} src={p.url} alt={`第 ${p.pageNo} 页`} />
            ))}
          </div>
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
