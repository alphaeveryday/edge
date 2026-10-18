import { useState } from 'react';
import { Button, Select, SectionHeading } from './design-system/components';
import { DividendChart } from './WorkbookCharts';
import { money, dividendMonthCovered, type Asset, type Dividend, type Forecast, type Quote } from './workbook';

export function DividendReport({ assets, dividends, forecasts, missing, startMonth, forecastStartMonth, quotes }: { assets: Asset[]; dividends: Dividend[]; forecasts: Forecast[]; missing: string[]; startMonth: string; forecastStartMonth: string | null; quotes: Map<string, Quote> }) {
  const currentYear = Number(startMonth.slice(0, 4));
  const [mode, setMode] = useState<'expected' | 'actual'>('expected');
  const [year, setYear] = useState(currentYear);
  const [month, setMonth] = useState(`${year}-01`);
  const years = [...new Set([year, currentYear, currentYear + 1, ...dividends.map(d => Number(d.date.slice(0, 4)))])].sort();
  const months = Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, '0')}`);
  const actual = dividends.filter(d => d.status === '실수령');
  const missingSelected = missing.filter(id => assets.some(a => a.id === id));
  const rows = assets.filter(a => a.category !== '현금').map(a => {
    return { asset: a, values: months.map(m => {
    if (mode === 'actual') {
      const recorded = actual.filter(d => d.asset_id === a.id && d.date.startsWith(m));
      return recorded.length ? recorded.reduce((s, d) => s + d.amount, 0) : null;
    }
    const covered = m >= startMonth && dividendMonthCovered(a, quotes.get(a.id), m, forecastStartMonth);
    const entries = forecasts.filter(d => d.asset_id === a.id && d.month === m);
    return entries.length ? entries.reduce((s, d) => s + d.amount, 0) : !covered || missing.includes(a.id) ? null : 0;
  }) }; });
  const sum = (values: (number | null)[]) => values.every(v => v === null) ? null : values.reduce<number>((s, v) => s + (v ?? 0), 0);
  const incompleteMonths = mode === 'actual' ? [] : months.filter(m => rows.some(r => m < startMonth || missing.includes(r.asset.id) || !dividendMonthCovered(r.asset, quotes.get(r.asset.id), m, forecastStartMonth)));
  const totals = months.map((_, i) => sum(rows.map(r => r.values[i])));
  return <section className="dashboard-card dividend-report"><SectionHeading title="연간 배당 내역" action={<label>연도<Select value={year} onChange={e => { setYear(Number(e.target.value)); setMonth(`${e.target.value}-01`); }}>{years.map(y => <option key={y} value={y}>{y}년</option>)}</Select></label>} />
    <div className="chart-switch" role="group" aria-label="배당 내역 종류"><Button aria-pressed={mode === 'expected'} onClick={() => setMode('expected')}>예상 배당</Button><Button aria-pressed={mode === 'actual'} onClick={() => setMode('actual')}>실수령 기록</Button></div>
    <p className="editor-note">{mode === 'expected' ? '세전 · 재투자 없이 합산 · 확인 가능한 향후 12개월 이력 기준' : '저장된 실수령 내역만 표시해요. 기록이 없는 달은 —로 보여요.'}</p>
    <div className="dividend-table-scroll" tabIndex={0} role="region" aria-label="종목별 월 배당 내역, 좌우로 이동"><table className="dividend-table"><thead><tr><th scope="col">종목명</th>{months.map(m => <th scope="col" key={m}>{Number(m.slice(5))}월</th>)}<th scope="col">합계</th></tr></thead><tbody>{rows.map(r => <tr key={r.asset.id}><th scope="row">{r.asset.name}<small>{r.asset.account}</small></th>{r.values.map((v, i) => <td key={i}>{v === null ? '—' : Math.round(v).toLocaleString('ko-KR')}</td>)}<td>{money(sum(r.values))}</td></tr>)}</tbody><tfoot><tr><th scope="row">확인된 합계</th>{totals.map((v, i) => <td key={i}>{v === null ? '—' : Math.round(v).toLocaleString('ko-KR')}</td>)}<td>{money(sum(totals))}</td></tr></tfoot></table></div>
    {!rows.length && <p className="empty-state">보유 종목을 추가하면 월별 배당을 볼 수 있어요.</p>}
    <DividendChart dividends={mode === 'actual' ? actual : []} forecasts={mode === 'expected' ? forecasts : []} complete={mode === 'actual' || !missingSelected.length} actualOnly={mode === 'actual'} unavailableMonths={months.filter((_, i) => totals[i] === null || totals[i] === 0 && incompleteMonths.includes(months[i]))} incompleteMonths={incompleteMonths} startMonth={`${year}-01`} monthsCount={12} selectedMonth={month} onMonth={setMonth} />
  </section>;
}
