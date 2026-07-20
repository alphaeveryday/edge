/* EDGE Console — modal dialog */
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './Icon';

export function Modal({
  title,
  sub,
  onClose,
  children,
  footer,
  w = 440,
}: {
  title: ReactNode;
  sub?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  w?: number;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" style={{ maxWidth: w }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-hd">
          <div style={{ minWidth: 0 }}>
            <h3>{title}</h3>
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          <button className="modal-x" onClick={onClose}>
            <Icon n="x" s={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
