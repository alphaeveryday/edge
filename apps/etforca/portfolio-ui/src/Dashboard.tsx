import { useEffect, useRef, useState } from 'react';
import { useConfirmation } from './design-system/confirmation';
import { Button, TextInput, Select, SectionHeading, Icon } from './design-system/components';
import { request, ensureVisitorSession, type Catalog, type Instrument } from './api';
import { accounts, money, needsSeed, withSeed, dividendMonthCovered, type Asset, type Saved, type Tracking, type Workbook } from './workbook';
import { preview } from './preview';
import { AllocationChart, DividendChart, DividendSummary } from './WorkbookCharts';
import { DividendReport } from './DividendReport';
import { Holdings } from './Holdings';
import { trackAction, trackScreen, type Screen } from './analytics';
import { InstrumentSearch } from './InstrumentSearch';
import { PerformanceChart } from './ReportCharts';
import { SeedMoney } from './SeedMoney';
import { Backtest } from './Backtest';
import { Analysis } from './Analysis';

const tabs = ['한눈에', '배당 달력', '수익 기록', '백테스트', 'AI 포폴 분석'];
const empty: Workbook = { title: '내 포트폴리오', fx: null, assets: [], dividends: [], months: [] };
const thisMonth = () => preview ? '2026-10' : new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(new Date()).slice(0, 7);

export function Dashboard() {
  const [saved, setSaved] = useState<Saved | null>(null);
  const [tracking, setTracking] = useState<Tracking>({ market: null, days: [] });
  const [trackingLoaded, setTrackingLoaded] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [trackingError, setTrackingError] = useState('');
  const [tab, setTab] = useState('한눈에');
  const [analysisOpened, setAnalysisOpened] = useState(false);
  useEffect(() => tab === 'AI 포폴 분석' ? undefined : trackScreen((['dashboard_overview', 'dashboard_dividends', 'dashboard_returns', 'dashboard_backtest'] as Screen[])[tabs.indexOf(tab)]), [tab]);
  const [account, setAccount] = useState('전체');
  const [month, setMonth] = useState(thisMonth);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Asset | null>(null);
  const [dirty, setDirty] = useState(false);
  const [entryCost, setEntryCost] = useState<number | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const launcher = useRef<HTMLElement | null>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const loadSequence = useRef(0);
  const trackingSequence = useRef(0);
  const { ask, confirmation, restoreFocus } = useConfirmation();
  async function loadCatalog() { try { setCatalog(await request<Catalog>('/catalog')); setCatalogError(''); } catch { setCatalogError('종목 목록을 불러오지 못했어요.'); } }
  async function loadTracking() {
    const sequence = ++trackingSequence.current;
    try { const next = await request<Tracking>('/workbook/tracking'); if (!Array.isArray(next.days)) throw new Error('Invalid tracking'); if (sequence === trackingSequence.current) { setTracking(next); setTrackingLoaded(true); setTrackingError(''); } }
    catch { if (sequence === trackingSequence.current) setTrackingError('자동 기록을 불러오지 못했어요. 잠시 후 다시 확인해 주세요.'); }
  }
  async function load() {
    const sequence = ++loadSequence.current;
    setLoading(true);
    try { await ensureVisitorSession(); const next = await request<Saved>('/workbook'); if (sequence === loadSequence.current) { setSaved(next); setError(''); return true; } }
    catch (e) { if (sequence === loadSequence.current) setError((e as Error).message); }
    finally { if (sequence === loadSequence.current) setLoading(false); }
    return false;
  }
  useEffect(() => { void load().then(ok => { if (ok) { void loadCatalog(); void loadTracking(); } }); const timer = setInterval(() => { if (!document.hidden) void loadTracking(); }, 30000); return () => { loadSequence.current++; trackingSequence.current++; clearInterval(timer); }; }, []);
  useEffect(() => { if (editing && dialog.current && !dialog.current.open) dialog.current.showModal(); }, [editing]);
  useEffect(() => { if (editing) { setEntryCost(editing.quantity !== null && editing.average !== null ? editing.quantity * editing.average : null); dialog.current?.querySelector<HTMLInputElement>(editing.instrument_id ? 'input[name="quantity"], input[name="amount"]' : 'input[role="combobox"]')?.focus(); } }, [editing?.instrument_id, editing?.id]);
  useEffect(() => { if (!editing && !busy && launcher.current) { const target = launcher.current.isConnected ? launcher.current : addButton.current; launcher.current = null; target?.focus(); } }, [editing, busy]);
  useEffect(() => { if (!busy && error) restoreFocus(); }, [busy, error]);
  useEffect(() => { if (!dirty && !busy) return; const prevent = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; }; window.addEventListener('beforeunload', prevent); return () => window.removeEventListener('beforeunload', prevent); }, [dirty, busy]);
  function closeEditor() { setDirty(false); dialog.current?.close(); setEditing(null); }
  async function cancelEditor() { if (!busy && (!dirty || await ask('저장하지 않은 수정을 취소할까요?', '수정 취소하기'))) closeEditor(); }
  async function reload() { if (dirty && !await ask('저장하지 않은 내용을 지우고 다시 불러올까요?', '다시 불러오기')) return; if (await load()) { closeEditor(); void loadTracking(); void loadCatalog(); } else restoreFocus(); }
  async function persist(workbook: Workbook) {
    if (!saved || busy || loading) return false;
    setBusy(true); setError(''); setMessage('');
    try { setSaved(await request<Saved>('/workbook', { method: 'POST', body: JSON.stringify({ version: saved.version, workbook }) })); trackAction('holdings_saved'); setMessage('저장했어요. 시세와 비중을 자동으로 계산해요.'); void loadTracking(); return true; }
    catch (e) { trackAction('holdings_save_failed'); setError((e as Error).message); return false; }
    finally { setBusy(false); }
  }
  const w = saved?.workbook ?? empty;
  const seedOnly = needsSeed(w) && w.assets.every(a => a.quantity === null && a.amount === null && a.weight !== null) && Math.abs(w.assets.reduce((sum, a) => sum + (a.weight ?? 0), 0) - 100) <= .01;
  const market = tracking.market && tracking.market.version === saved?.version ? tracking.market.payload : null;
  const assets = w.assets.filter(a => account === '전체' || a.account === account);
  const quotes = new Map(market?.assets.map(a => [a.id, a]) ?? []);
  const amountOf = (a: Asset) => quotes.get(a.id)?.value ?? (trackingLoaded && !trackingError && !tracking.market && a.quantity === null ? a.amount : null);
  const values = assets.map(amountOf);
  const total = values.every(v => v !== null) ? values.reduce<number>((sum, v) => sum + v!, 0) : null;
  const invested = assets.filter(a => a.category !== '현금');
  const profit = invested.length && invested.every(a => quotes.get(a.id)?.profit != null) ? invested.reduce((sum, a) => sum + quotes.get(a.id)!.profit!, 0) : null;
  const basis = profit !== null && invested.every(a => quotes.get(a.id)?.value != null) ? invested.reduce((sum, a) => sum + quotes.get(a.id)!.value!, 0) - profit : null;
  const returnPct = profit !== null && basis !== null && basis > 0 ? Number((profit / basis * 100).toFixed(2)) : null;
  const forecasts = (market?.forecasts ?? []).filter(d => d.month >= thisMonth() && assets.some(a => a.id === d.asset_id));
  const forecastComplete = !!market && market.date.slice(0, 7) === thisMonth() && !market.dividend_missing.some(id => assets.some(a => a.id === id));
  const forecastMonths = Array.from({ length: 13 }, (_, i) => new Date(Date.UTC(Number(thisMonth().slice(0, 4)), Number(thisMonth().slice(5)) - 1 + i, 1)).toISOString().slice(0, 7));
  const uncoveredMonths = forecastMonths.filter(m => assets.some(a => !dividendMonthCovered(a, quotes.get(a.id), m, market?.date.slice(0, 7) ?? null)));
  const unavailableMonths = uncoveredMonths.filter(m => !forecasts.some(d => d.month === m));
  const selectedComplete = forecastComplete && !uncoveredMonths.includes(month);
  const dividends = w.dividends.filter(d => assets.some(a => a.id === d.asset_id));
  const selected = forecasts.filter(d => d.month === month);
  function edit(a?: Asset) {
    if (busy || loading || seedOnly) return;
    trackAction(a ? 'holding_edit_opened' : 'holding_add_opened');
    launcher.current = document.activeElement as HTMLElement; setDirty(false); setError('');
    const quote = a ? quotes.get(a.id) : null;
    setEditing(a ? { ...a, quantity: a.quantity !== null ? quote?.quantity ?? a.quantity : null, average: quote?.average ?? a.average } : { id: crypto.randomUUID(), instrument_id: null, name: '', account: account === '전체' ? accounts[0] : account, category: '기타', currency: 'KRW', quantity: null, average: null, price: null, cost_fx: null, amount: null, weight: null, target: null, fee: 0, price_date: null });
  }
  function selectInstrument(item: Instrument) {
    if (!editing) return;
    if (w.assets.some(a => a.id !== editing.id && a.instrument_id === item.id && a.account === editing.account)) return '이 계좌에 이미 있어요. 보유 목록에서 수량을 수정해 주세요.';
    const currency = item.market === 'US' ? 'USD' : 'KRW';
    const existing = w.assets.some(a => a.id === editing.id);
    setEditing({ ...editing, instrument_id: item.id, name: item.name, currency, category: item.kind === 'cash' ? '현금' : editing.category === '현금' ? '기타' : editing.category,
      quantity: existing ? editing.quantity : null, average: existing && editing.currency === currency ? editing.average : null, amount: existing ? editing.amount : null }); setDirty(true);
  }
  async function saveAsset(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (!editing) return;
    if (!editing.instrument_id) { setError('목록에서 보유 종목을 선택해 주세요.'); return; }
    const f = new FormData(e.currentTarget), cash = editing.category === '현금';
    if (!w.assets.some(a => a.id === editing.id && a.instrument_id === editing.instrument_id && a.account === String(f.get('account'))) && w.assets.some(a => a.id !== editing.id && a.instrument_id === editing.instrument_id && a.account === String(f.get('account')))) { setError('이 계좌에 이미 있는 종목이에요. 기존 보유 수량을 수정해 주세요.'); return; }
    const quantity = f.get('quantity') ? Number(f.get('quantity')) : null;
    const amount = f.get('amount') ? Number(f.get('amount')) : null;
    const average = f.get('average') ? Number(f.get('average')) : null;
    if (!cash && quantity === null && amount === null) { setError('보유 수량을 입력해 주세요.'); return; }
    const stored = w.assets.find(a => a.id === editing.id && a.instrument_id === editing.instrument_id && a.currency === editing.currency);
    // Untouched quote-adjusted values keep their original stored split baselines.
    const savedQuantity = stored && quantity === editing.quantity ? stored.quantity : quantity;
    const savedAverage = stored && average === editing.average ? stored.average : average;
    const asset = { ...editing, account: String(f.get('account')), quantity: cash ? null : savedQuantity, quantity_revision: quantity !== null && quantity !== editing.quantity ? crypto.randomUUID() : editing.quantity_revision, average: savedAverage, average_revision: average !== editing.average ? crypto.randomUUID() : editing.average_revision, amount: cash || quantity === null ? amount : null, weight: null, target: null, price: null, price_date: null };
    if (await persist({ ...w, assets: w.assets.some(a => a.id === asset.id) ? w.assets.map(a => a.id === asset.id ? asset : a) : [...w.assets, asset] })) closeEditor();
  }
  const graphAssets = assets.map(a => ({ ...a, quantity: null, price: null, amount: amountOf(a) }));
  return <div className="dashboard" data-analytics-section="dashboard">
    <aside className="dashboard-nav"><a href={preview ? '?preview=input' : '?'} className="dashboard-brand"><Icon name="dashboard" size={24} />ETF Orca</a><nav aria-label="대시보드">{tabs.map(t => <Button key={t} aria-current={tab === t ? 'page' : undefined} onClick={() => { setTab(t); if (t === 'AI 포폴 분석') setAnalysisOpened(true); }}>{t}</Button>)}</nav><a href={preview ? '?preview=result' : '?'}>포트폴리오 분석 ↗</a></aside>
    <main className="dashboard-main"><header><div><p className="eyebrow">대시보드</p><h1>{w.title}</h1></div><label>계좌<Select value={account} onChange={e => setAccount(e.target.value)}><option>전체</option>{accounts.map(a => <option key={a}>{a}</option>)}</Select></label></header>
      <span className="dashboard-save-status" role="status">{busy ? '저장 중이에요.' : message}</span>
      {error && <div role="alert" className="dashboard-error">{error} <Button disabled={busy || loading} onClick={() => void reload()}>저장된 내용 다시 불러오기</Button></div>}
      {!saved ? <p role="status">{loading ? '불러오는 중…' : '저장된 내용을 불러오지 못했어요.'}</p> : <>
        {seedOnly && <section className="dashboard-card"><SeedMoney busy={busy} onSubmit={seed => { try { void persist(withSeed(w, seed)); } catch (e) { setError((e as Error).message); } }} /></section>}
        {needsSeed(w) && !seedOnly && <p className="notice">비중만 남아 있는 종목은 눌러서 보유 수량을 입력해 주세요.</p>}
        <section className="dashboard-stats" hidden={tab === 'AI 포폴 분석'}><article><span>총 자산</span><strong>{assets.length ? money(total) : '아직 비어 있어요'}</strong><small>{market ? `${market.date} 확인 · 최근 종가 기준` : assets.length ? '시세를 자동으로 가져오고 있어요' : '보유 종목부터 담아 보세요'}</small></article><article><span>보유 자산 평가손익</span><strong className={`profit-display ${profit === null ? '' : profit >= 0 ? 'gain' : 'loss'}`}><span className="profit-amount">{profit !== null && profit > 0 ? '+' : ''}{money(profit)}</span>{returnPct !== null && <> <span className="profit-percent">({returnPct > 0 ? '+' : ''}{returnPct.toFixed(2)}%)</span></>}</strong>{assets.some(a => a.currency === 'USD') && <small>해외 종목은 현재 환율 기준 · 매입 이후 환차익 제외</small>}</article><article><span>{forecastComplete ? "향후 12개월 예상 분배금" : "확인된 예상 분배금"}</span><strong>{(forecastComplete || forecasts.length > 0) ? money(forecasts.reduce((s, d) => s + d.amount, 0)) : '확인 중'}</strong><small>세전 · 최근 분배 이력 기준</small></article></section>
        {trackingError && <p role="alert">{trackingError} <Button onClick={() => void loadTracking()}>다시 확인</Button></p>}
        {market?.missing.some(id => !market.pending_splits?.includes(id) && assets.some(a => a.id === id)) && <p className="notice">일부 종목의 시세나 수량을 확인하지 못했어요. 종목을 눌러 보유 정보를 확인해 주세요.</p>}
        {market?.pending_splits?.some(id => assets.some(a => a.id === id)) && <p className="notice">분할된 종목은 분할 후 첫 종가를 확인하면 평가액을 표시해요.</p>}
        {tab === '한눈에' && <><div className="dashboard-overview"><AllocationChart assets={graphAssets} fx={market?.fx ?? null} /><section className="dashboard-card dividend-summary" data-analytics-section="dividend_summary"><SectionHeading className="card-heading" title="배당 분석" action={<Button onClick={() => setTab('배당 달력')}>달력 보기 <Icon name="chevronRight" size={16} /></Button>} /><DividendSummary forecasts={forecasts} complete={forecastComplete} total={total} /><DividendChart dividends={[]} forecasts={forecasts} complete={forecastComplete} unavailableMonths={unavailableMonths} startMonth={thisMonth()} selectedMonth={month} onMonth={m => { setMonth(m); setTab('배당 달력'); }} /></section></div>
          <section className="dashboard-card"><SectionHeading className="card-heading" title={`보유 자산 ${assets.length}`} action={<Button variant="primary" className="primary" ref={addButton} disabled={busy || loading || seedOnly || w.assets.length >= 30} onClick={() => edit()}><Icon name="plus" size={18} />종목 추가</Button>} />
            {!assets.length && <p className="empty-state">보유 종목을 검색하고 수량과 평균매수가를 입력해 주세요.</p>}
            <Holdings assets={assets} quotes={quotes} forecasts={forecasts} missing={market && market.date.slice(0, 7) === thisMonth() ? market.dividend_missing : assets.map(a => a.id)} total={total} fx={market?.fx ?? null} amountOf={amountOf} disabled={seedOnly} onEdit={edit} />
          </section></>}
        {tab === '배당 달력' && <><section className="dashboard-card"><SectionHeading title="배당 달력" description="앞으로 12개월, 월별 예상 분배금을 확인해요." /><DividendSummary forecasts={forecasts} complete={forecastComplete} total={total} /><DividendChart dividends={[]} forecasts={forecasts} complete={forecastComplete} unavailableMonths={unavailableMonths} startMonth={thisMonth()} selectedMonth={month} onMonth={setMonth} /><h3>{month.replace('-', '년 ')}월 · {selectedComplete ? "예상 " : selected.length ? "확인된 예상 " : "예상 미확인"}{(selectedComplete || selected.length > 0) && money(selected.reduce((s, d) => s + d.amount, 0))}</h3><p className="editor-note">최근 12개월 배당락 이력으로 추정한 세전 금액이에요. 지급월과 금액은 실제 공시에 따라 달라질 수 있어요.</p>
          <ul className="dividend-records">{selected.map((d, i) => <li key={d.asset_id + i}><span>{w.assets.find(a => a.id === d.asset_id)?.name}</span><b>예상 {money(d.amount)}</b></li>)}</ul>{!selected.length && <p className="empty-state">{selectedComplete ? "이 달의 예상 분배 내역이 없어요." : "배당 자료를 확인하지 못한 종목이 있어요. 확인된 내역부터 보여드려요."}</p>}
          {dividends.length > 0 && <details><summary>저장된 지급 내역</summary><ul>{[...dividends].sort((a, b) => b.date.localeCompare(a.date)).map(d => <li key={d.id}>{d.date} · {w.assets.find(a => a.id === d.asset_id)?.name} · {d.status} {money(d.amount)}</li>)}</ul></details>}
        </section><DividendReport quotes={quotes} assets={assets} dividends={dividends} forecasts={forecasts} missing={market?.dividend_missing ?? assets.map(a => a.id)} startMonth={thisMonth()} forecastStartMonth={market?.date.slice(0, 7) ?? null} /></>}
        {tab === '수익 기록' && <section className="dashboard-card"><SectionHeading title="일별 수익 기록" description="전체 계좌 · 등록한 날부터 하루에 한 번 자동 기록해요." />
          {tracking.days.length >= 2 && <PerformanceChart showSources={false} label="총자산" history={{ currency: 'KRW', points: tracking.days.map(d => ({ date: d.date, recommended: d.value, current: null })), basis: '전체 계좌 · 일별 평가액', source_url: '', method: '최근 종가와 환율 기준. 보유 수량 변경과 입출금도 자산 변화에 포함돼요.' }} />}
          {!tracking.days.length && <p className="empty-state">첫 시세를 확인하면 오늘부터 기록을 시작해요.</p>}
          <div className="return-records">{[...tracking.days].sort((a, b) => b.date.localeCompare(a.date)).map(d => {
            const held = d.assets.filter(a => a.price !== null || a.quantity === 0);
            const gain = held.length && held.every(a => a.profit !== null) ? held.reduce((s, a) => s + a.profit!, 0) : null;
            const basis = gain !== null && held.every(a => a.value !== null) ? held.reduce((s, a) => s + a.value!, 0) - gain : null;
            return <article key={d.date}><time dateTime={d.date}>{d.date}</time><span>총자산 <b>{money(d.value)}</b></span><span className={gain === null ? '' : gain >= 0 ? 'gain' : 'loss'}>평가손익 {gain !== null && gain > 0 ? '+' : ''}{money(gain)}{gain !== null && basis !== null && basis > 0 && ` (${gain > 0 ? '+' : ''}${(gain / basis * 100).toFixed(2)}%)`}</span></article>;
          })}</div>
        </section>}
        <div hidden={tab !== '백테스트'}><Backtest assets={graphAssets} catalog={catalog} /></div>
        {analysisOpened && <div hidden={tab !== 'AI 포폴 분석'}><Analysis embedded active={tab === 'AI 포폴 분석'} /></div>}
      </>}
    </main>
    {editing && <dialog ref={dialog} onCancel={e => { e.preventDefault(); void cancelEditor(); }} className="asset-editor" aria-labelledby="asset-editor-title"><form onSubmit={saveAsset} onChange={() => setDirty(true)} onInput={e => { const data = new FormData(e.currentTarget); const q = data.get('quantity'), p = data.get('average'); setEntryCost(q && p && Number.isFinite(Number(q) * Number(p)) ? Number(q) * Number(p) : null); }}><div className="card-heading"><h2 id="asset-editor-title">{w.assets.some(a => a.id === editing.id) ? '보유 자산 수정' : '보유 자산 추가'}</h2><Button type="button" aria-label="수정 닫기" disabled={busy} onClick={() => void cancelEditor()}>×</Button></div><fieldset disabled={busy || loading} className="editor-fields">
      <label>계좌<Select name="account" value={editing.account} onChange={e => setEditing({ ...editing, account: e.target.value })}>{accounts.map(a => <option key={a}>{a}</option>)}</Select></label>
      {editing.instrument_id ? <div className="selected-instrument"><strong>{editing.name}</strong>{!w.assets.some(a => a.id === editing.id) && <Button type="button" onClick={() => { setEditing({ ...editing, instrument_id: null }); setDirty(true); }}>종목 변경</Button>}</div> : <><InstrumentSearch account={editing.account} catalog={catalog} onAdd={selectInstrument} />{editing.name && <p>저장된 종목: {editing.name} · 자동 시세를 연결할 종목을 선택해 주세요.</p>}</>}
      {catalogError && <p role="alert">{catalogError} <Button type="button" onClick={() => void loadCatalog()}>목록 다시 불러오기</Button></p>}
      {editing.instrument_id && (editing.category === '현금' ? <label>현금 (원)<TextInput name="amount" type="number" min="0" max="10000000000000" step="any" defaultValue={editing.amount ?? ''} required /></label> : <div className="editor-grid" key={editing.instrument_id}>
        <div><label>보유 수량<TextInput name="quantity" type="number" inputMode="decimal" aria-describedby="quantity-help" min="0" max="10000000000000" step="any" placeholder="예: 10" defaultValue={editing.quantity ?? ''} required={editing.amount === null} /></label><small id="quantity-help">소수점 수량도 입력할 수 있어요.</small></div>
        <div><label>평균매수가 ({editing.currency === 'USD' ? '달러' : '원'}) · 선택<TextInput name="average" type="number" inputMode="decimal" aria-describedby="average-help" min="0" max="10000000000000" step="any" placeholder={editing.currency === 'USD' ? '예: 25.50' : '예: 15000'} defaultValue={editing.average ?? ''} /></label><small id="average-help">입력하면 평가손익을 함께 보여드려요.</small></div>
        {editing.amount !== null && editing.quantity === null && <label>가져온 보유금액 (원)<TextInput name="amount" type="number" min="0" max="10000000000000" step="any" defaultValue={editing.amount} /><small>수량을 입력하면 실제 보유 수량으로 추적해요.</small></label>}
      </div>)}
      {editing.category !== '현금' && entryCost !== null && <p className="entry-cost" role="status">매입금액 <strong>{editing.currency === 'USD' ? `$${entryCost.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}` : money(entryCost)}</strong></p>}
      <p className="editor-note">현재가·환율·보유 비중은 자동으로 계산해요.</p><Button variant="primary" className="primary" loading={busy} disabled={!editing.instrument_id}>{busy ? '저장 중…' : '저장'}</Button>
      {w.assets.some(a => a.id === editing.id) && <Button type="button" className="delete-asset" disabled={busy} onClick={async () => { if (await ask('이 종목을 보유 목록에서 삭제할까요? 이전 자동 수익 기록은 유지해요.') && await persist({ ...w, assets: w.assets.filter(a => a.id !== editing.id), dividends: w.dividends.filter(d => d.asset_id !== editing.id) })) closeEditor(); }}>종목 삭제</Button>}
      {error && <p role="alert">{error} <Button type="button" onClick={() => void reload()}>저장된 내용 다시 불러오기</Button></p>}
    </fieldset></form></dialog>}
    {confirmation}
  </div>;
}
