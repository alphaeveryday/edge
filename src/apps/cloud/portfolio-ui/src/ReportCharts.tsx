import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { History } from './api';

export const chartColors = ['#7089fb', '#9fd0ff', '#f2b866', '#b49ee8', '#75c6b0', '#eaa4be'];

export function PortfolioDonut({ holdings }: { holdings: { name: string; weight_pct: number; color: string }[] }) {
  let offset = 0;
  return <div className="donut md" role="img" aria-label={holdings.map(h => `${h.name} ${h.weight_pct}%`).join(', ')}><svg viewBox="0 0 42 42" className="donut-enter">{holdings.map((h, i) => {
    const start = offset; offset += h.weight_pct;
    const arc = `${Math.max(0, h.weight_pct - .8)} ${100 - Math.max(0, h.weight_pct - .8)}`;
    return <circle key={i} cx="21" cy="21" r="15.915" fill="none" stroke={h.color} strokeWidth="6.4" strokeDasharray={arc} strokeDashoffset={-start} style={{ '--arc': arc, animationDelay: `${i * 65}ms` } as CSSProperties} />;
  })}</svg><div className="donut__c"><b>{holdings.length}개</b></div></div>;
}

export function PerformanceChart({ history }: { history: History }) {
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!('IntersectionObserver' in window) || matchMedia('(prefers-reduced-motion: reduce)').matches) { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) { setVisible(true); observer.disconnect(); } }, { threshold: .15 });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const points = history.points;
  if (points.length < 2) return <p className="notice">성과를 비교할 기간의 자료가 부족해요.</p>;
  const values = points.flatMap(p => [p.recommended, ...(p.current === null ? [] : [p.current])]);
  const low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
  const x = (i: number) => 16 + i / (points.length - 1) * 278;
  const y = (value: number) => 124 - (value - low) / span * 100;
  const line = (key: 'recommended' | 'current') => points.map((p, i) => p[key] === null ? '' : `${x(i)},${y(p[key]!)}`).join(' ');
  const hasCurrent = points.every(p => p.current !== null);
  return <figure ref={ref} className={'perf report-history' + (visible ? ' is-visible' : '')}>
    <figcaption>{history.basis}</figcaption><div className="history-legend"><span>● 추천 구성</span>{hasCurrent && <span>● 현재 구성</span>}</div>
    <svg viewBox="0 0 310 155" role="img" aria-label="추천 구성과 현재 구성의 과거 성과"><line className="base" x1="16" x2="294" y1="124" y2="124" />{hasCurrent && <polyline className="a history-line" pathLength="1" points={line('current')} />}<polyline className="b history-line" pathLength="1" points={line('recommended')} /><text className="x" x="16" y="150">{points[0].date}</text><text className="x" x="294" y="150" textAnchor="end">{points.at(-1)!.date}</text></svg>
    <div className="history-values"><span>추천 구성 {points.at(-1)!.recommended.toLocaleString('ko-KR')}</span>{hasCurrent && <span>현재 구성 {points.at(-1)!.current!.toLocaleString('ko-KR')}</span>}</div>
    <a href={history.source_url} target="_blank" rel="noreferrer">성과 자료 출처 ↗</a>
  </figure>;
}
