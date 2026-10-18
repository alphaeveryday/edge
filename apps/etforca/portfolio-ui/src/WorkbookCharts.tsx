import { chartPalette, SectionHeading } from './design-system/components';
import { Button } from './design-system/components';
import { useRef } from 'react';
import { money, performance, value, type Asset, type Month, type Dividend, type Forecast } from './workbook';

export function DividendChart({ dividends, forecasts, complete, startMonth, selectedMonth, onMonth }: { dividends: Dividend[]; forecasts: Forecast[]; complete: boolean; startMonth: string; selectedMonth: string; onMonth: (month: string) => void }) {
  const scroll = useRef<HTMLDivElement>(null);
  const rows = Array.from({ length: 12 }, (_, i) => {
    const month = new Date(Date.UTC(Number(startMonth.slice(0, 4)), Number(startMonth.slice(5)) - 1 + i, 1)).toISOString().slice(0, 7);
    const ds = dividends.filter(d => d.date.startsWith(month));
    return { month, actual: ds.filter(d => d.status === '실수령').reduce((s, d) => s + d.amount, 0), expected: forecasts.filter(d => d.month === month).reduce((s, d) => s + d.amount, 0) };
  });
  const max = Math.max(1, ...rows.map(r => r.actual + r.expected));
  function move(direction: number) { scroll.current?.scrollBy({ left: direction * scroll.current.clientWidth * .75, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); }
  return <><div className="dividend-navigation"><span>{rows[0].month} ~ {rows.at(-1)!.month}</span><Button aria-label="이전 배당 월 보기" onClick={() => move(-1)}>←</Button><Button aria-label="다음 배당 월 보기" onClick={() => move(1)}>→</Button></div><div className="dividend-scroll" ref={scroll} tabIndex={0} role="region" aria-label="12개월 배당 그래프, 좌우로 이동"><div className="dividend-bars">{rows.map(r => <Button key={r.month} aria-pressed={r.month === selectedMonth} aria-label={`${r.month} 실수령 ${money(r.actual)}, ${complete ? '예상 ' + money(r.expected) : r.expected ? '확인된 예상 ' + money(r.expected) : '예상 미확인'}`} onClick={() => onMonth(r.month)}><span className="dividend-bars-track"><i style={{ height: `${r.expected / max * 100}%` }} /><b style={{ height: `${r.actual / max * 100}%` }} /></span><small>{r.month.slice(2).replace('-', '.')}</small></Button>)}</div></div><p className="dividend-legend"><b>실수령</b> <span>예상 · 세전{!complete && ' · 일부 자료 미확인'}</span></p></>;
}

export function AllocationChart({ assets, fx }: { assets: Asset[]; fx: number | null }) {
  const groups = new Map<string, number>();
  if (!assets.length || assets.some(a => value(a, fx) === null)) return null;
  for (const a of assets) groups.set(a.category, (groups.get(a.category) ?? 0) + value(a, fx)!);
  const total = [...groups.values()].reduce((s, n) => s + n, 0);
  if (!total) return null;
  const colors = chartPalette;
  return <section className="dashboard-card"><SectionHeading className="card-heading" title="자산 구성" /><div className="allocation-bar" aria-hidden="true">{[...groups].map(([name, n], i) => <span key={name} style={{ width: `${n / total * 100}%`, background: colors[i] }} />)}</div><ul className="allocation-legend">{[...groups].map(([name, n], i) => <li key={name}><i style={{ background: colors[i] }} /><b>{name}</b><span>{(n / total * 100).toFixed(1)}%</span><small>{money(n)}</small></li>)}</ul></section>;
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
