import type { ReactNode } from 'react';

export interface ModalProps {
  open: boolean;
  title: string;
  width?: number;
  onClose: () => void;
  children: ReactNode;
  /** 하단 버튼 영역. 없으면 푸터를 그리지 않는다. */
  footer?: ReactNode;
}

/** 스크림 클릭으로 닫히는 센터 모달. 패널 내부 클릭은 전파를 막는다. */
export function Modal({ open, title, width = 400, onClose, children, footer }: ModalProps) {
  if (!open) return null;
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="card modal-panel" style={{ width }} onClick={(e) => e.stopPropagation()}>
        <div className="card-head">
          <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>
        {children}
        {footer && (
          <div
            style={{
              display: 'flex',
              gap: 8,
              justifyContent: 'flex-end',
              padding: '12px 20px',
              borderTop: '1px solid var(--border-faint)',
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
