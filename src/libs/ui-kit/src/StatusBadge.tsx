import type { ReactNode } from 'react';

/**
 * StatusBadge — 시맨틱 컬러를 쓰는 유일한 프리미티브.
 * 색은 상태 신호 전용이고 나머지는 그레이스케일이다 (디자인 시스템 규약).
 */
export type BadgeTone = 'active' | 'exposed' | 'neutral' | 'gated' | 'warn' | 'blocked' | 'env';

const TONES: Record<BadgeTone, { fg: string; bg: string; bd: string }> = {
  active: { fg: '#2f7d5b', bg: 'rgba(47,125,91,0.10)', bd: 'rgba(47,125,91,0.22)' },
  exposed: { fg: '#2f7d5b', bg: 'rgba(47,125,91,0.10)', bd: 'rgba(47,125,91,0.22)' },
  neutral: { fg: '#6b6b73', bg: 'rgba(107,107,115,0.10)', bd: 'rgba(107,107,115,0.20)' },
  gated: { fg: '#6b6b73', bg: 'rgba(107,107,115,0.10)', bd: 'rgba(107,107,115,0.20)' },
  warn: { fg: '#9a6a17', bg: 'rgba(154,106,23,0.10)', bd: 'rgba(154,106,23,0.22)' },
  blocked: { fg: '#b4453b', bg: 'rgba(180,69,59,0.10)', bd: 'rgba(180,69,59,0.22)' },
  env: { fg: '#2e5aac', bg: 'rgba(46,90,172,0.10)', bd: 'rgba(46,90,172,0.22)' },
};

export interface StatusBadgeProps {
  tone?: BadgeTone;
  dot?: boolean;
  children: ReactNode;
}

export function StatusBadge({ tone = 'neutral', dot = true, children }: StatusBadgeProps) {
  const t = TONES[tone];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        height: 23,
        padding: '0 9px',
        borderRadius: 5,
        fontSize: 12,
        fontWeight: 500,
        lineHeight: 1,
        whiteSpace: 'nowrap',
        color: t.fg,
        background: t.bg,
        border: `0.5px solid ${t.bd}`,
      }}
    >
      {dot && <span style={{ width: 6, height: 6, borderRadius: '50%', background: t.fg, flex: 'none' }} />}
      {children}
    </span>
  );
}
