import client from './client';
import type { Page, TimelineGroup, TimelineSummary } from '../types/api';

export const timelineApi = {
  async events(params: {
    subjectId: string;
    types?: string;
    dateFrom?: string;
    dateTo?: string;
    page?: number;
    pageSize?: number;
  }): Promise<Page<TimelineGroup> & { groups: TimelineGroup[] }> {
    const { data } = await client.get('timeline/events', { params });
    return data;
  },

  async summary(subjectId: string): Promise<TimelineSummary> {
    const { data } = await client.get<TimelineSummary>('timeline/summary', {
      params: { subjectId },
    });
    return data;
  },
};
