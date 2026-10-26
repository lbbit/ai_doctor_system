import { FormEvent, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import ConfirmDialog from '../components/ConfirmDialog';
import { subjectsApi } from '../api/subjects';
import { useSubjectStore } from '../stores/subjectStore';
import type { Subject } from '../types/api';

const RELATIONS = ['本人', '父亲', '母亲', '祖父母', '外祖父母', '配偶', '儿子', '女儿', '其他'];
const COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6'];

/** F-02 成员管理：增删家庭成员、档案概览（§10 删除级联）。 */
export default function SubjectsPage() {
  const current = useSubjectStore((s) => s.current);
  const loadSubjects = useSubjectStore((s) => s.loadSubjects);
  const selectSubject = useSubjectStore((s) => s.selectSubject);
  const [name, setName] = useState('');
  const [relation, setRelation] = useState('父亲');
  const [birthday, setBirthday] = useState('');
  const [gender, setGender] = useState('unknown');
  const [color, setColor] = useState(COLORS[0]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Subject | null>(null);
  const [toDelete, setToDelete] = useState<Subject | null>(null);

  const statsQuery = useQuery({
    queryKey: ['subject-stats-all', current?.id],
    queryFn: () => subjectsApi.stats(current!.id),
    enabled: Boolean(current?.id),
  });

  const subjects = useSubjectStore((s) => s.subjects);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await subjectsApi.create({
        name,
        relation,
        birthday: birthday || undefined,
        gender,
        color,
      });
      setName('');
      await loadSubjects();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const onUpdate = async (e: FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    setError(null);
    try {
      await subjectsApi.update(editing.id, {
        name,
        relation,
        birthday: birthday || undefined,
        gender,
        color,
      });
      setEditing(null);
      await loadSubjects();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const onDelete = async () => {
    if (!toDelete) return;
    try {
      const res = await subjectsApi.remove(toDelete.id);
      setToDelete(null);
      await loadSubjects();
      setError(
        `已删除「${toDelete.name}」：归档会话 ${res.archivedConversations} 个，软删文件 ${res.softDeletedFiles} 个`,
      );
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const startEdit = (s: Subject) => {
    setEditing(s);
    setName(s.name);
    setRelation(s.relation);
    setBirthday(s.birthday ?? '');
    setGender(s.gender ?? 'unknown');
    setColor(s.color ?? COLORS[0]);
  };

  return (
    <div className="subjects-page">
      <section className="panel">
        <h2>家庭成员</h2>
        <ul className="subject-grid">
          {subjects.map((s) => (
            <li key={s.id} className={`subject-card ${current?.id === s.id ? 'subject-card--current' : ''}`}>
              <span className="subject-card__avatar" style={{ background: s.color || '#10B981' }}>
                {s.avatar || '👤'}
              </span>
              <div className="subject-card__body">
                <strong>{s.name}</strong>
                <span className="muted small">
                  {s.relation}
                  {s.birthday ? ` · ${s.birthday}` : ''}
                  {s.isDefault ? ' · 默认' : ''}
                </span>
              </div>
              <div className="subject-card__actions">
                <button type="button" className="btn btn--sm btn--ghost" onClick={() => selectSubject(s.id)}>
                  {current?.id === s.id ? '当前' : '切换'}
                </button>
                <button type="button" className="btn btn--sm btn--ghost" onClick={() => startEdit(s)}>
                  编辑
                </button>
                <button type="button" className="btn btn--sm btn--danger" onClick={() => setToDelete(s)}>
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
        {statsQuery.data && (
          <p className="muted small">
            当前档案「{current?.name}」概览：事件 {statsQuery.data.eventCount} · 指标{' '}
            {statsQuery.data.observationCount} · 文档 {statsQuery.data.documentCount}
          </p>
        )}
      </section>

      <section className="panel">
        <h2>{editing ? `编辑「${editing.name}」` : '新建成员'}</h2>
        <form className="subject-form" onSubmit={editing ? onUpdate : onCreate}>
          <label>
            姓名
            <input required maxLength={32} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            关系
            <select value={relation} onChange={(e) => setRelation(e.target.value)}>
              {RELATIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <label>
            生日（可选）
            <input type="date" value={birthday} onChange={(e) => setBirthday(e.target.value)} />
          </label>
          <label>
            性别
            <select value={gender} onChange={(e) => setGender(e.target.value)}>
              <option value="unknown">未知</option>
              <option value="male">男</option>
              <option value="female">女</option>
            </select>
          </label>
          <label>
            颜色标识
            <span className="color-row">
              {COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  className={`color-dot ${color === c ? 'color-dot--active' : ''}`}
                  style={{ background: c }}
                  onClick={() => setColor(c)}
                  aria-label={`颜色 ${c}`}
                />
              ))}
            </span>
          </label>
          {error && <p className="form-error">{error}</p>}
          <div className="subject-form__actions">
            {editing && (
              <button type="button" className="btn btn--ghost" onClick={() => setEditing(null)}>
                取消编辑
              </button>
            )}
            <button type="submit" className="btn btn--primary">
              {editing ? '保存' : '创建'}
            </button>
          </div>
        </form>
      </section>

      <ConfirmDialog
        open={Boolean(toDelete)}
        title={`删除「${toDelete?.name ?? ''}」`}
        danger
        confirmText="确认删除"
        onCancel={() => setToDelete(null)}
        onConfirm={onDelete}
      >
        <p>
          删除将归档该档案的会话、软删其文件与事件，审计日志保留。
          原件文件不会被物理删除。此操作不可撤销。
        </p>
      </ConfirmDialog>
    </div>
  );
}
