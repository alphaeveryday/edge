import { useEffect, useState } from 'react';
import { request, ensureVisitorSession, type Catalog, type History } from './api';
import { InstrumentSearch } from './InstrumentSearch';
import { Button, TextInput, SectionHeading } from './design-system/components';
import { PerformanceChart } from './ReportCharts';
import { trackAction } from './analytics';
import type { Asset } from './workbook';

type Row = { instrument_id: string; name: string; weight: string };
type Result = { history: History; excluded: string[]; return_pct: number; message?: string };
export function Backtest({ assets, catalog }: { assets: Asset[]; catalog: Catalog | null }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [job, setJob] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!job) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await request<{ state: string; result: Result }>(`/backtests/${job}`);
        if (stopped) return;
        setError('');
        if (next.state === 'succeeded') { trackAction('backtest_completed'); setResult(next.result); setJob(null); setBusy(false); }
        else if (next.state === 'failed') { trackAction('backtest_failed'); setError(next.result.message || '가격 자료를 가져오지 못했어요. 다시 조회해 주세요.'); setJob(null); setBusy(false); }
        else timer = setTimeout(poll, 3000);
      } catch (error) { if (!stopped) {
        if (error instanceof Error && 'status' in error && [401, 404].includes(Number(error.status))) { setError(error.message); setJob(null); setBusy(false); }
        else { setError('조회 연결을 다시 시도하고 있어요. 잠시만 기다려 주세요.'); timer = setTimeout(poll, 3000); }
      } }
    }
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [job]);
  function copyHoldings() {
    const included = assets.filter(a => a.amount !== 0);
    if (!included.length) { setRows([]); setResult(null); setError('보유금액이 있는 종목을 먼저 담아 주세요.'); return; }
    const combined = new Map<string, Row>();
    for (const a of included) if (a.instrument_id) { const old = combined.get(a.instrument_id); combined.set(a.instrument_id, { instrument_id: a.instrument_id, name: a.name, weight: String(Number(old?.weight ?? 0) + (a.amount ?? 0)) }); }
    const next = [...combined.values()];
    if (next.length > 20) { setError('백테스트에는 최대 20개 종목을 담을 수 있어요. 비교할 종목을 검색해 담아 주세요.'); return; }
    const sum = next.reduce((s, a) => s + Number(a.weight), 0);
    const complete = included.every(a => a.amount !== null && a.instrument_id) && sum > 0;
    const units = complete ? next.map(r => Math.max(1, Math.floor(Number(r.weight) / sum * 10000))) : [];
    if (complete) { const largest = units.indexOf(Math.max(...units)); units[largest] += 10000 - units.reduce((s, n) => s + n, 0); }
    setRows(next.map((r, i) => ({ ...r, weight: complete ? String(units[i] / 100) : '' })));
    const tiny = complete && next.some(r => Number(r.weight) / sum < .0001);
    setResult(null); setError(complete ? tiny ? '0.01%보다 작은 보유분은 0.01%로 맞췄어요.' : '' : '보유금액을 확인하지 못한 종목이 있어요. 비교할 비중을 입력해 주세요.');
  }
  const total = rows.reduce((s, r) => s + Number(r.weight), 0);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!rows.length || rows.some(r => !r.weight || Number(r.weight) <= 0) || Math.abs(total - 100) > .01) { setError('각 종목의 비중을 넣고 합계를 100%로 맞춰 주세요.'); return; }
    const data = new FormData(e.currentTarget);
    setBusy(true); setError(''); setResult(null);
    try {
      await ensureVisitorSession();
      const response = await request<{ id: string }>('/backtests', { method: 'POST', body: JSON.stringify({ allocations: rows.map(r => ({ instrument_id: r.instrument_id, weight_pct: Number(r.weight) })), start: data.get('start') || null, end: data.get('end') || null }) });
      trackAction('backtest_requested'); setJob(response.id);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <section className="dashboard-card"><SectionHeading title="내 조합 백테스트" description="원하는 종목과 비중으로 과거 수익률을 확인해요." />
    <form onSubmit={submit} onChange={() => setResult(null)}><fieldset disabled={busy} className="editor-fields">
      <Button type="button" onClick={copyHoldings} disabled={!assets.some(a => a.instrument_id)}>내 보유 종목으로 시작</Button>
      <InstrumentSearch account="백테스트" catalog={catalog} onAdd={item => { if (rows.some(r => r.instrument_id === item.id)) return '이미 담은 종목이에요.'; if (rows.length >= 20) return '최대 20개 종목까지 담을 수 있어요.'; setRows([...rows, { instrument_id: item.id, name: item.name, weight: rows.length ? '' : '100' }]); setResult(null); }} />
      <div className="backtest-rows">{rows.map(r => <div key={r.instrument_id}><label>{r.name}<TextInput type="number" min="0.01" max="100" step="0.01" required value={r.weight} aria-label={`${r.name} 비중 (%)`} onChange={e => { setRows(rows.map(x => x === r ? { ...r, weight: e.target.value } : x)); setResult(null); }} /></label><Button type="button" aria-label={`${r.name} 제외`} onClick={() => { setRows(rows.filter(x => x !== r)); setResult(null); }}>삭제</Button></div>)}</div>
      <p>비중 합계 {total.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}%</p>
      <div className="editor-grid"><label>시작일 · 선택<TextInput name="start" type="date" /></label><label>종료일 · 선택<TextInput name="end" type="date" /></label></div>
      <p className="editor-note">기간을 비워두면 함께 비교할 수 있는 가장 긴 기간으로 계산해요. 분배금은 전액 재투자해요.</p>
      <Button variant="primary" loading={busy} disabled={!rows.length}>{busy ? '과거 가격을 조회하고 있어요' : '과거 수익률 조회'}</Button>
    </fieldset></form>
    {error && <p role="alert">{error}</p>}
    {result && <div aria-live="polite"><h3>기간 수익률 {result.return_pct > 0 ? '+' : ''}{result.return_pct.toLocaleString()}%</h3>{result.excluded.length > 0 && <p className="notice">자료가 없는 {result.excluded.join(', ')}을 제외하고 나머지 비중을 100%로 맞췄어요.</p>}<PerformanceChart showSources={false} history={result.history} label="내 조합" /></div>}
  </section>;
}
