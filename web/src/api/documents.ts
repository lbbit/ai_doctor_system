import client, { ApiError } from './client';
import type {
  ConfirmResult,
  Decision,
  DerivedRecord,
  DocStatus,
  DocType,
  Document,
  ExtractionJob,
  Page,
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
}

export const documentsApi = {
  async upload({ subjectId, file, force = false }: UploadParams): Promise<Document> {
    const form = new FormData();
    form.append('subjectId', subjectId);
    form.append('file', file);
    if (force) form.append('force', 'true');
    // 注意：不要手动设置 Content-Type（浏览器自动带 boundary）
    const { data } = await client.post<{ document: Document; duplicate: boolean | null }>(
      'documents',
      form,
    );
    return data.document;
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
