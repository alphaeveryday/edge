import type { CSSProperties } from 'react';

/** 방향 수치 — glyph-first(▲/▼), 색은 보조 신호 (디자인 시스템 규약). */
export interface DeltaProps {
  direction: 1 | -1 | 0;
  /** 등락률(%) 절대값 */
  pct: number;
  style?: CSSProperties;
}

export function formatDelta(direction: 1 | -1 | 0, pct: number): string {
  if (direction === 0) return `– ${pct.toFixed(2)}%`;
  const glyph = direction > 0 ? '▲' : '▼';
  const sign = direction > 0 ? '+' : '−';
  return `${glyph} ${sign}${pct.toFixed(2)}%`;
}

export function deltaClass(direction: 1 | -1 | 0): string {
  if (direction === 0) return 'delta delta-flat';
  return `delta ${direction > 0 ? 'delta-up' : 'delta-down'}`;
}

export function Delta({ direction, pct, style }: DeltaProps) {
  return (
    <span className={deltaClass(direction)} style={style}>
      {formatDelta(direction, pct)}
    </span>
  );
}
