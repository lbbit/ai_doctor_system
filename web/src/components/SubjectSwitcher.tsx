import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSubjectStore } from '../stores/subjectStore';

/** Subject 切换器大 chip（AC-2.4 常驻显示当前档案；E-8 切换确认）。 */
export default function SubjectSwitcher() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const subjects = useSubjectStore((s) => s.subjects);
  const current = useSubjectStore((s) => s.current);
  const pendingSwitch = useSubjectStore((s) => s.pendingSwitch);
  const selectSubject = useSubjectStore((s) => s.selectSubject);
  const commitPending = useSubjectStore((s) => s.commitPendingSwitch);
  const cancelPending = useSubjectStore((s) => s.cancelPendingSwitch);

  const onSelect = (id: string) => {
    const result = selectSubject(id);
    if (result !== 'missing') setOpen(false);
    // result === 'pending' → 由 pendingSwitch 弹窗处理（E-8）
  };

  return (
    <div className="subject-switcher">
      <button
        type="button"
        className="subject-chip"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="subject-chip__avatar" style={{ background: current?.color || '#3B82F6' }}>
          {current?.avatar || '🙂'}
        </span>
        <span className="subject-chip__name">{current ? current.name : '请选择档案'}</span>
        <span className="subject-chip__caret">▾</span>
      </button>

      {open && (
        <ul className="subject-menu" role="listbox">
          {subjects.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                role="option"
                aria-selected={current?.id === s.id}
                className={current?.id === s.id ? 'selected' : ''}
                onClick={() => onSelect(s.id)}
              >
                <span className="subject-chip__avatar" style={{ background: s.color || '#10B981' }}>
                  {s.avatar || '👤'}
                </span>
                <span>{s.name}</span>
                <span className="subject-menu__relation">{s.relation}</span>
              </button>
            </li>
          ))}
          <li className="subject-menu__manage">
            <button type="button" onClick={() => { setOpen(false); navigate('/app/subjects'); }}>
              + 管理成员
            </button>
          </li>
        </ul>
      )}

      {pendingSwitch && (
        <div className="modal-mask" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>切换档案对象</h3>
            <p>
              将切换到 <strong>{pendingSwitch.to.name}</strong> 的档案，当前会话将结束并新建会话。
            </p>
            <div className="modal__actions">
              <button type="button" className="btn btn--ghost" onClick={cancelPending}>
                取消
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => {
                  commitPending();
                  navigate('/app/chat');
                }}
              >
                确认切换
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
