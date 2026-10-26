/**
 * 系统配置面板（§6.9）。
 *
 * 设计要点：
 * 1. 按 group 分区展示，密钥类只显示「已配置」且不可编辑（AGENTS.md 密钥约束）。
 * 2. 有改动才提交，逐项校验失败由后端统一拒绝（不做前端猜测性放行）。
 * 3. 支持「恢复默认」= 删除 DB 覆盖，回退 env。
 */

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { systemConfigApi, type SystemSettingItem } from '../api/systemConfig';

const GROUP_TITLES: Record<string, string> = {
  llm_extraction: '文档识别 LLM（量大事简，建议成本优先）',
  llm_chat: '问诊 LLM（要求推理能力，建议能力优先）',
  agent: '问诊 Agent（联网检索与临床思维）',
  chat: '对话参数',
  privacy: '隐私',
  upload: '上传',
};

function groupTitle(group: string): string {
  return GROUP_TITLES[group] ?? group;
}

function sourceBadge(source: SystemSettingItem['source']): string {
  if (source === 'db') return '已覆盖';
  if (source === 'env') return '来自环境变量';
  return '默认值';
}

export default function SystemConfigPanel() {
  const query = useQuery({
    queryKey: ['system-config'],
    queryFn: () => systemConfigApi.get(),
    staleTime: 0,
  });

  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const items = query.data?.items ?? [];
  const editable = query.data?.editable ?? false;

  // 服务端刷新后清空本地草稿（避免显示已过期的编辑内容）
  useEffect(() => {
    setDraft({});
  }, [query.data]);

  const grouped = useMemo(() => {
    const map = new Map<string, SystemSettingItem[]>();
    for (const item of items) {
      const list = map.get(item.group) ?? [];
      list.push(item);
      map.set(item.group, list);
    }
    return Array.from(map.entries());
  }, [items]);

  const dirtyKeys = Object.keys(draft);

  const valueOf = (item: SystemSettingItem): string | boolean => {
    if (item.key in draft) return draft[item.key];
    if (item.kind === 'bool') return Boolean(item.value);
    return String(item.value ?? '');
  };

  const setValue = (key: string, value: string | boolean) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setNotice(null);
    setError(null);
  };

  const save = async () => {
    if (dirtyKeys.length === 0) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const payload = dirtyKeys.map((key) => {
        const item = items.find((i) => i.key === key);
        const raw = draft[key];
        if (item?.kind === 'bool') return { key, value: Boolean(raw) };
        if (item?.kind === 'int' || item?.kind === 'float') {
          return { key, value: Number(raw) };
        }
        return { key, value: raw };
      });
      const result = await systemConfigApi.patch(payload);
      setNotice(`已保存 ${result.updated.length} 项配置，即时生效`);
      await query.refetch();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const restore = async (item: SystemSettingItem) => {
    setError(null);
    setNotice(null);
    if (!editable) {
      setError('当前账户为只读权限，无法修改配置');
      return;
    }
    try {
      await systemConfigApi.reset([item.key]);
      setNotice(`「${item.label}」已恢复为环境变量/默认值`);
      await query.refetch();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const runTest = async (item: SystemSettingItem) => {
    if (!editable) {
      setTestResult((prev) => ({ ...prev, [item.key]: '无权限（仅只读查看）' }));
      return;
    }
    setTestResult((prev) => ({ ...prev, [item.key]: '检测中…' }));
    try {
      const result = await systemConfigApi.test(item.key);
      setTestResult((prev) => ({
        ...prev,
        [item.key]: result.supported === false ? result.detail : `${result.ok ? '✔ ' : '✘ '}${result.detail}`,
      }));
    } catch (err) {
      setTestResult((prev) => ({ ...prev, [item.key]: `检测失败：${(err as Error).message}` }));
    }
  };

  if (query.isLoading) {
    return (
      <section className="panel">
        <h2>系统配置</h2>
        <p className="muted">加载中…</p>
      </section>
    );
  }

  if (query.isError) {
    return (
      <section className="panel">
        <h2>系统配置</h2>
        <p className="form-error">{(query.error as Error).message}</p>
      </section>
    );
  }


  return (
    <section className="panel">
      <div className="config-panel__head">
        <h2>系统配置</h2>
        <div className="config-panel__actions">
          {!editable && (
            <span className="config-readonly">只读（无管理权限）</span>
          )}
          {dirtyKeys.length > 0 && (
            <span className="muted small">{dirtyKeys.length} 项待保存</span>
          )}
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={saving || dirtyKeys.length === 0 || !editable}
            onClick={() => void save()}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>

      {query.data?.note && <p className="muted small">{query.data.note}</p>}
      {notice && <p className="form-success">{notice}</p>}
      {error && <p className="form-error">{error}</p>}

      {grouped.map(([group, groupItems]) => (
        <div key={group} className="config-group">
          <h3 className="config-group__title">{groupTitle(group)}</h3>
          {groupItems.map((item) => (
            <div key={item.key} className="config-row">
              <div className="config-row__label">
                <div className="config-row__name">
                  {item.label}
                  <span className={`config-badge config-badge--${item.source}`}>
                    {sourceBadge(item.source)}
                  </span>
                </div>
                {item.description && (
                  <div className="config-row__desc">{item.description}</div>
                )}
                {item.isSecret && (
                  <div className="config-row__desc">
                    {item.secretConfigured
                      ? `已通过环境变量 ${item.envName || ''} 配置，出于安全不可在此修改`
                      : `未配置（设置环境变量 ${item.envName || 'API_KEY'} 后生效）`}
                  </div>
                )}
              </div>

              <div className="config-row__control">
                {item.isSecret ? (
                  <span className="config-secret">
                    {item.secretConfigured ? '••••••••（已配置）' : '未配置'}
                  </span>
                ) : item.kind === 'bool' ? (
                  <label className="config-switch">
                    <input
                      type="checkbox"
                      checked={Boolean(valueOf(item))}
                      disabled={(item.readOnly || !editable)}
                      onChange={(e) => setValue(item.key, e.target.checked)}
                    />
                    <span>{Boolean(valueOf(item)) ? '开启' : '关闭'}</span>
                  </label>
                ) : item.kind === 'enum' ? (
                  <select
                    value={String(valueOf(item))}
                    disabled={(item.readOnly || !editable)}
                    onChange={(e) => setValue(item.key, e.target.value)}
                  >
                    {item.choices.map((choice) => (
                      <option key={choice} value={choice}>
                        {choice}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type={item.kind === 'int' || item.kind === 'float' ? 'number' : 'text'}
                    value={String(valueOf(item))}
                    disabled={(item.readOnly || !editable)}
                    min={item.min ?? undefined}
                    max={item.max ?? undefined}
                    placeholder={item.default === '' ? '（未设置）' : String(item.default)}
                    onChange={(e) => setValue(item.key, e.target.value)}
                  />
                )}

                <div className="config-row__tools">
                  {(item.key.startsWith('llm.') || item.key.startsWith('agent.')) && (
                    <button
                      type="button"
                      className="btn btn--sm btn--ghost"
                      onClick={() => void runTest(item)}
                    >
                      测试
                    </button>
                  )}
                  {item.source === 'db' && editable && (
                    <button
                      type="button"
                      className="btn btn--sm btn--ghost"
                      onClick={() => void restore(item)}
                    >
                      恢复默认
                    </button>
                  )}
                </div>
              </div>

              {testResult[item.key] && (
                <div className="config-row__test">{testResult[item.key]}</div>
              )}
            </div>
          ))}
        </div>
      ))}
    </section>
  );
}
