import client from './client';
import type { Account, Subject } from '../types/api';

export interface RegisterPayload {
  email: string;
  password: string;
  displayName?: string;
}

export interface LoginResponse {
  token: string;
  account: Account;
  lastSubjectId: string | null;
}

export interface RegisterResponse {
  token: string;
  account: Account;
  defaultSubject: Subject;
}

export interface MeResponse {
  account: Account;
  subjects: Subject[];
  lastSubjectId: string | null;
}

export const authApi = {
  async register(payload: RegisterPayload): Promise<RegisterResponse> {
    const { data } = await client.post<RegisterResponse>('auth/register', payload);
    return data;
  },

  async login(payload: { email: string; password: string }): Promise<LoginResponse> {
    const { data } = await client.post<LoginResponse>('auth/login', payload);
    return data;
  },

  async refresh(): Promise<{ token: string }> {
    const { data } = await client.post<{ token: string }>('auth/refresh');
    return data;
  },

  async me(): Promise<MeResponse> {
    const { data } = await client.get<MeResponse>('auth/me');
    return data;
  },
};
