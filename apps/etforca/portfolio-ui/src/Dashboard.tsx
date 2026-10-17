import { useConfirmation } from './design-system/confirmation';
import { Button, TextInput, Select, SectionHeading, Icon } from './design-system/components';
import { useEffect, useRef, useState } from 'react';
import { request, ensureVisitorSession } from './api';
import { accounts, cost, money, performance, purchases, value, type Asset, type Saved, type Workbook } from './workbook';
import { preview } from './preview';
import { AllocationChart, ReturnChart, DividendChart } from './WorkbookCharts';

const tabs = ['한눈에', '보유 자산', '배당 달력', '수익 기록', '추가매수'];
const empty: Workbook = { title: '내 포트폴리오', fx: null, assets: [], dividends: [], months: [] };
const number = (f: FormData, key: string) => f.get(key) === '' ? null : Number(f.get(key));

export function Dashboard() {
  const [saved, setSaved] = useState<Saved | null>(null);
  const [tab, setTab] = useState('한눈에');
  const [account, setAccount] = useState('전체');
  const [error, setError] = useState('');
  const [savedMessage, setSavedMessage] = useState('');
  const { ask, confirmation, restoreFocus } = useConfirmation();
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const loadSequence = useRef(0);
  const [editing, setEditing] = useState<Asset | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const launcher = useRef<HTMLElement | null>(null);
  useEffect(() => { if (editing && dialog.current && !dialog.current.open) dialog.current.showModal(); }, [editing]);
  function closeEditor() { dialog.current?.close(); setEditing(null); if (launcher.current?.isConnected) launcher.current.focus(); else document.querySelector<HTMLButtonElement>('.card-heading .primary')?.focus(); }
  const [month, setMonth] = useState(preview ? '2026-10' : new Date().getFullYear() + '-' + String(new Date().getMonth() + 1).padStart(2, '0'));
  const [budget, setBudget] = useState(1000000);
  async function load() {
    if (busy) return;
    const sequence = ++loadSequence.current;
    setLoading(true);
    try {
      await ensureVisitorSession();
      const next = await request<Saved>('/workbook');
      if (sequence === loadSequence.current) { setSaved(next); setError(''); }
    } catch (e) { if (sequence === loadSequence.current) setError((e as Error).message); }
    finally { if (sequence === loadSequence.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { loadSequence.current++; }; }, []);
  async function persist(workbook: Workbook) {
    if (!saved || busy || loading) return false;
    setBusy(true); setError(''); setSavedMessage('');
    try { setSaved(await request<Saved>('/workbook', { method: 'POST', body: JSON.stringify({ version: saved.version, workbook }) })); setSavedMessage('변경 내용을 저장했어요.'); return true; }
    catch (e) { setError((e as Error).message); return false; }
    finally { setBusy(false); restoreFocus(document.querySelector<HTMLElement>('.dashboard-nav [aria-current]')); }
  }
  const w = saved?.workbook ?? empty;
  const assets = w.assets.filter(a => account === '전체' || a.account === account);
  const weightTotal = assets.every(a => a.weight !== null) ? assets.reduce((s, a) => s + a.weight!, 0) : null;
  const values = assets.map(a => value(a, w.fx));
  const complete = values.every(v => v !== null);
  const total = complete ? values.reduce<number>((s, v) => s + v!, 0) : null;
  const invested = assets.filter(a => a.category !== '현금');
  const profit = invested.length && invested.every(a => cost(a) !== null && value(a, w.fx) !== null) ? invested.reduce((s, a) => s + value(a, w.fx)! - cost(a)!, 0) : null;
  const dividends = w.dividends.filter(d => assets.some(a => a.id === d.asset_id) && d.date.startsWith(month));
  const actual = dividends.filter(d => d.status === '실수령').reduce((s, d) => s + d.amount, 0);
  const expected = dividends.filter(d => d.status === '예상').reduce((s, d) => s + d.amount, 0);
  const plan = account === '전체' ? [] : purchases(assets, w.fx, budget);
  function edit(a?: Asset) { if (busy || loading) return; launcher.current = document.activeElement as HTMLElement; setEditing(a ?? { id: crypto.randomUUID(), name: '', account: account === '전체' ? accounts[0] : account, category: '기타', currency: 'KRW', quantity: null, average: null, price: null, cost_fx: null, amount: null, weight: null, target: null, fee: 0, price_date: null }); }
  async function saveAsset(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!editing) return;
    const f = new FormData(event.currentTarget);
    const a = { ...editing, name: String(f.get('name')).trim(), account: String(f.get('account')), category: String(f.get('category')), currency: String(f.get('currency')) as Asset['currency'], price_date: String(f.get('price_date')) || null };
    for (const key of ['quantity', 'average', 'price', 'cost_fx', 'amount', 'weight', 'target', 'fee'] as const) Object.assign(a, { [key]: number(f, key) });
    a.fee ??= 0;
    if (await persist({ ...w, assets: w.assets.some(x => x.id === a.id) ? w.assets.map(x => x.id === a.id ? a : x) : [...w.assets, a] })) closeEditor();
  }
  return <div className="dashboard">
    <aside className="dashboard-nav"><a href={preview ? '?preview=input' : '?'} className="dashboard-brand"><Icon name="dashboard" size={24} />ETF Orca</a><nav aria-label="대시보드">{tabs.map(t => <Button key={t} aria-current={tab === t ? 'page' : undefined} onClick={() => setTab(t)}>{t}</Button>)}</nav><a href={location.search.includes('preview=') ? '?preview=result' : '?'}>포트폴리오 분석 ↗</a></aside>
    <main className="dashboard-main"><fieldset className="dashboard-fields" disabled={busy || loading}><header><div><p className="eyebrow">대시보드</p><h1>{w.title}</h1></div><label>계좌<Select value={account} onChange={e => setAccount(e.target.value)}><option>전체</option>{accounts.map(a => <option key={a}>{a}</option>)}</Select></label></header>
      <span className="sr-only" role="status">{busy ? '저장 중이에요.' : savedMessage}</span>
      {error && <div role="alert" className="dashboard-error">{error} <Button disabled={busy || loading} onClick={() => void load()}>저장된 내용 다시 불러오기</Button></div>}
      {!saved ? <p role="status">{loading ? '불러오는 중…' : '저장된 내용을 불러오지 못했어요.'}</p> : <>
      <section className="dashboard-stats"><article><span>총 자산</span><strong>{assets.length ? money(total) : '아직 비어 있어요'}</strong>{!complete && <small>금액을 입력하면 합계를 볼 수 있어요</small>}</article><article><span>보유 자산 평가손익</span><strong className={profit === null ? undefined : profit >= 0 ? 'gain' : 'loss'}>{profit !== null && profit > 0 ? '+' : ''}{money(profit)}</strong></article><article><span>{month} 받은 배당</span><strong>{money(actual)}</strong><small>예정 {money(expected)}</small></article></section>
      {tab === '한눈에' && <div className="dashboard-overview"><AllocationChart assets={assets} fx={w.fx} /><section className="dashboard-card dividend-summary"><SectionHeading className="card-heading" title="월별 배당" action={<Button className="text-button" onClick={() => setTab('배당 달력')}>달력 보기 <Icon name="chevronRight" size={16} /></Button>} /><DividendChart dividends={w.dividends.filter(d => assets.some(a => a.id === d.asset_id))} year={month.slice(0, 4)} selectedMonth={month} onMonth={m => { setMonth(m); setTab('배당 달력'); }} /></section></div>}
      {(tab === '한눈에' || tab === '보유 자산') && <section className="dashboard-card"><SectionHeading className="card-heading" title={`보유 자산 ${assets.length}`} action={<Button variant="primary" className="primary" onClick={() => edit()}><Icon name="plus" size={18} />종목 추가</Button>} />
        {!assets.length && <p className="empty-state">보유 종목을 추가하거나 분석 결과에서 포트폴리오를 저장해 주세요.</p>}
        <div className="asset-list-heading" aria-hidden="true"><span>종목 · 계좌</span><span>평가금액 · 비중</span><span>평가손익</span></div><div className="asset-list">{assets.map(a => { const amount = value(a, w.fx); const base = cost(a); const gain = amount !== null && base !== null ? amount - base : null; return <Button className="asset-row" key={a.id} onClick={() => edit(a)}><span className="asset-symbol">{a.category === '현금' ? '₩' : a.name.slice(0, 1)}</span><span className="asset-name"><b>{a.name}</b><small>{a.account} · {a.category}{a.price_date ? ` · ${a.price_date}` : ''}</small></span><span><b>{money(amount)}</b><small>{amount !== null && total ? (amount / total * 100).toFixed(1) + '%' : a.weight !== null ? (account !== '전체' && weightTotal ? (a.weight / weightTotal * 100).toFixed(1) + '%' : a.weight + '% · 전체 기준') : '비중 —'}</small></span><span className={gain === null ? undefined : gain >= 0 ? 'gain' : 'loss'}>{gain !== null && gain > 0 ? '+' : ''}{money(gain)}<small>평가손익</small></span><span aria-hidden="true"><Icon name="chevronRight" size={16} /></span></Button>; })}</div>
        <details className="dashboard-settings"><summary>환율 설정</summary><form onSubmit={e => { e.preventDefault(); void persist({ ...w, fx: Number(new FormData(e.currentTarget).get('fx')) }); }}><label>USD → KRW<TextInput key={saved?.version} name="fx" type="number" min="0.01" max="100000" step="any" defaultValue={w.fx ?? ''} required /></label><Button disabled={busy}>저장</Button></form></details>
      </section>}
      {tab === '배당 달력' && <section className="dashboard-card"><div className="card-heading"><h2>배당 달력</h2><TextInput autoFocus aria-label="배당 월" type="month" value={month} onChange={e => { if (e.target.value) setMonth(e.target.value); }} /></div><DividendChart dividends={w.dividends.filter(d => assets.some(a => a.id === d.asset_id))} year={month.slice(0, 4)} selectedMonth={month} onMonth={setMonth} /><div className="dividend-calendar">{['일', '월', '화', '수', '목', '금', '토'].map(d => <span className="calendar-weekday" key={d}>{d}</span>)}{Array.from({ length: new Date(`${month}-01T12:00:00`).getDay() }, (_, i) => <span key={`blank-${i}`} />)}{Array.from({ length: new Date(Number(month.slice(0, 4)), Number(month.slice(5)), 0).getDate() }, (_, i) => { const ds = dividends.filter(d => Number(d.date.slice(8)) === i + 1); return <div key={i} className={ds.length ? 'has-dividend' : ''}><span>{i + 1}</span>{ds.map(d => <small key={d.id} title={w.assets.find(a => a.id === d.asset_id)?.name}>{d.status} {money(d.amount)}</small>)}</div>; })}</div>
        {tab === '배당 달력' && <><ul className="dividend-records">{dividends.map(d => <li key={d.id}><span>{d.date} · {w.assets.find(a => a.id === d.asset_id)?.name}</span><b>{d.status} {money(d.amount)}</b><Button disabled={busy} aria-label={`${d.date} 배당 삭제`} onClick={async () => { if (await ask('이 배당 기록을 삭제할까요?')) void persist({ ...w, dividends: w.dividends.filter(x => x.id !== d.id) }); }}>삭제</Button></li>)}</ul><form className="record-form" onSubmit={async e => { e.preventDefault(); const form = e.currentTarget; const f = new FormData(form); if (await persist({ ...w, dividends: [...w.dividends, { id: crypto.randomUUID(), asset_id: String(f.get('asset')), date: String(f.get('date')), status: String(f.get('status')) as '실수령' | '예상', amount: Number(f.get('amount')) }] })) form.reset(); }}><label>종목<Select name="asset" required><option value="">선택</option>{assets.map(a => <option value={a.id} key={a.id}>{a.name} · {a.account}</option>)}</Select></label><label>날짜<TextInput name="date" type="date" required /></label><label>구분<Select name="status"><option>실수령</option><option>예상</option></Select></label><label>세후 원화 금액<TextInput name="amount" type="number" min="0" max="10000000000000" step="0.01" required /></label><Button disabled={busy || !assets.length}>배당 기록</Button></form></>}
      </section>}
      {tab === '수익 기록' && <section className="dashboard-card"><h2>월별 수익 기록 <small>전체 계좌</small></h2><p>입출금이 있는 달은 증권사 월수익률을 입력해 주세요.</p><ReturnChart months={w.months} /><div className="return-records">{performance(w.months).map(m => <article key={m.month}><b>{m.month}</b><span>{money(m.value)}</span><span>월 {m.monthly === null ? '—' : (m.monthly * 100).toFixed(2) + '%'}</span><span>연속 기록 누적 {m.cumulative === null ? '—' : m.cumulative.toFixed(2) + '%'}</span><Button disabled={busy} aria-label={m.month + ' 월 기록 삭제'} onClick={async () => { if (await ask('이 월 기록을 삭제할까요?')) void persist({ ...w, months: w.months.filter(x => x.month !== m.month) }); }}>삭제</Button></article>)}</div><form className="record-form" onSubmit={async e => { e.preventDefault(); const f = new FormData(e.currentTarget); const month = String(f.get('month')); if (w.months.some(m => m.month === month) && !await ask(month + ' 수익 기록을 바꿀까요? 기존 값이 새 입력으로 바뀌어요.', '기록 바꾸기')) return; void persist({ ...w, months: [...w.months.filter(m => m.month !== month), { month, value: Number(f.get('value')), deposit: Number(f.get('deposit')), withdrawal: Number(f.get('withdrawal')), return_pct: number(f, 'return_pct') }] }); }}><label>월<TextInput name="month" type="month" required /></label>{[['value', '월말 총자산'], ['deposit', '외부 입금'], ['withdrawal', '외부 출금'], ['return_pct', '월수익률 (%) · 선택']].map(([key, label]) => <label key={key}>{label}<TextInput name={key} type="number" min={key === 'return_pct' ? -100 : 0} max={key === 'return_pct' ? 10000 : 10000000000000} step="any" defaultValue={key === 'deposit' || key === 'withdrawal' ? 0 : undefined} required={key !== 'return_pct'} /></label>)}<Button disabled={busy}>월 기록 저장</Button></form></section>}
      {tab === '추가매수' && <section className="dashboard-card"><h2>추가매수 계획</h2><label>추가 투자금 (원)<TextInput type="number" min="0" max="10000000000000" value={budget} onChange={e => setBudget(Number(e.target.value))} /></label>{account === '전체' ? <p>위에서 매수할 계좌를 선택해 주세요.</p> : !plan.length ? <p>보유 자산의 평가금액·환율·목표 비중을 확인해 주세요. 목표 비중 합계는 100% 이하여야 해요.</p> : <><ul className="purchase-list">{plan.filter(p => p.amount > 0).map(p => <li key={p.asset.id}><span>{p.asset.name}</span><strong className="gain">{money(p.amount)} 매수</strong><small>{p.quantity === null ? '금액 기준' : `${p.quantity}주`}</small></li>)}</ul><p>배정하지 않은 비중은 현금으로 남겨요.</p><p>남는 금액 {money(budget - plan.reduce((s, p) => s + p.amount, 0))}</p></>}</section>}
      </>}
    </fieldset></main>
    {editing && <dialog ref={dialog} onCancel={e => { e.preventDefault(); closeEditor(); }} className="asset-editor" aria-labelledby="asset-editor-title" onKeyDown={e => { if (e.key === 'Escape') closeEditor(); }}><form onSubmit={saveAsset}><div className="card-heading"><h2 id="asset-editor-title">{w.assets.some(a => a.id === editing.id) ? '보유 자산 수정' : '보유 자산 추가'}</h2><Button type="button" aria-label="수정 닫기" onClick={closeEditor}>×</Button></div><fieldset disabled={busy || loading} className="editor-fields"><label>종목명<TextInput autoFocus name="name" defaultValue={editing.name} required maxLength={250} /></label><div className="editor-grid"><label>계좌<Select name="account" defaultValue={editing.account}>{accounts.map(a => <option key={a}>{a}</option>)}</Select></label><label>자산군<Select name="category" defaultValue={editing.category}>{['주식', '채권', '금·원자재', '기타', '현금'].map(a => <option key={a}>{a}</option>)}</Select></label><label>통화<Select name="currency" defaultValue={editing.currency}><option>KRW</option><option>USD</option></Select></label>{([['amount', '평가금액 (원)'], ['weight', '현재 비중 (%)'], ['quantity', '보유 수량'], ['average', '평균 매입가'], ['price', '현재가'], ['cost_fx', '매입 환율 (USD)'], ['target', '계좌 내 목표 비중 (%)'], ['fee', '수수료 (원)']] as const).map(([key, label]) => <label key={key}>{label}<TextInput name={key} type="number" min={key === 'cost_fx' ? '0.01' : '0'} max={key === 'target' || key === 'weight' ? 100 : key === 'cost_fx' ? 100000 : 10000000000000} step="any" defaultValue={editing[key] ?? ''} /></label>)}<label>가격 기준일<TextInput name="price_date" type="date" defaultValue={editing.price_date ?? ''} /></label></div><p className="editor-note">수량과 현재가를 입력하면 평가금액을 자동 계산해요.</p><Button variant="primary" className="primary" loading={busy}>{busy ? '저장 중…' : '저장'}</Button>{w.assets.some(a => a.id === editing.id) && <Button type="button" className="delete-asset" disabled={busy} onClick={async () => { if (await ask('이 종목과 연결된 배당 기록을 삭제할까요?') && await persist({ ...w, assets: w.assets.filter(a => a.id !== editing.id), dividends: w.dividends.filter(d => d.asset_id !== editing.id) })) closeEditor(); }}>종목 삭제</Button>}{error && <p role="alert">{error}</p>}</fieldset></form></dialog>}
    {confirmation}
  </div>;
}
