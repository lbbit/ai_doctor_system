import client from './client';
import type { Subject } from '../types/api';

export interface SubjectCreatePayload {
  name: string;
  relation: string;
  birthday?: string;
  gender?: string;
  color?: string;
  avatar?: string;
}

export const subjectsApi = {
  async list(): Promise<Subject[]> {
    const { data } = await client.get<Subject[]>('subjects');
    return data;
  },

  async create(payload: SubjectCreatePayload): Promise<Subject> {
    const { data } = await client.post<Subject>('subjects', payload);
    return data;
  },

  async update(id: string, payload: Partial<SubjectCreatePayload>): Promise<Subject> {
    const { data } = await client.patch<Subject>(`subjects/${id}`, payload);
    return data;
  },

  async remove(id: string): Promise<{ archivedConversations: number; softDeletedFiles: number }> {
    const { data } = await client.delete<{ archivedConversations: number; softDeletedFiles: number }>(
      `subjects/${id}`,
    );
    return data;
  },

  async setLastSelected(subjectId: string): Promise<{ subjectId: string }> {
    const { data } = await client.put<{ subjectId: string }>('subjects/last-selected', {
      subjectId,
    });
    return data;
  },

  async stats(id: string): Promise<{
    eventCount: number;
    observationCount: number;
    documentCount: number;
    lastRecordedAt: string | null;
  }> {
    const { data } = await client.get(`subjects/${id}/stats`);
    return data;
  },
};
