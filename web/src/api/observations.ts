import client from './client';
import type { DictionaryMiss, Page, TrendOption, TrendResponse } from '../types/api';

export const observationsApi = {
  async trend(params: {
    subjectId: string;
    code: string;
    dateFrom?: string;
    dateTo?: string;
    limit?: number;
  }): Promise<TrendResponse> {
    const { data } = await client.get<TrendResponse>('observations/trend', { params });
    return data;
  },

  async list(params: { subjectId: string; code?: string; page?: number; pageSize?: number }): Promise<
    Page<{
      id: string;
      code: string | null;
      displayName: string;
      valueNum: number | null;
      valueText: string | null;
      unit: string | null;
      refLow: number | null;
      refHigh: number | null;
      refText: string | null;
      abnormalFlag: string | null;
      occurredAt: string;
      confidence: number | null;
      isConfirmed: boolean;
      unmapped: boolean;
    }>
  > {
    const { data } = await client.get('observations', { params });
    return data;
  },

  async trendOptions(): Promise<TrendOption[]> {
    const { data } = await client.get<TrendOption[]>('dictionary/trend-options');
    return data;
  },

  async listMisses(params: { resolved?: boolean; page?: number; pageSize?: number }): Promise<
    Page<DictionaryMiss>
  > {
    const { data } = await client.get<Page<DictionaryMiss>>('dictionary/misses', { params });
    return data;
  },

  async resolveMiss(
    missId: string,
    payload: { code?: string; newCode?: string },
  ): Promise<{ id: string; resolved: boolean; resolvedToCode: string | null }> {
    const { data } = await client.post(`dictionary/misses/${missId}/resolve`, payload);
    return data;
  },
};
