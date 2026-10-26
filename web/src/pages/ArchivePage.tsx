import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import ConfirmDialog from '../components/ConfirmDialog';
import { listStatusNote, documentStatusLabels } from '../components/extractionProgress';
import { documentsApi } from '../api/documents';
import { useSubjectStore } from '../stores/subjectStore';
import { DOC_STATUS_LABELS, DocStatus, RECORD_TYPE_LABELS, formatOccurredAt } from '../types/api';

/** F-04/F-13 档案页：上传 + 列表 + 原件入口。 */
export default function ArchivePage() {
  const current = useSubjectStore((s) => s.current);
  const fileRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<DocStatus | ''>('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [dupFile, setDupFile] = useState<{ file: File; name: string } | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<{ id: string; name: string } | null>(null);

  const list = useQuery({
    queryKey: ['documents', current?.id, status],
    queryFn: () =>
      documentsApi.list({
        subjectId: current!.id,
        status: status || undefined,
        pageSize: 50,
      }),
    enabled: Boolean(current?.id),
    refetchInterval: (query) => {
      // 处理中文档轮询刷新（§5.1 轮询 pending/extracting）
      const items = query.state.data?.items ?? [];
      return items.some((d) => d.status === 'pending' || d.status === 'extracting')
        ? 3000
        : false;
    },
  });

  const doUpload = async (file: File, force = false) => {
    if (!current) {
      setNotice('请先选择档案对象');
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const doc = await documentsApi.upload({ subjectId: current.id, file, force });
      setNotice(`《${doc.fileName}》已上传，正在解析…`);
      void list.refetch();
    } catch (err) {
      if (documentsApi.isDuplicateError(err)) {
        setDupFile({ file, name: file.name });
      } else {
        setNotice((err as Error).message);
      }
    } finally {
      setBusy(false);
    }
  };

  /** 批量上传：逐项结果，单个失败不影响其他（R1 T105）。 */
  const doUploadMany = async (files: File[]) => {
    if (!current) {
      setNotice('请先选择档案对象');
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const result = await documentsApi.uploadBatch({
        subjectId: current.id,
        files,
      });
      const okCount = result.succeeded;
      const failCount = result.failed;
      if (failCount > 0) {
        const firstError = result.items.find((i) => !i.ok);
        setNotice(
          `已上传 ${okCount} 个，${failCount} 个失败` +
            (firstError ? `（${firstError.fileName}: ${firstError.message ?? firstError.code}）` : ''),
        );
      } else {
        setNotice(`已上传 ${okCount} 个文件，正在解析…`);
      }
      void list.refetch();
    } catch (err) {
      setNotice((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!current) {
    return <div className="empty-state">请先选择档案对象（顶部切换器）</div>;
  }

  return (
    <div className="archive-page">
      <section className="panel">
        <header className="panel__header">
          <h2>上传报告</h2>
          <span className="muted small">
            支持 JPG / PNG / PDF / TXT / MD，可多选，单个 ≤ 20MB
          </span>
        </header>
        <div className="upload-row">
          <input
            ref={fileRef}
            type="file"
            multiple
            accept="image/jpeg,image/png,application/pdf,text/plain,text/markdown,.txt,.md"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) void doUploadMany(files);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
          >
            {busy ? '上传中…' : '选择文件上传'}
          </button>
        </div>
        {notice && <p className="notice notice--info">{notice}</p>}
        {list.data && list.data.total > 0 &&
          (list.data.items ?? []).some(
            (d) => d.status === 'pending' || d.status === 'extracting',
          ) && (
            <p className="small muted">
              有报告正在识别中，列表每 3 秒自动刷新；点开可看到逐页进度。
            </p>
          )}
      </section>

      <section className="panel">
        <header className="panel__header">
          <h2>文档列表</h2>
          <select value={status} onChange={(e) => setStatus(e.target.value as DocStatus | '')}>
            <option value="">全部状态</option>
            {Object.entries(DOC_STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </header>

        {list.data && list.data.total === 0 && (
          <div className="empty-state">
            <p>暂无文档。上传化验单/报告，AI 会帮你抽取成结构化草稿。</p>
          </div>
        )}

        <ul className="doc-list">
          {(list.data?.items ?? []).map((doc) => {
            const note = listStatusNote(doc.status);
            const labels = documentStatusLabels(doc);
            return (
              <li key={doc.id} className="doc-list__row">
                <Link to={`/app/archive/${doc.id}`} className="doc-list__item">
                  <span className="doc-list__name">{doc.fileName}</span>
                  <span className={`doc-status doc-status--${doc.status}`}>
                    {labels.primary}
                  </span>
                  {labels.lifecycle && (
                    <span className="doc-status doc-status--archived">
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
                  <span className="muted small">
                    {formatOccurredAt(doc)} ·{' '}
                    {(doc.sizeBytes / 1024).toFixed(0)} KB
                    {doc.recordType ? ` · ${RECORD_TYPE_LABELS[doc.recordType] ?? doc.recordType}` : ''}
                    {doc.facility ? ` · ${doc.facility}` : ''}
                    {doc.pageCount ? ` · ${doc.pageCount} 页` : ''}
                  </span>
                  {note && <span className="small doc-list__note">{note}</span>}
                </Link>
                {doc.lifecycleStatus !== 'archived' && (
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost doc-list__archive"
                    title="归档后可随时在原处查看，原件不会被删除"
                    onClick={() => setArchiveTarget({ id: doc.id, name: doc.fileName })}
                  >
                    归档
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <ConfirmDialog
        open={Boolean(dupFile)}
        title="已存在相同文件"
        confirmText="仍要上传"
        onCancel={() => setDupFile(null)}
        onConfirm={() => {
          if (dupFile) void doUpload(dupFile.file, true);
          setDupFile(null);
        }}
      >
        <p>同一档案下已上传过内容相同的文件（按内容 hash 判定）。仍要重复上传吗？</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(archiveTarget)}
        title="归档这份报告？"
        confirmText="确认归档"
        onCancel={() => setArchiveTarget(null)}
        onConfirm={() => {
          const target = archiveTarget;
          if (!target || !current) return;
          setArchiveTarget(null);
          void documentsApi
            .archive(target.id, current.id)
            .then(() => {
              setNotice(`《${target.name}》已归档（原件保留，可随时查看）`);
              void list.refetch();
            })
            .catch((err) => setNotice((err as Error).message));
        }}
      >
        <p>
          归档后该报告不再出现在默认处理流程中，但原件与已确认的指标不会删除，
          历史时间线仍可追溯。若只是暂时不想看，建议用上方状态筛选。
        </p>
      </ConfirmDialog>
    </div>
  );
}
