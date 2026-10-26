import { ReactNode } from 'react';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  danger?: boolean;
  confirmText?: string;
  onConfirm: () => void;
  onCancel: () => void;
  children?: ReactNode;
}

/** 通用确认弹窗（删除成员级联、强制上传覆盖等）。 */
export default function ConfirmDialog({
  open,
  title,
  danger = false,
  confirmText = '确认',
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  if (!open) return null;
  return (
    <div className="modal-mask" role="dialog" aria-modal="true">
      <div className="modal">
        <h3>{title}</h3>
        <div className="modal__body">{children}</div>
        <div className="modal__actions">
          <button type="button" className="btn btn--ghost" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className={danger ? 'btn btn--danger' : 'btn btn--primary'}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
