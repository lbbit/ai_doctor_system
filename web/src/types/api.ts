/** 与后端 API 契约对应的 TypeScript 类型（§4 / §8.3 camelCase）。 */

export interface Account {
  id: string;
  email: string;
  displayName: string | null;
  status: string;
}

export interface Subject {
  id: string;
  name: string;
  relation: string;
  color: string | null;
  avatar: string | null;
  isDefault: boolean;
  birthday: string | null;
  gender: string | null;
}

export type DocStatus =
  | 'pending'
  | 'extracting'
  | 'awaiting_review'
  | 'confirmed'
  | 'parsed_no_data'
  | 'failed'
  | 'archived';

export type DocType = 'lab_report' | 'imaging_report' | 'visit_record' | 'other';

export interface Document {
  id: string;
  subjectId: string;
  kind: 'PDF' | 'IMAGE' | 'DICOM' | 'AUDIO' | 'TEXT';
  docType: DocType | null;
  status: DocStatus;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  pageCount: number | null;
  failureReason: string | null;
  reviewVersion: number;
  contentHash: string | null;
  archivedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface Page<T> {
  items: T[];
  page: number;
  pageSize: number;
  total: number;
  totalPages?: number;
}

export interface ExtractionJob {
  id: string;
  documentId: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  attempt: number;
  parser: string | null;
  error: string | null;
  /** loading / rendering / extracting / mapping / done / failed */
  stage: string | null;
  progress: { done: number; total: number };
  elapsedSeconds: number;
  /** 租约已过期（worker 可能已崩溃，服务端会自动回收重试） */
  stale: boolean;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface ObsDraft {
  raw_name: string;
  value: number | string | null;
  unit: string | null;
  ref_low: number | null;
  ref_high: number | null;
  ref_text: string | null;
  abnormal_flag: 'N' | 'L' | 'H' | 'A' | null;
  occurred_at: string | null;
  confidence: number | null;
  bbox: { page: number; x: number; y: number; w: number; h: number } | null;
  status: 'proposed' | 'confirmed' | 'corrected' | 'rejected';
  code: string | null;
  dictionary_id: string | null;
  display_name: string;
  unmapped?: boolean;
}

export interface EventDraft {
  type: string;
  title: string | null;
  narrative: string | null;
  diagnosis: string | null;
  department: string | null;
  occurred_at: string | null;
  confidence: number | null;
}

export interface DerivedRecordPayload {
  report_date: string | null;
  doc_type_suggest: string | null;
  events: EventDraft[];
  observations: ObsDraft[];
  mock?: boolean;
  /** 逐页识别中失败的页（部分成功仍可用） */
  page_errors?: string[];
  page_count?: number;
  _decisions?: Record<string, { action: string }>;
}

export interface DerivedRecord {
  id: string;
  version: number;
  payload: DerivedRecordPayload;
  parser: string;
  confidenceSummary: {
    observationCount: number;
    eventCount: number;
    min: number | null;
    max: number | null;
    belowThreshold: (string | null)[];
    mock: boolean;
  } | null;
  /** 重新抽取后与上一版草稿的差异（无上一版时为 null） */
  comparison: RecordComparison | null;
}

export interface RecordComparison {
  previousVersion: number;
  previousObservationCount: number;
  currentObservationCount: number;
  added: number;
  removed: number;
  changed: number;
  addedNames: string[];
  removedNames: string[];
}

export interface Decision {
  fieldRef: string; // event:{i} | obs:{i}
  action: 'confirm' | 'correct' | 'reject';
  value?: Record<string, unknown>;
  note?: string;
}

export interface ConfirmResult {
  documentStatus: DocStatus;
  events: Array<{
    id: string;
    eventType: string;
    title: string | null;
    occurredAt: string;
    recordedAt: string | null;
    documentId: string | null;
  }>;
  observations: Array<{
    id: string;
    code: string | null;
    displayName: string;
    valueNum: number | null;
    valueText: string | null;
    unit: string | null;
    abnormalFlag: string | null;
    occurredAt: string;
    confidence: number | null;
  }>;
  auditId: string | null;
}

export interface TimelineEvent {
  id: string;
  eventType: string;
  occurredAt: string;
  occurredAtPrecision: string;
  recordedAt: string | null;
  title: string | null;
  narrative: string | null;
  diagnosis: string | null;
  department: string | null;
  eventSource: string;
  documentId: string | null;
  observationCount: number;
}

export interface TimelineGroup {
  year: number;
  month: number | null;
  items: TimelineEvent[];
}

export interface TimelineSummary {
  typeCounts: Record<string, number>;
  dateRange: { from: string | null; to: string | null };
  documentCount: number;
  eventCount: number;
  isEmpty: boolean;
}

export interface TrendPoint {
  occurredAt: string;
  valueNum: number | null;
  valueText: string | null;
  abnormalFlag: string | null;
  refLow: number | null;
  refHigh: number | null;
  documentId: string | null;
  observationId: string | null;
}

export interface TrendResponse {
  code: string | null;
  name: string;
  unit: string | null;
  points: TrendPoint[];
  refBand: { low: number | null; high: number | null; text: string | null } | null;
  insufficient: boolean;
}

export interface TrendOption {
  code: string;
  name: string;
  unit: string | null;
  category: string | null;
}

export interface Conversation {
  id: string;
  subjectId: string;
  title: string | null;
  status: 'active' | 'archived';
  lastMessageAt: string | null;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  createdAt: string | null;
  redFlagHitId: string | null;
  errorState: string | null;
  llmModel?: string | null;
}

export interface Draft {
  id: string;
  messageId: string;
  subjectId: string;
  draftType: 'event' | 'observation' | 'both';
  payload: {
    event: {
      type: string;
      title: string;
      narrative: string | null;
      diagnosis: string | null;
      department: string | null;
      occurredAt: string;
      precision: string;
      expression: string | null;
      confidence: number | null;
    } | null;
    observations: Array<{
      displayName: string;
      value: number | string | null;
      unit: string | null;
      refLow: number | null;
      refHigh: number | null;
      refText: string | null;
      abnormalFlag: string | null;
      occurredAt: string;
      precision: string;
      expression: string | null;
      confidence: number | null;
    }>;
  };
  status: 'pending' | 'confirmed' | 'discarded';
  confirmedAt: string | null;
  discardedAt: string | null;
  resultEventId: string | null;
  resultObservationIds: string[];
}

export interface CitationRef {
  refIndex: number;
  refType: 'observation' | 'event' | 'document';
  refId: string;
  label: string;
  documentId?: string | null;
  url?: string | null;
  expiresInSeconds?: number | null;
}

export type SseEvent =
  | { event: 'meta'; data: { conversationId: string; userMessageId: string; assistantMessageId: string; subjectId: string; subjectName: string; subjectAvatar: string | null } }
  | { event: 'delta'; data: { content: string } }
  | { event: 'citations'; data: { traceId: string; refs: CitationRef[]; sanitizer: Array<{ type: string; repl: string }> } }
  | { event: 'redflag'; data: { code: string; severity: string; message: string; hitId?: string } }
  | { event: 'draft'; data: Draft & { subjectName: string; subjectAvatar: string | null } }
  | { event: 'status'; data: { stage: string; detail: string; tool?: string } }
  | {
      event: 'done';
      data: {
        messageId: string;
        traceId: string;
        latencyMs: number;
        agent?: { usedTools: string[]; toolErrors: string[] };
        /** 是否走了真流式（false 表示降级为整段输出） */
        streamed?: boolean;
        /** 首个 token 延迟（毫秒），用于体验度量 */
        firstTokenMs?: number | null;
      };
    }
  | { event: 'error'; data: { code: string; message: string } };

export interface AuditLogItem {
  id: string;
  subjectId: string | null;
  actorType: string;
  action: string;
  resourceType: string;
  resourceId: string | null;
  before: unknown;
  after: unknown;
  requestId: string | null;
  ip: string | null;
  createdAt: string | null;
}

export interface AppSettings {
  piiMaskingEnabled: boolean;
  localModelMode: boolean;
  disclaimerVersion: string;
  confidenceThreshold: number;
  autoCommitEnabled: boolean;
  maxUploadMb: number;
}

export interface DictionaryMiss {
  id: string;
  subjectId: string;
  documentId: string | null;
  rawName: string;
  rawValue: string | null;
  rawUnit: string | null;
  resolved: boolean;
  resolvedToCode: string | null;
  createdAt: string | null;
}

export const DOC_STATUS_LABELS: Record<DocStatus, string> = {
  pending: '待处理',
  extracting: '抽取中',
  awaiting_review: '待确认',
  confirmed: '已确认',
  parsed_no_data: '已解析无事件',
  failed: '解析失败',
  archived: '已归档',
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
  visit: '就诊',
  hospitalization: '住院',
  surgery: '手术',
  checkup: '体检',
  medication: '用药',
  vaccine: '疫苗',
  self_reported: '自述',
  other: '其他',
};

export const ABNORMAL_LABELS: Record<string, string> = {
  N: '正常',
  L: '偏低',
  H: '偏高',
  A: '异常',
};
