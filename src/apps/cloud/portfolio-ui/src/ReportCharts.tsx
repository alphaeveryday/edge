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
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    if (!('IntersectionObserver' in window) || matchMedia('(prefers-reduced-motion: reduce)').matches) { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) { setVisible(true); observer.disconnect(); } }, { threshold: .15 });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const points = history.points;
  if (points.length < 2) return <p className="notice">성과를 비교할 기간의 자료가 부족해요.</p>;
  const values = points.flatMap(p => [p.recommended, ...(p.current === null ? [] : [p.current])]);
  const low = Math.min(...values), high = Math.max(...values), span = high - low || Math.max(high * .05, 1);
  const min = low - span * .1, max = high + span * .1;
  const first = Date.parse(points[0].date), last = Date.parse(points.at(-1)!.date);
  const x = (i: number) => 42 + (Date.parse(points[i].date) - first) / (last - first || 1) * 260;
  const y = (value: number) => 148 - (value - min) / (max - min) * 128;
  const line = (key: 'recommended' | 'current') => points.map((p, i) => p[key] === null ? '' : `${x(i)},${y(p[key]!)}`).join(' ');
  const hasCurrent = points.every(p => p.current !== null);
  const active = selected ?? points.length - 1;
  const isKRW = history.currency === 'KRW';
  const valueLabel = (value: number) => `${Math.round(value).toLocaleString('ko-KR')}${isKRW ? '원' : ''}`;
  return <figure ref={ref} className={'perf report-history' + (visible ? ' is-visible' : '')}>
    <figcaption>{history.basis}</figcaption><div className="history-legend"><span>● 추천 구성</span>{hasCurrent && <span>● 현재 구성</span>}</div>
    <svg viewBox="0 0 320 180" role="img" aria-label={history.interval === '1d' ? '최근 1년 일별 추천 구성과 현재 구성의 과거 성과' : '추천 구성과 현재 구성의 과거 성과'} onPointerMove={event => {
      const rect = event.currentTarget.getBoundingClientRect();
      const time = first + Math.max(0, Math.min(1, ((event.clientX - rect.left) / rect.width * 320 - 42) / 260)) * (last - first);
      setSelected(points.reduce((best, p, i) => Math.abs(Date.parse(p.date) - time) < Math.abs(Date.parse(points[best].date) - time) ? i : best, 0));
    }}>
      {[min, (min + max) / 2, max].map(v => <g key={v}><line className="base" x1="42" x2="302" y1={y(v)} y2={y(v)} /><text className="x" x="37" y={y(v) + 3} style={{ textAnchor: "end" }}>{isKRW ? `${(v / 10000).toFixed(1)}만` : v.toFixed(1)}</text></g>)}
      {hasCurrent && <polyline className="a history-line" pathLength="1" points={line('current')} />}<polyline className="b history-line" pathLength="1" points={line('recommended')} />
      {selected !== null && <><line className="history-cursor" x1={x(active)} x2={x(active)} y1="20" y2="148" /><circle className="b" cx={x(active)} cy={y(points[active].recommended)} r="3" /></>}
      {[0, Math.floor((points.length - 1) / 2), points.length - 1].map(i => <text key={i} className="x" x={x(i)} y="174" style={{ textAnchor: i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle' }}>{points[i].date.slice(2).replaceAll('-', '.')}</text>)}
    </svg>
    <input className="history-slider" type="range" min="0" max={points.length - 1} value={active} onChange={event => setSelected(Number(event.target.value))} aria-label="과거 성과 날짜 선택" aria-valuetext={points[active].date} />
    <p className="history-date">{points[active].date}</p><div className="history-values"><span>추천 구성 <b>{valueLabel(points[active].recommended)}</b></span>{hasCurrent && <span>현재 구성 <b>{valueLabel(points[active].current!)}</b></span>}</div>
    {history.current_unavailable_reason && <p className="notice">{history.current_unavailable_reason}</p>}<details><summary>계산 기준과 출처</summary><p>{history.method}</p>{history.sources?.length ? history.sources.map(source => <a key={source.instrument_id} href={source.url} target="_blank" rel="noreferrer">{source.instrument_id} ↗</a>) : <a href={history.source_url} target="_blank" rel="noreferrer">성과 자료 출처 ↗</a>}</details>
  </figure>;
}
