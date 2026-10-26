import client from './client';

export type AccountRole = 'admin' | 'user';

export interface AccountRow {
  id: string;
  email: string;
  displayName: string | null;
  role: AccountRole;
  status: string;
  createdAt: string | null;
}

export interface AccountsResponse {
  items: AccountRow[];
  adminCount: number;
  canManage: boolean;
}

export interface ConfigNotice {
  id: string;
  action: string;
  actorEmail: string;
  keys: string[];
  highImpact: boolean;
  createdAt: string | null;
}

export interface NoticesResponse {
  items: ConfigNotice[];
  scope: 'all' | 'mine';
  isAdmin: boolean;
}

export const accountsApi = {
  async list(): Promise<AccountsResponse> {
    const { data } = await client.get<AccountsResponse>('accounts');
    return data;
  },

  async changeRole(
    accountId: string,
    role: AccountRole,
  ): Promise<{ account: AccountRow; changed: boolean }> {
    const { data } = await client.patch<{ account: AccountRow; changed: boolean }>(
      `accounts/${accountId}/role`,
      { role },
    );
    return data;
  },

  async configNotices(params?: {
    sinceHours?: number;
    limit?: number;
  }): Promise<NoticesResponse> {
    const { data } = await client.get<NoticesResponse>('accounts/config-notices', {
      params,
    });
    return data;
  },
};
