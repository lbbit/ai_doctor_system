/**
 * 账户与权限面板（PLAN §17）。
 *
 * 职责：
 * 1. 管理员：展示账户列表、提升/降级角色（二次确认）。
 * 2. 所有人：展示最近配置变更通知（管理员看全部，普通用户只看自己的）。
 *
 * 安全约束由后端保证（403 / 422）；前端仅做展示与确认，
 * 不复制后端保护逻辑（避免两处规则不一致）。
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { accountsApi, type AccountRole, type AccountRow } from '../api/accounts';
import ConfirmDialog from './ConfirmDialog';
import { useAuthStore } from '../stores/authStore';

function fmtTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '';
}

function actionLabel(action: string): string {
  if (action === 'config.reset') return '恢复默认';
  if (action === 'account.role_change') return '角色变更';
  return '配置修改';
}

export default function AccountAdminPanel() {
  const me = useAuthStore((s) => s.account);
  const [pending, setPending] = useState<{ row: AccountRow; next: AccountRole } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const accountsQ = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
    retry: false,
  });
  const noticesQ = useQuery({
    queryKey: ['config-notices'],
    queryFn: () => accountsApi.configNotices({ sinceHours: 72, limit: 10 }),
    retry: false,
  });

  const isAdmin = accountsQ.data?.canManage ?? noticesQ.data?.isAdmin ?? false;

  const applyRole = async () => {
    if (!pending) return;
    setError(null);
    setNotice(null);
    try {
      const res = await accountsApi.changeRole(pending.row.id, pending.next);
      setNotice(
        res.changed
          ? `${pending.row.email} 已设为${pending.next === 'admin' ? '管理员' : '普通用户'}`
          : '角色未变化',
      );
      await accountsQ.refetch();
      await noticesQ.refetch();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="panel">
      <h2>账户与权限</h2>

      {notice && <p className="form-success">{notice}</p>}
      {error && <p className="form-error">{error}</p>}

      {isAdmin ? (
        <>
          <p className="muted small">
            管理员可修改全站系统配置。系统至少保留一名管理员，且不能降低自己的权限。
          </p>
          {accountsQ.isLoading && <p className="muted">加载中…</p>}
          {accountsQ.data && (
            <div className="md-table-wrap">
              <table className="md-table">
                <thead>
                  <tr>
                    <th>邮箱</th>
                    <th>昵称</th>
                    <th>角色</th>
                    <th>注册时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {accountsQ.data.items.map((row) => {
                    const isSelf = row.id === me?.id;
                    const next: AccountRole = row.role === 'admin' ? 'user' : 'admin';
                    return (
                      <tr key={row.id}>
                        <td>
                          {row.email}
                          {isSelf && <span className="muted small">（我）</span>}
                        </td>
                        <td>{row.displayName || '—'}</td>
                        <td>
                          <span className={`role-badge role-badge--${row.role}`}>
                            {row.role === 'admin' ? '管理员' : '普通用户'}
                          </span>
                        </td>
                        <td className="mono small">{fmtTime(row.createdAt)}</td>
                        <td>
                          <button
                            type="button"
                            className="btn btn--sm btn--ghost"
                            disabled={isSelf && next !== 'admin'}
                            title={isSelf ? '不能降低自己的权限' : ''}
                            onClick={() => setPending({ row, next })}
                          >
                            {next === 'admin' ? '设为管理员' : '设为普通用户'}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <p className="muted small">
          当前为普通用户，无法管理账户与全站配置。如需权限请联系管理员。
        </p>
      )}

      <h3 className="config-group__title" style={{ marginTop: 20 }}>
        最近配置变更（72 小时）
      </h3>
      {noticesQ.isLoading && <p className="muted">加载中…</p>}
      {noticesQ.data && noticesQ.data.items.length === 0 && (
        <p className="muted">暂无变更记录</p>
      )}
      {noticesQ.data && noticesQ.data.items.length > 0 && (
        <ul className="notice-list">
          {noticesQ.data.items.map((n) => (
            <li key={n.id} className={n.highImpact ? 'notice-list__item--high' : undefined}>
              <strong>{actionLabel(n.action)}</strong>
              {n.highImpact && <span className="config-badge config-badge--db">高影响</span>}
              <span className="muted small"> · {fmtTime(n.createdAt)}</span>
              {n.actorEmail && <span className="muted small"> · {n.actorEmail}</span>}
              <div className="mono small notice-list__keys">{n.keys.join('、')}</div>
            </li>
          ))}
        </ul>
      )}

      {pending && (
        <ConfirmDialog
          open
          title={pending.next === 'admin' ? '确认提升为管理员' : '确认降级为普通用户'}
          confirmText="确认"
          danger={pending.next !== 'admin'}
          onCancel={() => setPending(null)}
          onConfirm={() => void applyRole()}
        >
          <p>
            {pending.next === 'admin'
              ? `将 ${pending.row.email} 设为管理员后，该账户可以修改全站 AI 模型、检索源与限额配置。`
              : `将 ${pending.row.email} 降级为普通用户后，该账户将无法修改系统配置。`}
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}
