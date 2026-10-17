import { money, performance, value, type Asset, type Month, type Dividend } from './workbook';

export function DividendChart({ dividends, year, onMonth }: { dividends: Dividend[]; year: string; onMonth: (month: string) => void }) {
  const rows = Array.from({ length: 12 }, (_, i) => {
    const month = `${year}-${String(i + 1).padStart(2, '0')}`;
    const ds = dividends.filter(d => d.date.startsWith(month));
    return { month, actual: ds.filter(d => d.status === '실수령').reduce((s, d) => s + d.amount, 0), expected: ds.filter(d => d.status === '예상').reduce((s, d) => s + d.amount, 0) };
  });
  const max = Math.max(1, ...rows.map(r => r.actual + r.expected));
  return <><div className="dividend-bars">{rows.map((r, i) => <button key={r.month} aria-label={`${i + 1}월 실수령 ${money(r.actual)}, 예상 ${money(r.expected)}`} onClick={() => onMonth(r.month)}><span className="dividend-bars-track"><i style={{ height: `${r.expected / max * 100}%` }} /><b style={{ height: `${r.actual / max * 100}%` }} /></span><small>{i + 1}</small></button>)}</div><p className="dividend-legend">{year}년 · <b>실수령</b> <span>예상</span></p></>;
}

export function AllocationChart({ assets, fx }: { assets: Asset[]; fx: number | null }) {
  const groups = new Map<string, number>();
  if (!assets.length || assets.some(a => value(a, fx) === null)) return null;
  for (const a of assets) groups.set(a.category, (groups.get(a.category) ?? 0) + value(a, fx)!);
  const total = [...groups.values()].reduce((s, n) => s + n, 0);
  if (!total) return null;
  const colors = ['#859cff', '#62b9b1', '#d7b269', '#b695d4', '#9aa7bc'];
  return <section className="dashboard-card"><h2>자산 구성</h2><div className="allocation-bar" aria-hidden="true">{[...groups].map(([name, n], i) => <span key={name} style={{ width: `${n / total * 100}%`, background: colors[i] }} />)}</div><ul className="allocation-legend">{[...groups].map(([name, n], i) => <li key={name}><i style={{ background: colors[i] }} /><b>{name}</b><span>{(n / total * 100).toFixed(1)}%</span><small>{money(n)}</small></li>)}</ul></section>;
}

export function ReturnChart({ months }: { months: Month[] }) {
  const rows = performance(months);
  const points = rows.flatMap((m, i) => m.cumulative === null ? [] : [{ i, value: m.cumulative }]);
  if (!points.length) return <p>월 기록을 입력하면 수익률 추이를 볼 수 있어요.</p>;
  const min = Math.min(0, ...points.map(p => p.value)), max = Math.max(1, ...points.map(p => p.value));
  const y = (n: number) => 150 - (n - min) / (max - min) * 120;
  const x = (i: number) => 30 + i / Math.max(1, rows.length - 1) * 540;
  return <svg className="workbook-return-chart" viewBox="0 0 600 190" role="img" aria-label="연속 월 기록의 누적 수익률. 아래 목록에서 정확한 수치를 확인할 수 있습니다."><line x1="30" x2="570" y1={y(0)} y2={y(0)} stroke="#35445d" />{points.map((p, j) => <g key={p.i}>{j > 0 && points[j - 1].i === p.i - 1 && new Date(`${rows[p.i].month}-01`).getTime() === Date.UTC(Number(rows[p.i - 1].month.slice(0, 4)), Number(rows[p.i - 1].month.slice(5)), 1) && <line x1={x(points[j - 1].i)} y1={y(points[j - 1].value)} x2={x(p.i)} y2={y(p.value)} stroke="#95aaff" strokeWidth="3" />}<circle cx={x(p.i)} cy={y(p.value)} r="4" fill="#b8c6ff"><title>{rows[p.i].month}: {p.value.toFixed(2)}%</title></circle></g>)}<text x="30" y="180" fill="#a5b3cd" fontSize="11">{rows[0].month}</text><text x="570" y="180" textAnchor="end" fill="#a5b3cd" fontSize="11">{rows.at(-1)?.month}</text></svg>;
}
