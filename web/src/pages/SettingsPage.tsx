import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { settingsApi } from '../api/settings';
import { observationsApi } from '../api/observations';
import SystemConfigPanel from '../components/SystemConfigPanel';
import AccountAdminPanel from '../components/AccountAdminPanel';
import { useAuthStore } from '../stores/authStore';

/** F-14 设置页：账户安全、脱敏开关、审计日志、免责声明。 */
export default function SettingsPage() {
  const account = useAuthStore((s) => s.account);
  const logout = useAuthStore((s) => s.logout);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState('');
  const [saved, setSaved] = useState(false);
  const [maskErr, setMaskErr] = useState<string | null>(null);

  const settingsQ = useQuery({ queryKey: ['settings'], queryFn: () => settingsApi.get() });
  const auditQ = useQuery({
    queryKey: ['audit', page, action],
    queryFn: () => settingsApi.auditLogs({ page, pageSize: 20, action: action || undefined }),
  });
  const missesQ = useQuery({
    queryKey: ['dict-misses'],
    queryFn: () => observationsApi.listMisses({ resolved: false, pageSize: 20 }),
  });

  const toggleMasking = async (next: boolean) => {
    setMaskErr(null);
    try {
      await settingsApi.patch({ piiMaskingEnabled: next });
      await settingsQ.refetch();
      setSaved(true);
    } catch (err) {
      setMaskErr((err as Error).message);
    }
  };

  const settings = settingsQ.data;

  return (
    <div className="settings-page">
      <section className="panel">
        <h2>账户</h2>
        <dl className="kv">
          <dt>邮箱</dt>
          <dd>{account?.email}</dd>
          <dt>昵称</dt>
          <dd>{account?.displayName || '（未设置）'}</dd>
        </dl>
        <button type="button" className="btn btn--danger" onClick={logout}>
          退出登录
        </button>
      </section>

      <section className="panel">
        <h2>隐私与 AI</h2>
        {settings ? (
          <dl className="kv">
            <dt>出域脱敏</dt>
            <dd>
              {settings.piiMaskingEnabled ? '已开启' : '已关闭'}
              {!settings.localModelMode && settings.piiMaskingEnabled && (
                <span className="muted small">（云端模式强制开启，不可关闭）</span>
              )}
              {settings.piiMaskingEnabled && settings.localModelMode && (
                <button type="button" className="btn btn--sm btn--ghost" onClick={() => void toggleMasking(false)}>
                  关闭
                </button>
              )}
              {!settings.piiMaskingEnabled && (
                <button type="button" className="btn btn--sm btn--primary" onClick={() => void toggleMasking(true)}>
                  开启
                </button>
              )}
            </dd>
            <dt>本地模型模式</dt>
            <dd>{settings.localModelMode ? '开启（不出域）' : '关闭（云端网关）'}</dd>
            <dt>确认阈值</dt>
            <dd>{settings.confidenceThreshold}（P0 全部强制人工确认）</dd>
            <dt>自动入库</dt>
            <dd>{settings.autoCommitEnabled ? '开启' : '关闭（P1 功能）'}</dd>
            <dt>上传上限</dt>
            <dd>{settings.maxUploadMb} MB</dd>
            <dt>免责声明版本</dt>
            <dd>{settings.disclaimerVersion}</dd>
          </dl>
        ) : (
          <p className="muted">加载中…</p>
        )}
        {maskErr && <p className="form-error">{maskErr}</p>}
        {saved && !maskErr && <p className="notice notice--success">已保存</p>}
      </section>

      <section className="panel">
        <h2>操作审计</h2>
        <div className="filters">
          <input
            placeholder="action 过滤（如 record.confirm）"
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setPage(1);
            }}
          />
        </div>
        {auditQ.data && auditQ.data.total === 0 && <p className="muted">暂无记录</p>}
        {auditQ.data && auditQ.data.total > 0 && (
          <>
            <table className="audit-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>动作</th>
                  <th>资源</th>
                  <th>requestId</th>
                </tr>
              </thead>
              <tbody>
                {auditQ.data.items.map((item) => (
                  <tr key={item.id}>
                    <td>{item.createdAt?.replace('T', ' ').slice(0, 19) ?? ''}</td>
                    <td>{item.action}</td>
                    <td>
                      {item.resourceType}
                      {item.resourceId ? `:${item.resourceId.slice(0, 8)}…` : ''}
                    </td>
                    <td className="mono small">{item.requestId?.slice(0, 10) ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pager">
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                上一页
              </button>
              <span className="muted small">
                {auditQ.data.page} / {auditQ.data.totalPages ?? 1}（共 {auditQ.data.total}）
              </span>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={page >= (auditQ.data.totalPages ?? 1)}
                onClick={() => setPage((p) => p + 1)}
              >
                下一页
              </button>
            </div>
          </>
        )}
      </section>

      <SystemConfigPanel />

      <AccountAdminPanel />

      <section className="panel">
        <h2>字典未命中队列（E-3）</h2>
        {missesQ.data && missesQ.data.total === 0 && <p className="muted">暂无未命中指标</p>}
        <ul className="miss-list">
          {(missesQ.data?.items ?? []).map((m) => (
            <li key={m.id}>
              <strong>{m.rawName}</strong>
              {m.rawValue ? ` = ${m.rawValue}` : ''} {m.rawUnit ?? ''}
              <span className="muted small"> · {m.createdAt?.slice(0, 10) ?? ''}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h2>免责声明</h2>
        <p className="disclaimer">
          本系统为家庭健康管理工具，提供的所有内容（含 AI 生成）仅供健康参考与就医建议，
          不构成医疗诊断、处方或治疗方案。任何用药与诊疗决定请遵医嘱。紧急情况请立即拨打 120。
        </p>
      </section>
    </div>
  );
}
