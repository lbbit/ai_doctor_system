import client from './client';
import type { AuditLogItem, AppSettings } from '../types/api';

export interface RedFlagRule {
  id: string;
  code: string;
  name: string;
  category: string;
  keywords: string[];
  keywordLogic: 'any' | 'all';
  contextLogic: Record<string, unknown> | null;
  severity: 'high' | 'medium';
  message: string;
  action: string;
  enabled: boolean;
  version: number;
}

export const redflagApi = {
  async list(enabled?: boolean): Promise<RedFlagRule[]> {
    const { data } = await client.get<RedFlagRule[]>('redflag/rules', {
      params: enabled === undefined ? {} : { enabled },
    });
    return data;
  },

  async create(payload: Omit<RedFlagRule, 'id' | 'version'>): Promise<RedFlagRule> {
    const { data } = await client.post<RedFlagRule>('redflag/rules', payload);
    return data;
  },

  async update(id: string, payload: Partial<RedFlagRule>): Promise<RedFlagRule> {
    const { data } = await client.patch<RedFlagRule>(`redflag/rules/${id}`, payload);
    return data;
  },

  async test(text: string): Promise<{ hits: Array<{ code: string; message: string; severity: string }> }> {
    const { data } = await client.post('redflag/rules/test', { text });
    return data;
  },
};

export const settingsApi = {
  async get(): Promise<AppSettings> {
    const { data } = await client.get<AppSettings>('settings');
    return data;
  },

  async patch(payload: { piiMaskingEnabled?: boolean }): Promise<AppSettings> {
    const { data } = await client.patch<AppSettings>('settings', payload);
    return data;
  },

  async auditLogs(params: {
    subjectId?: string;
    action?: string;
    dateFrom?: string;
    dateTo?: string;
    page?: number;
    pageSize?: number;
  }): Promise<{
    items: AuditLogItem[];
    page: number;
    pageSize: number;
    total: number;
    totalPages?: number;
  }> {
    const { data } = await client.get('audit/logs', { params });
    return data;
  },
};
