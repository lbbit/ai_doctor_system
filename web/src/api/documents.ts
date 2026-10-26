import client, { ApiError } from './client';
import type {
  ConfirmResult,
  Decision,
  DerivedRecord,
  DocStatus,
  DocType,
  Document,
  DocumentAsset,
  ExtractionJob,
  Page,
  TimePrecision,
} from '../types/api';

export interface ListDocumentsParams {
  subjectId: string;
  status?: DocStatus;
  docType?: DocType;
  page?: number;
  pageSize?: number;
}

export interface UploadParams {
  subjectId: string;
  file: File;
  force?: boolean;
  /** R1 T101：用户填写的来源元数据（优先于 AI 推断） */
  occurredAt?: string;
  timePrecision?: TimePrecision;
  recordType?: string;
  facility?: string;
  department?: string;
  sourceNote?: string;
}

export const documentsApi = {
  async upload({ subjectId, file, force = false, ...meta }: UploadParams): Promise<Document> {
    const form = new FormData();
    form.append('subjectId', subjectId);
    form.append('file', file);
    if (force) form.append('force', 'true');
    // R1 T101：来源元数据（用户填写优先，重抽不覆盖）
    if (meta.occurredAt) form.append('occurredAt', meta.occurredAt);
    if (meta.timePrecision) form.append('timePrecision', meta.timePrecision);
    if (meta.recordType) form.append('recordType', meta.recordType);
    if (meta.facility) form.append('facility', meta.facility);
    if (meta.department) form.append('department', meta.department);
    if (meta.sourceNote) form.append('sourceNote', meta.sourceNote);
    // 注意：不要手动设置 Content-Type（浏览器自动带 boundary）
    const { data } = await client.post<{ document: Document; duplicate: boolean | null }>(
      'documents',
      form,
    );
    return data.document;
  },

  /** 批量上传（R1 T105）：逐项结果，单项失败不影响其他。 */
  async uploadBatch(params: {
    subjectId: string;
    files: File[];
  }): Promise<{
    items: Array<
      | { ok: true; fileName: string; document: Document }
      | { ok: false; fileName: string; code: string; message: string }
    >;
    total: number;
    succeeded: number;
    failed: number;
  }> {
    const form = new FormData();
    form.append('subjectId', params.subjectId);
    for (const f of params.files) form.append('files', f);
    const { data } = await client.post<{
      items: Array<
        | { ok: true; fileName: string; document: Document }
        | { ok: false; fileName: string; code: string; message: string }
      >;
      total: number;
      succeeded: number;
      failed: number;
    }>('documents/batch', form);
    return data;
  },

  async list(params: ListDocumentsParams): Promise<Page<Document>> {
    const { data } = await client.get<Page<Document>>('documents', { params });
    return data;
  },

  /**
   * 详情。必须带 subjectId：服务端按当前档案做归属校验（K4 零串档），
   * 漏传会回退到账号默认档案，导致跨档案读取或误 404。
   */
  async detail(
    docId: string,
    subjectId?: string,
  ): Promise<{
    document: Document;
    derivedRecord: DerivedRecord | null;
    job: ExtractionJob | null;
  }> {
    const { data } = await client.get('documents/' + docId, {
      params: subjectId ? { subjectId } : {},
    });
    return data;
  },

  async retry(docId: string, subjectId?: string): Promise<{ job: ExtractionJob }> {
    const { data } = await client.post<{ job: ExtractionJob }>(
      `documents/${docId}/retry`,
      undefined,
      { params: subjectId ? { subjectId } : {} },
    );
    return data;
  },

  async archive(docId: string, subjectId?: string): Promise<Document> {
    const { data } = await client.post<Document>(
      `documents/${docId}/archive`,
      undefined,
      { params: subjectId ? { subjectId } : {} },
    );
    return data;
  },

  /** 取消归档：恢复为 active，处理状态与已确认事实保持不变（R0 T004）。 */
  async restore(docId: string, subjectId?: string): Promise<Document> {
    const { data } = await client.post<Document>(
      `documents/${docId}/restore`,
      undefined,
      { params: subjectId ? { subjectId } : {} },
    );
    return data;
  },

  /** 列出派生资产（R1 T102）。 */
  async listAssets(
    docId: string,
    subjectId?: string,
    role?: string,
  ): Promise<DocumentAsset[]> {
    const { data } = await client.get<{ items: DocumentAsset[] }>(
      `documents/${docId}/assets`,
      { params: { ...(subjectId ? { subjectId } : {}), ...(role ? { role } : {}) } },
    );
    return data.items;
  },

  /** 取资产内容（含签名 URL）。 */
  async getAsset(
    docId: string,
    role: string,
    subjectId?: string,
    version?: number,
  ): Promise<DocumentAsset> {
    const { data } = await client.get<DocumentAsset>(
      `documents/${docId}/assets/${role}`,
      {
        params: {
          ...(subjectId ? { subjectId } : {}),
          ...(version ? { version } : {}),
        },
      },
    );
    return data;
  },

  async contentUrl(
    docId: string,
    subjectId?: string,
    page?: number,
  ): Promise<{ url: string; expiresAt: string }> {
    const { data } = await client.get<{ url: string; expiresAt: string }>(
      `documents/${docId}/content`,
      { params: { ...(page ? { page } : {}), ...(subjectId ? { subjectId } : {}) } },
    );
    return data;
  },

  async pageUrls(
    docId: string,
    subjectId?: string,
  ): Promise<Array<{ pageNo: number; url: string; expiresAt: string }>> {
    const { data } = await client.get(`documents/${docId}/pages`, {
      params: subjectId ? { subjectId } : {},
    });
    return data;
  },

  async derivedRecord(docId: string, subjectId?: string): Promise<DerivedRecord> {
    const { data } = await client.get<DerivedRecord>(`documents/${docId}/derived-record`, {
      params: subjectId ? { subjectId } : {},
    });
    return data;
  },

  async confirm(
    docId: string,
    version: number,
    decisions: Decision[],
    subjectId?: string,
  ): Promise<ConfirmResult> {
    const { data } = await client.post<ConfirmResult>(
      `documents/${docId}/derived-record/confirm`,
      { version, decisions },
      { params: subjectId ? { subjectId } : {} },
    );
    return data;
  },

  /** 重新抽取：异步入队（服务端 worker 处理），返回任务用于展示进度。 */
  async reextract(
    docId: string,
    subjectId?: string,
  ): Promise<{ queued: boolean; job: ExtractionJob }> {
    const { data } = await client.post<{ queued: boolean; job: ExtractionJob }>(
      `documents/${docId}/derived-record/reextract`,
      undefined,
      { params: subjectId ? { subjectId } : {} },
    );
    return data;
  },

  /** 409 DUP_FILE 判定（AC-4.3） */
  isDuplicateError(err: unknown): boolean {
    return err instanceof ApiError && err.code === 'DUP_FILE';
  },
};
