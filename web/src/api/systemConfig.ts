import client from './client';

/** 单个系统配置项（与后端 app/config/router.py 契约一致）。 */
export interface SystemSettingItem {
  key: string;
  label: string;
  description: string;
  group: string;
  kind: 'bool' | 'int' | 'float' | 'str' | 'enum';
  isSecret: boolean;
  secretConfigured: boolean;
  value: string | number | boolean;
  default: string | number | boolean;
  source: 'db' | 'env' | 'default';
  readOnly: boolean;
  requiresRestart: boolean;
  min: number | null;
  max: number | null;
  choices: string[];
  envName: string;
}

export interface SystemSettingsResponse {
  items: SystemSettingItem[];
  groups: string[];
  editable: boolean;
  note: string;
}

export interface TestResult {
  supported: boolean;
  ok: boolean | null;
  detail: string;
}

export const systemConfigApi = {
  async get(): Promise<SystemSettingsResponse> {
    const { data } = await client.get<SystemSettingsResponse>('settings/system');
    return data;
  },

  async patch(
    items: Array<{ key: string; value: unknown }>,
  ): Promise<{ updated: string[]; items: SystemSettingItem[] }> {
    const { data } = await client.patch<{ updated: string[]; items: SystemSettingItem[] }>(
      'settings/system',
      { items },
    );
    return data;
  },

  async reset(keys: string[]): Promise<{ reset: string[] }> {
    const { data } = await client.post<{ reset: string[] }>('settings/system/reset', {
      keys,
    });
    return data;
  },

  async test(key: string): Promise<TestResult> {
    const { data } = await client.post<TestResult>('settings/system/test', { key });
    return data;
  },
};
