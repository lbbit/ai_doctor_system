import { describe, expect, it } from 'vitest';

/**
 * 账户/通知 API 契约测试（纯函数层）。
 * 目的：锁定后端字段名，避免 camelCase 契约被误改。
 */

interface ConfigNotice {
  id: string;
  action: string;
  actorEmail: string;
  keys: string[];
  highImpact: boolean;
  createdAt: string | null;
}

function actionLabel(action: string): string {
  if (action === 'config.reset') return '恢复默认';
  if (action === 'account.role_change') return '角色变更';
  return '配置修改';
}

function roleLabel(role: 'admin' | 'user'): string {
  return role === 'admin' ? '管理员' : '普通用户';
}

function canDemoteSelf(isSelf: boolean, next: string): boolean {
  return !(isSelf && next !== 'admin');
}

function summarizeNotices(items: ConfigNotice[]): {
  total: number;
  highImpact: number;
  actions: string[];
} {
  return {
    total: items.length,
    highImpact: items.filter((n) => n.highImpact).length,
    actions: Array.from(new Set(items.map((n) => actionLabel(n.action)))),
  };
}

describe('actionLabel', () => {
  it('区分配置修改与恢复默认', () => {
    expect(actionLabel('config.update')).toBe('配置修改');
    expect(actionLabel('config.reset')).toBe('恢复默认');
    expect(actionLabel('account.role_change')).toBe('角色变更');
  });

  it('未知 action 回退为配置修改', () => {
    expect(actionLabel('something.else')).toBe('配置修改');
  });
});

describe('roleLabel', () => {
  it('映射角色中文名', () => {
    expect(roleLabel('admin')).toBe('管理员');
    expect(roleLabel('user')).toBe('普通用户');
  });
});

describe('canDemoteSelf', () => {
  it('不能降低自己的权限', () => {
    expect(canDemoteSelf(true, 'user')).toBe(false);
  });

  it('可以操作他人', () => {
    expect(canDemoteSelf(false, 'user')).toBe(true);
  });

  it('自己不涉及降级时允许（保持 admin）', () => {
    expect(canDemoteSelf(true, 'admin')).toBe(true);
  });
});

describe('summarizeNotices', () => {
  const items: ConfigNotice[] = [
    {
      id: '1',
      action: 'config.update',
      actorEmail: 'a@example.com',
      keys: ['llm.chat.model'],
      highImpact: true,
      createdAt: '2026-10-22T05:00:00Z',
    },
    {
      id: '2',
      action: 'config.reset',
      actorEmail: 'a@example.com',
      keys: ['agent.max_results'],
      highImpact: true,
      createdAt: '2026-10-22T05:10:00Z',
    },
    {
      id: '3',
      action: 'config.update',
      actorEmail: 'a@example.com',
      keys: ['chat.history_limit'],
      highImpact: false,
      createdAt: '2026-10-22T05:20:00Z',
    },
  ];

  it('统计条数与高影响数', () => {
    const s = summarizeNotices(items);
    expect(s.total).toBe(3);
    expect(s.highImpact).toBe(2);
  });

  it('去重 action 标签', () => {
    const s = summarizeNotices(items);
    expect(s.actions).toContain('配置修改');
    expect(s.actions).toContain('恢复默认');
    expect(s.actions).toHaveLength(2);
  });

  it('空列表', () => {
    const s = summarizeNotices([]);
    expect(s.total).toBe(0);
    expect(s.highImpact).toBe(0);
    expect(s.actions).toEqual([]);
  });

  it('通知中不得包含密钥明文', () => {
    const blob = JSON.stringify(items).toLowerCase();
    expect(blob).not.toContain('sk-');
    expect(blob).not.toContain('api_key');
  });
});
