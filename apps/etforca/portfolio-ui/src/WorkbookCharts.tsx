import { chartPalette, SectionHeading } from './design-system/components';
import { Button } from './design-system/components';
import { useRef, useState } from 'react';
import { classifyAsset } from './assetClassification';
import { money, performance, value, type Asset, type Month, type Dividend, type Forecast } from './workbook';

export function DividendChart({ dividends, forecasts, complete, startMonth, selectedMonth, onMonth, monthsCount = 13, actualOnly = false, unavailableMonths = [], incompleteMonths = [] }: { dividends: Dividend[]; forecasts: Forecast[]; complete: boolean; startMonth: string; selectedMonth: string; onMonth: (month: string) => void; monthsCount?: number; actualOnly?: boolean; unavailableMonths?: string[]; incompleteMonths?: string[] }) {
  const scroll = useRef<HTMLDivElement>(null);
  const rows = Array.from({ length: monthsCount }, (_, i) => {
    const month = new Date(Date.UTC(Number(startMonth.slice(0, 4)), Number(startMonth.slice(5)) - 1 + i, 1)).toISOString().slice(0, 7);
    const ds = dividends.filter(d => d.date.startsWith(month));
    return { month, actual: ds.filter(d => d.status === '실수령').reduce((s, d) => s + d.amount, 0), expected: forecasts.filter(d => d.month === month).reduce((s, d) => s + d.amount, 0) };
  });
  // Actual and forecast are separate series, never added as if both were received.
  const rawMax = Math.max(1, ...rows.flatMap(r => [r.actual, r.expected]));
  const step = 10 ** Math.floor(Math.log10(rawMax));
  const max = Math.ceil(rawMax / step) * step;
  function description(r: typeof rows[number]) {
    if (unavailableMonths.includes(r.month)) return `${r.month} ${actualOnly ? '실수령 미기록' : '예상 미확인'}`;
    if (actualOnly) return `${r.month} 실수령 ${money(r.actual)}`;
    return `${r.month} ${complete && !incompleteMonths.includes(r.month) ? '예상 ' + money(r.expected) : r.expected ? '확인된 예상 ' + money(r.expected) : '예상 미확인'}`;
  }
  function move(direction: number) { scroll.current?.scrollBy({ left: direction * scroll.current.clientWidth * .75, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); }
  return <><div className="dividend-navigation"><span>{rows[0].month} ~ {rows.at(-1)!.month}</span><Button aria-label="이전 배당 월 보기" onClick={() => move(-1)}>←</Button><Button aria-label="다음 배당 월 보기" onClick={() => move(1)}>→</Button></div><div className="dividend-plot"><div className="dividend-axis" aria-hidden="true">{[1, .75, .5, .25, 0].map(r => <span key={r}>{new Intl.NumberFormat('ko-KR', { notation: 'compact', maximumFractionDigits: 1 }).format(max * r)}</span>)}</div><div className="dividend-scroll" ref={scroll} tabIndex={0} role="region" aria-label="12개월 배당 그래프, 좌우로 이동"><div className="dividend-bars">{rows.map(r => <Button key={r.month} aria-pressed={r.month === selectedMonth} aria-label={description(r)} title={description(r)} onClick={() => onMonth(r.month)}><span className="dividend-bars-track">{unavailableMonths.includes(r.month) && <span className="dividend-unavailable">—</span>}<i style={{ height: `${r.expected / max * 100}%` }} /><b style={{ height: `${r.actual / max * 100}%` }} /></span><small>{r.month.slice(2).replace('-', '.')}</small></Button>)}</div></div></div><p className="dividend-legend">{actualOnly ? <b>실수령 · 기록된 금액 합계</b> : <span>예상 · 세전 · 재투자 없이 합산{!complete && ' · 일부 자료 미확인'}</span>}</p></>;
}

export function AllocationChart({ assets, fx }: { assets: Asset[]; fx: number | null }) {
  const [dimension, setDimension] = useState<'region' | 'theme'>('region');
  const groups = new Map<string, number>();
  const incomplete = assets.some(a => value(a, fx) === null);
  for (const a of assets) { const n = value(a, fx); if (n !== null && n > 0) { const name = classifyAsset(a)[dimension]; groups.set(name, (groups.get(name) ?? 0) + n); } }
  const total = [...groups.values()].reduce((s, n) => s + n, 0);
  const rows = [...groups].sort((a, b) => b[1] - a[1]);
  const colors = ['var(--chart-coral)', 'var(--chart-ocean)', 'var(--chart-violet)', 'var(--chart-slate)', 'var(--chart-teal)', ...chartPalette];
  let offset = 0;
  return <section className="dashboard-card allocation-card"><SectionHeading className="card-heading" title="자산 구성" /><div className="chart-switch" role="group" aria-label="자산 분류"><Button aria-pressed={dimension === 'region'} onClick={() => setDimension('region')}>투자 지역</Button><Button aria-pressed={dimension === 'theme'} onClick={() => setDimension('theme')}>투자 테마</Button></div>
    {total > 0 ? <><svg className="allocation-donut" viewBox="0 0 200 200" role="img" aria-label={rows.map(([name, n]) => `${name} ${(n / total * 100).toFixed(1)}%`).join(', ')}>{rows.map(([name, n], i) => { const start = offset; offset += n / total * 100; return <circle key={name} cx="100" cy="100" r="70" pathLength="100" fill="none" stroke={colors[i % colors.length]} strokeWidth="32" strokeDasharray={`${n / total * 100} ${100 - n / total * 100}`} strokeDashoffset={-start} transform="rotate(-90 100 100)" />; })}</svg><ul className="allocation-legend">{rows.map(([name, n], i) => <li key={name}><i style={{ background: colors[i % colors.length] }} /><b>{name}</b><span>{(n / total * 100).toFixed(1)}%</span><small>{money(n)}</small></li>)}</ul></> : <p className="empty-state">{assets.length ? '평가금액을 확인하면 구성을 보여드려요.' : '보유 종목을 추가해 주세요.'}</p>}
    {incomplete && <p className="editor-note">확인된 평가금액 기준이에요. 시세가 없는 종목은 제외했어요.</p>}
  </section>;
}

export function DividendSummary({ forecasts, annualEstimate, total }: { forecasts: Forecast[]; annualEstimate: number | null; total: number | null }) {
  const complete = annualEstimate !== null;
  const annual = annualEstimate ?? (forecasts.length ? forecasts.reduce((sum, d) => sum + d.amount, 0) : null);
  return <div className="dividend-metrics"><div><span>예상 배당률</span><strong>{complete && annual !== null && total !== null && total > 0 ? (annual / total * 100).toFixed(1) + '%' : '—'}</strong></div><div><span>{complete ? '예상 연간' : '확인된 예상'}</span><strong>{money(annual)}</strong></div><div><span>월평균</span><strong>{complete && annual !== null ? money(annual / 12) : '—'}</strong></div></div>;
}

export function ReturnChart({ months }: { months: Month[] }) {
  const rows = performance(months);
  const points = rows.flatMap((m, i) => m.cumulative === null ? [] : [{ i, value: m.cumulative }]);
  if (!points.length) return <p>월 기록을 입력하면 수익률 추이를 볼 수 있어요.</p>;
  const min = Math.min(0, ...points.map(p => p.value)), max = Math.max(1, ...points.map(p => p.value));
  const y = (n: number) => 150 - (n - min) / (max - min) * 120;
  const x = (i: number) => 30 + i / Math.max(1, rows.length - 1) * 540;
  return <svg className="workbook-return-chart" viewBox="0 0 600 190" role="img" aria-label="연속 월 기록의 누적 수익률. 아래 목록에서 정확한 수치를 확인할 수 있습니다."><line x1="30" x2="570" y1={y(0)} y2={y(0)} stroke="var(--color-border)" />{points.map((p, j) => <g key={p.i}>{j > 0 && points[j - 1].i === p.i - 1 && new Date(`${rows[p.i].month}-01`).getTime() === Date.UTC(Number(rows[p.i - 1].month.slice(0, 4)), Number(rows[p.i - 1].month.slice(5)), 1) && <line x1={x(points[j - 1].i)} y1={y(points[j - 1].value)} x2={x(p.i)} y2={y(p.value)} stroke="var(--color-primary)" strokeWidth="3" />}<circle cx={x(p.i)} cy={y(p.value)} r="4" fill="var(--color-primary)"><title>{rows[p.i].month}: {p.value.toFixed(2)}%</title></circle></g>)}<text x="30" y="180" fill="var(--color-text-secondary)" fontSize="11">{rows[0].month}</text><text x="570" y="180" textAnchor="end" fill="var(--color-text-secondary)" fontSize="11">{rows.at(-1)?.month}</text></svg>;
}
