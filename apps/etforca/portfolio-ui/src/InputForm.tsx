import { Button, TextInput, TextArea, chartPalette } from './design-system/components';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { request, ensureVisitorSession, type Catalog, type Chip, type Instrument, type Holding, type Input } from './api';
import { trackAction, trackScreen } from './analytics';
import { inputDraftKey, legacyDraftKey, readInputDraft, inputDraftFromInput } from './inputDraft';
import { useConfirmation } from './design-system/confirmation';
import { bulkHoldings, fromPercents, toPercents } from './bulkHoldings';
import { cashInstrument, matchInstruments } from './InstrumentSearch';
import { PlanInput, planGuidance, taxPayload, usableAccounts, horizonLabel, monthlyOff } from './PlanInput';
import { Avatar, Glyph, Quota, man, manInput, won as wonText } from './flow';
import type { TaxProfile } from './accountEligibility';
import type { Saved } from './workbook';
import { pendingKey } from './preview';

const accounts = ['일반 계좌', 'ISA', '연금저축', 'IRP'];
const steps = ['지금 가진 것', '앞으로의 계획', '궁금한 점'];
const asks = ['이대로 가도 될까요?', '매달 얼마씩 어디에 넣을까요?', '하나만 바꾼다면 뭘 바꿀까요?'];
// 꼭 지킬 조건(서버가 검사한다). '종목 n개 이하'는 1~20개.
const chipLabels: [Chip['kind'], string][] = [['no_leverage', '레버리지 빼기'], ['some_bonds', '채권도 조금'], ['dividend', '배당 받기'], ['max_positions', '종목 개 이하'], ['no_sell', '갖고 있는 건 팔지 않기'], ['kr_listed_etf_only', '국내 상장 ETF만']];
const sheetBenefit: Record<string, string> = { '일반 계좌': '제한 없음', ISA: '번 돈 200만 원까지 세금 0원', 연금저축: '세액공제', IRP: '세액공제' };
// 목록을 못 받아도 바로 담을 수 있게 많이 담는 종목은 화면에 둔다(종목 코드는 서버 목록과 같다).
const popular: (Instrument & { note: string })[] = [
  { id: 'KR:360750', market: 'KR', ticker: '360750', code: '360750', name: 'TIGER 미국S&P500', kind: 'etf', note: '미국 큰 기업 500곳 · 가장 많이 담아요' },
  { id: 'KR:379810', market: 'KR', ticker: '379810', code: '379810', name: 'KODEX 미국나스닥100', kind: 'etf', note: '미국 기술 기업 100곳' },
  { id: 'KR:069500', market: 'KR', ticker: '069500', code: '069500', name: 'KODEX 200', kind: 'etf', note: '한국 큰 기업 200곳' },
  { id: 'KR:005930', market: 'KR', ticker: '005930', code: '005930', name: '삼성전자', kind: 'stock', note: '국내 주식' },
  { ...cashInstrument, note: '예수금 그대로 적어 주세요' }];
// 서버 계약과 같다: 개별주는 연금저축·IRP 에 못 담고, 미국 상장 종목은 일반 계좌에만 담는다.
const blockedIn = (account: string, item: { kind?: string; market?: string; instrument_id?: string; id?: string }) =>
  item.kind === 'stock' && ['연금저축', 'IRP'].includes(account) ? '주식은 못 담아요'
    : account !== '일반 계좌' && (item.market === 'US' || (item.instrument_id ?? item.id ?? '').startsWith('US:')) ? '미국 상장은 일반 계좌만' : '';
const itemLabel = (item: Instrument) => item.kind === 'cash' ? '원화 현금' : `${item.market === 'KR' ? '국내' : '미국'} ${item.kind === 'stock' ? '주식' : '상장 ETF'} · ${item.ticker}`;

export function InputForm({ onSubmit, busy, initialInput, start, onExit }: { onSubmit: (input: Input) => void; busy: boolean; initialInput?: Input | null; start?: 'fresh' | 'import'; onExit?: () => void }) {
  const [initial] = useState(() => initialInput ? inputDraftFromInput(initialInput) : readInputDraft());
  const { ask, confirmation, restoreFocus } = useConfirmation();
  const heading = useRef<HTMLElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  // '아직 투자 전이에요'(S1)로 들어오면 가진 것 없이 계획부터 시작한다(첫 화면부터 그 단계로 센다).
  const [fresh, setFresh] = useState(start === 'fresh' || (initial?.fresh ?? false));
  const [step, setStep] = useState(start === 'fresh' ? 1 : initial?.step ?? 0);
  useEffect(() => trackScreen((['input_holdings', 'input_plan', 'input_ask'] as const)[step]), [step]);
  const [holdings, setHoldings] = useState<Holding[]>(initial?.holdings ?? []);
  // 고른 종목은 바로 그 줄의 금액 칸으로 — 찾기 → 계좌 → 금액 → Enter 로 다음 검색.
  const focusRow = useRef<string | null>(null);
  useEffect(() => {
    if (!focusRow.current) return;
    reveal(document.querySelector<HTMLInputElement>(`[data-holding="${CSS.escape(focusRow.current)}"]`));
    focusRow.current = null;
  }, [holdings]);
  const [selected, setSelected] = useState(initial?.selected ?? ['일반 계좌']);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [catalogError, setCatalogError] = useState(false);
  useEffect(() => { let stopped = false; setCatalogError(false); request<Catalog>('/workbook/catalog').then(data => { if (!stopped) setCatalog(data); }).catch(() => { if (!stopped) setCatalogError(true); }); return () => { stopped = true; }; }, [catalogAttempt]);
  const [question, setQuestion] = useState(initial?.question ?? '');
  const [horizon, setHorizon] = useState(initial?.horizon ?? '');
  const [how, setHow] = useState(initial?.how ?? '');
  const [note, setNote] = useState(initial?.note ?? '');
  const [chips, setChips] = useState<Chip[]>(initial?.chips ?? []);
  const count = String(chips.find(c => c.kind === 'max_positions')?.value ?? '');
  const [dividendGoal, setDividendGoal] = useState(initial?.dividendGoal ?? '');
  const [contribution, setContribution] = useState(initial?.contribution ?? '');
  const [tax, setTax] = useState<TaxProfile | null>(initial?.tax ?? null);
  const [saving, setSaving] = useState(false);
  const [draftId] = useState(() => initial?.draftId ?? crypto.randomUUID());
  const [mode, setMode] = useState<'amount' | 'percent'>(initial?.mode ?? 'amount');
  const [total, setTotal] = useState(initial?.total ?? '');
  const [alternate, setAlternate] = useState<Holding[]>(initial?.alternate ?? []);
  const [storageError, setStorageError] = useState(false);
  useEffect(() => {
    try { sessionStorage.setItem(inputDraftKey, JSON.stringify({ flow: 3, step, holdings, alternate, selected, question, horizon, how, interests: [], note, chips, dividendGoal, contribution, keyword: '', tax, draftId, mode, total, fresh }));
      // Any v2 write supersedes a leftover v1 draft, so clearing v2 later cannot bring the old one back.
      sessionStorage.removeItem(legacyDraftKey); setStorageError(false); }
    catch { setStorageError(true); }
  }, [step, holdings, alternate, selected, question, horizon, how, note, chips, dividendGoal, contribution, tax, draftId, mode, total, fresh]);
  const previousStep = useRef(step);
  useEffect(() => { if (step !== previousStep.current) { heading.current?.focus({ preventScroll: true }); window.scrollTo(0, 0); previousStep.current = step; } }, [step]);
  const [inputError, setInputError] = useState('');
  const [hasSaved, setHasSaved] = useState(false);
  useEffect(() => {
    let stopped = false;
    void ensureVisitorSession().then(() => request<Saved>('/workbook'))
      .then(saved => { if (!stopped) setHasSaved(!!saved.workbook?.assets?.length); })
      .catch(() => { /* 확인하지 못하면 버튼만 숨긴다 — 입력은 그대로 할 수 있다. */ });
    return () => { stopped = true; };
  }, []);
  // 시작 화면에서 고른 길: '아직 투자 전이에요'는 계획부터, '저장한 포트폴리오로 분석'은 불러오기부터.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (start === 'fresh' && !initial?.fresh) trackAction('recommend_started'); // 같은 초안에서 다시 눌러도 한 번
    if (start === 'import') void reuse();
  }, []);
  const revealing = useRef(false);
  const searchOpened = useRef(false);
  // 포커스를 옮긴 칸이 하단 버튼·화면 밖·모바일 키보드 뒤에 숨지 않게 직접 맞춘다.
  function reveal(element: HTMLElement | null | undefined) {
    if (!element) return;
    revealing.current = true; // 코드가 옮긴 포커스는 방문자가 검색을 연 것이 아니다
    element.focus({ preventScroll: true });
    revealing.current = false;
    const view = window.visualViewport, viewBottom = view ? view.offsetTop + view.height : innerHeight;
    // 넓은 화면에서는 버튼 패널이 옆 열에 있어 칸을 가리지 않는다 — 가로로 겹칠 때만 그 위를 한계로 본다.
    const box = element.getBoundingClientRect(), cta = bar.current?.getBoundingClientRect();
    const limit = Math.min(cta && cta.left < box.right && cta.right > box.left ? cta.top : viewBottom, viewBottom) - 8;
    if (box.bottom > limit) window.scrollBy(0, box.bottom - limit);
    else if (box.top < 8) window.scrollBy(0, box.top - 8);
  }
  const [query, setQuery] = useState('');
  const [searchError, setSearchError] = useState('');
  const options = useMemo(() => query.trim() ? matchInstruments([cashInstrument, ...(catalog?.instruments || []).filter(i => i.id !== cashInstrument.id)], query, 8) : [], [query, catalog]);
  const empty = !!catalog && !!query.trim() && !options.length;
  // 검색 한 번에 한 번만 센다(입력한 글은 보내지 않는다). 한글 조합 중 0건이 깜빡여도 같은 검색이다.
  const emptyReported = useRef(false);
  useEffect(() => { if (!query.trim()) emptyReported.current = false; else if (empty && !emptyReported.current) { emptyReported.current = true; trackAction('instrument_search_empty'); } }, [empty, query]);
  const [sheet, setSheet] = useState<{ item?: Instrument; from?: string } | null>(null);
  const [choice, setChoice] = useState('일반 계좌');
  const sheetRef = useRef<HTMLDialogElement>(null);
  const sheetGo = useRef<HTMLButtonElement>(null);
  // 시트가 열리면 '담기'에 포커스 — 찾기 Enter → 계좌 Enter → 금액으로 키보드만으로 이어진다.
  useEffect(() => { if (sheet && !sheetRef.current?.open) { sheetRef.current?.showModal(); sheetGo.current?.focus(); } }, [sheet]);
  const moving = sheet?.from ? holdings.filter(h => h.account === sheet.from) : [];
  const blockReason = (account: string) => sheet?.item ? blockedIn(account, sheet.item) : moving.map(h => blockedIn(account, h)).find(Boolean) || '';
  function openSheet(next: { item?: Instrument; from?: string }) {
    const last = holdings.at(-1)?.account ?? '일반 계좌';
    const rows = next.item ? [next.item] : holdings.filter(h => h.account === next.from);
    setChoice(next.from ?? (rows.every(r => !blockedIn(last, r)) ? last : '일반 계좌'));
    setSearchError(''); setSheet(next);
  }
  function pick(item: Instrument) {
    if (holdings.length >= 20) { setSearchError('최대 20종목까지 담을 수 있어요. 기존 종목을 빼고 추가해 주세요.'); return; }
    openSheet({ item });
  }
  function confirmSheet() {
    const account = choice;
    if (sheet?.item) {
      const item = sheet.item;
      if (holdings.some(h => h.account === account && h.instrument_id === item.id)) { setSearchError('이미 담은 종목이에요. 보유 금액이나 비중을 수정해 주세요.'); sheetRef.current?.close(); return; }
      focusRow.current = account + '|' + item.id;
      setHoldings([...holdings, { name: item.name, account, amount_krw: '', kind: item.kind, instrument_id: item.id }]);
      setQuery('');
      trackAction('input_holding_added');
    } else if (sheet?.from && account !== sheet.from) {
      if (moving.some(h => holdings.some(x => x.account === account && x.instrument_id === h.instrument_id))) { setInputError(`${account}에 같은 종목이 이미 있어요. 하나를 빼고 옮겨 주세요.`); sheetRef.current?.close(); return; }
      const move = (rows: Holding[]) => rows.map(h => h.account === sheet.from ? { ...h, account } : h);
      setHoldings(move(holdings)); setAlternate(move(alternate));
    }
    if (!selected.includes(account)) setSelected([...selected, account]);
    sheetRef.current?.close();
  }
  function openedSearch() {
    if (searchOpened.current || revealing.current) return;
    searchOpened.current = true;
    trackAction('input_search_opened');
  }
  const [bulk, setBulk] = useState('');
  const [bulkAccount, setBulkAccount] = useState('일반 계좌');
  const amountOk = (v: string | null) => (mode === 'amount' ? /^\d+$/ : /^(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/).test(v || '') && Number(v) > 0 && Number(v) <= (mode === 'percent' ? 100 : 1e13);
  const invalidHolding = holdings.some(h => !amountOk(h.amount_krw));
  const percentHundredths = holdings.reduce((n, h) => n + Math.round(Number(h.amount_krw) * 100), 0);
  const percentSum = (percentHundredths / 100).toLocaleString('ko-KR', { maximumFractionDigits: 2 });
  const amountSum = holdings.reduce((n, h) => n + (Number(h.amount_krw) || 0), 0);
  // 전체 금액은 만 원 단위로 받되 원까지(소수 넷째 자리) 보관한다. 넘거나 비면 고치게 하고, 잘라 맞추지 않는다.
  const totalOk = /^\d{0,10}(\.\d{0,4})?$/.test(total) && Number(total) > 0 && Number(total) * 1e4 <= 1e13;
  // 비중으로 넣어도 서버에는 전체 금액을 비중대로 나눈 원 단위 금액을 보낸다(합계 = 전체 금액) — 전체 금액이 없는 분석은 없다.
  const split = mode === 'percent' && totalOk ? fromPercents(Math.round(Number(total) * 1e4), holdings.map(h => Number(h.amount_krw) || 0)) : null;
  const tooSmall = !!split && holdings.some((h, i) => Number(h.amount_krw) > 0 && !split[i]);
  const holdingsValid = holdings.length > 0 && !invalidHolding && (mode !== 'percent' || percentHundredths === 10000 && totalOk && !tooSmall);
  const won = (h: Holding, i: number) => !Number(h.amount_krw) ? null : mode === 'amount' ? String(Math.round(Number(h.amount_krw))) : split?.[i] ? String(split[i]) : null;
  function changeMode(next: 'amount' | 'percent') {
    if (next === mode) return;
    const kept = holdings.map(h => alternate.find(a => a.account === h.account && a.instrument_id === h.instrument_id)?.amount_krw ?? '');
    // 금액은 비중을 정확히 정하므로 다시 적게 하지 않고 바꾼다. 전체 금액도 금액 합계로 채운다.
    const converted = next === 'percent' && !invalidHolding && holdings.length ? toPercents(holdings.map(h => Number(h.amount_krw))) : kept;
    if (next === 'percent' && !invalidHolding && amountSum > 0) setTotal(String(amountSum / 1e4)); // 금액이 정한 전체 금액, 원 단위 그대로(1.5만 원)
    const filled = next === 'amount' ? converted.map((v, i) => v || (split && percentHundredths === 10000 && split[i] ? String(split[i]) : '')) : converted;
    setAlternate(holdings); setHoldings(holdings.map((h, i) => ({ ...h, amount_krw: filled[i] }))); setMode(next);
  }
  function payload(planning = fresh): Input {
    const usable = usableAccounts(selected, [...new Set(holdings.map(h => h.account))], tax);
    // 월 납입은 서버가 읽는 숫자 꼴로 고쳐 보낸다('.5' → '0.5', '1.' → '1').
    const sent = chips.flatMap<Chip>(c => c.kind !== 'max_positions' ? [{ kind: c.kind }] : c.value && c.value >= 1 && c.value <= 20 ? [{ kind: c.kind, value: c.value }] : []);
    return { holdings: holdings.map((h, i) => ({ ...h, amount_krw: won(h, i) })), horizon, question, input_mode: 'amount', planning,
      monthly_krw: how === '매달 꾸준히' && Number(contribution) > 0 ? Math.round(Number(contribution) * 1e4) : null, goal_text: note, constraints: sent,
      preferences: { accounts: usable, contribution: how + (contribution && how === '매달 꾸준히' ? ` ${Number(contribution)}만 원` : ''), ...(initial?.dividendGoal ? { dividend_goal: dividendGoal } : {}), interests: [], note: '', tax_profile: taxPayload(tax, usable) } };
  }
  async function save() {
    await ensureVisitorSession();
    await request('/drafts/' + draftId, { method: 'POST', body: JSON.stringify(payload()) });
  }
  async function reuse() {
    setSaving(true); setInputError('');
    try {
      await ensureVisitorSession();
      const saved = await request<Saved>('/workbook');
      const assets = saved.workbook?.assets || [];
      const listed = catalog ?? await request<Catalog>('/workbook/catalog');
      if (!assets.length) throw new Error('저장된 보유 종목이 없어요.');
      if (assets.length > 20) throw new Error('분석은 최대 20종목까지 가능해요. 분석할 종목을 입력해 주세요.');
      const rows: Holding[] = assets.map(asset => {
        const instrumentId = asset.instrument_id || (asset.category === '현금' ? 'CASH:KRW' : null);
        if (!instrumentId) throw new Error(`${asset.name}의 종목 코드를 확인할 수 없어요. 대시보드에서 종목을 다시 선택해 주세요.`);
        const item = listed.instruments.find(row => row.id === instrumentId && row.kind !== 'cash');
        if (instrumentId !== 'CASH:KRW' && !item) throw new Error(`${asset.name}을 종목 목록에서 찾지 못했어요. 대시보드에서 종목을 다시 선택해 주세요. 저장된 보유 종목은 그대로 유지돼요.`);
        const market = saved.tracking?.market?.version === saved.version ? saved.tracking.market.payload : null;
        const missing = market?.missing.includes(asset.id) || market?.pending_prices?.includes(asset.id);
        const quote = market?.assets.find(row => row.id === asset.id);
        const amount = instrumentId === 'CASH:KRW' ? asset.amount : missing ? null : quote?.value;
        return { name: asset.name, instrument_id: instrumentId, account: asset.account,
          kind: instrumentId === 'CASH:KRW' ? 'cash' : item!.kind === 'stock' ? 'stock' : 'etf', amount_krw: amount == null ? '' : String(Math.round(amount)) };
      });
      if (holdings.length && !await ask('현재 입력을 저장된 보유 종목으로 바꿀까요?', '불러오기')) return;
      try { sessionStorage.removeItem(pendingKey); } catch { /* Import remains available in memory without browser storage. */ }
      setHoldings(rows); setSelected([...new Set([...selected, ...rows.map(row => row.account)])]); setAlternate([]); setMode('amount'); setFresh(false); setStep(0);
    } catch (error) { setInputError((error as Error).message); }
    finally { setSaving(false); restoreFocus(); }
  }
  const held = [...new Set(holdings.map(h => h.account))];
  const plan = planGuidance({ how, contribution, horizon, selected, tax, held });
  async function advance() {
    if (saving) return;
    if (holdings.length && !holdingsValid) { setInputError('보유 종목의 금액 또는 비중을 확인해 주세요.'); setStep(0); return; }
    if (step === 2 && plan) { setInputError(plan); setStep(1); return; }
    setSaving(true); setInputError('');
    try { await save(); } catch (error) { setInputError((error as Error).message); setSaving(false); return; }
    setSaving(false);
    if (step < 2) { setStep(step + 1); return; }
    onSubmit({ ...payload(), draft_id: draftId });
  }
  const countOk = !chips.some(c => c.kind === 'max_positions') || /^\d{1,2}$/.test(count) && Number(count) >= 1 && Number(count) <= 20;
  const valid = step === 0 ? holdingsValid || fresh && !holdings.length : step === 1 ? !plan : countOk;
  const guidance = step === 0 ? (valid ? '' : !holdings.length ? '종목을 하나 이상 담으면 넘어갈 수 있어요'
    : holdings.some(h => !(Number(h.amount_krw) > 0)) ? mode === 'percent' ? '각 종목의 비중을 입력해 주세요.' : '보유 종목의 금액을 입력해 주세요.'
    : invalidHolding ? mode === 'percent' ? '비중은 100% 이하, 소수 둘째 자리까지 적어 주세요.' : '금액은 10조 원 이하로 적어 주세요.'
    : percentHundredths !== 10000 ? `비중 합계가 100%가 되면 넘어갈 수 있어요. 지금 ${percentSum}%예요.` : Number(total) * 1e4 > 1e13 ? '전체 금액은 10조 원 이하로 적어 주세요.' : tooSmall ? '전체 금액이 너무 작아 비중대로 원 단위로 나눌 수 없어요. 전체 금액을 확인해 주세요.' : '전체 금액을 적으면 넘어갈 수 있어요') : step === 1 ? plan : countOk ? '' : '종목 수는 1~20개로 적어 주세요';
  // Enter on an amount returns to the search, so the next holding needs no pointer.
  function nextSearch(e: KeyboardEvent<HTMLInputElement>) { if (e.key === 'Enter') { e.preventDefault(); reveal(searchBox.current); } }
  const weights = holdings.map(h => mode === 'percent' ? Number(h.amount_krw) || 0 : amountSum > 0 ? (Number(h.amount_krw) || 0) / amountSum * 100 : 0);
  let filled = 0;
  // 비중이 100%를 넘으면 넘친 몫을 빨갛게 보인다(넘은 뒤의 종목은 막대에 들어갈 자리가 없다).
  const segments = weights.map((w, i) => { const room = Math.max(0, Math.min(w, 100 - filled)); const over = filled + w > 100.005; filled += w; return { i, width: room, over }; }).filter(s => s.width > 0);
  const groups = [...new Set(holdings.map(h => h.account))];
  const barSegments = segments.map(s => <span key={s.i} style={{ flexGrow: s.width, background: s.over ? 'var(--color-danger)' : chartPalette[s.i % chartPalette.length] }} />);
  const planText = [how === '매달 꾸준히' ? `매달 ${Number(contribution).toLocaleString('ko-KR', { maximumFractionDigits: 4 })}만 원` : monthlyOff.includes(how) ? '매달 넣지 않음' : '', horizonLabel[horizon], `계좌 ${usableAccounts(selected, held, tax).length}개`].filter(Boolean);
  const chipText = chips.map(c => c.kind === 'max_positions' ? `종목 ${c.value ?? 'n'}개 이하` : chipLabels.find(([kind]) => kind === c.kind)?.[1] ?? c.kind);
  const pct = (w: number) => `${Math.round(w)}%`;
  const pickRow = (item: Instrument, note?: string) => <li className="fl-pick" key={item.id}><Avatar holding={{ ...item, instrument_id: item.id }} />
    <span className="fl-pick__text"><b>{item.name}{item.kind === 'stock' && <span className="fl-badge">주식</span>}</b><small>{note ?? itemLabel(item)}</small></span><Button className="fl-add" aria-label={item.name + ' 담기'} onClick={() => pick(item)}><Glyph name="plus" size={16} />담기</Button></li>;
  const accountName = sheet?.item ? '' : sheet?.from ?? '';
  return <div className="fl" data-surface="canvas">
    <div className="fl-top"><Button className="fl-back" aria-label={step ? '앞 단계로' : '처음 화면으로'} disabled={(!step && (!onExit || storageError)) || saving || busy} onClick={() => step ? setStep(step - 1) : onExit?.()}><Glyph name="left" size={26} /></Button>
      <div className="fl-progress" role="progressbar" aria-valuemin={1} aria-valuenow={step + 1} aria-valuemax={3} aria-label={`3단계 중 ${step + 1}단계 ${steps[step]}`}>{steps.map((s, i) => <span key={s} data-on={i <= step} data-current={i === step}><i /><span>{s}</span></span>)}</div></div>
    <section inert={saving || busy} ref={heading} tabIndex={-1} aria-label={`${step + 1}단계 ${steps[step]}`} className="fl-step" key={step}>
      {storageError && <p className="fl-error" role="alert">이 브라우저에서는 입력을 임시 보관할 수 없어요. 완료할 때까지 이 화면을 유지해 주세요.</p>}
      {initial && step === 0 && <div className="fl-resume"><span>입력하던 내용을 이어서 작성해요.</span><Button className="fl-textlink" onClick={async () => { if (await ask('입력한 내용을 지우고 새로 시작할까요?', '새로 시작하기')) { try { sessionStorage.removeItem(inputDraftKey); sessionStorage.removeItem(legacyDraftKey); sessionStorage.removeItem(pendingKey); location.reload(); } catch { setInputError('임시 입력을 지우지 못했어요. 이 브라우저의 저장 설정을 확인해 주세요.'); restoreFocus(); } } }}>새로 작성</Button></div>}
      {inputError && <p className="fl-error" role="alert">{inputError}</p>}
      {step === 0 && <>
        <div className="fl-title"><h1>지금 갖고 있는 종목을<br />넣어 주세요</h1><p>{mode === 'percent' ? '비중과 전체 금액을 적으면 사고팔 금액까지 계산해 드려요.' : '증권사 앱의 평가금액 그대로 적어 주세요. ETF, 주식, 현금 모두 돼요.'}</p></div>
        {mode === 'percent' && <section className="fl-card"><label className="fl-card__title" htmlFor="total">전체 금액 <span className="fl-required">· 필수</span></label>
          <span className="fl-total" data-invalid={!totalOk}><TextInput id="total" className="fl-bare" inputMode="decimal" autoComplete="off" placeholder="예: 1,250" aria-describedby="total-help" aria-invalid={!totalOk} value={total.replace(/^\d+/, n => Number(n).toLocaleString('ko-KR'))}
            onChange={e => setTotal(manInput(e.target.value))} /><span>만 원</span></span>
          <p className={totalOk ? 'fl-card__desc' : 'fl-error'} id="total-help">{totalOk ? '대략이어도 괜찮아요.' : Number(total) * 1e4 > 1e13 ? '전체 금액은 10조 원(10억 만 원) 이하로 적어 주세요.' : '비중으로 넣으면 전체 금액이 꼭 필요해요. 대략이어도 괜찮아요.'}</p></section>}
        <section className="fl-card">
          <label className="fl-search"><Glyph name="search" size={22} /><TextInput ref={searchBox} className="fl-bare" type="search" aria-label="종목 이름이나 코드로 찾기" placeholder="종목 이름이나 코드로 찾기" autoComplete="off" enterKeyHint="search" value={query}
            onFocus={openedSearch} onChange={e => { openedSearch(); setQuery(e.target.value); setSearchError(''); }} onKeyDown={e => { if (e.key === 'Enter' && options[0]) { e.preventDefault(); pick(options[0]); } if (e.key === 'Escape') setQuery(''); }} />
            {query && <Button className="fl-clear" aria-label="찾는 글 지우기" onClick={() => { setQuery(''); searchBox.current?.focus(); }}><Glyph name="x" size={18} /></Button>}</label>
          {searchError && <p className="fl-error" role="alert">{searchError}</p>}
          {query.trim() ? <>
            {!!options.length && <ul className="fl-picks" aria-label="찾은 종목">{options.map(item => pickRow(item))}</ul>}
            {empty && <div className="fl-empty"><b>맞는 종목이 없어요</b><p>종목 코드(예: 360750)나 티커(예: VOO)로도 찾을 수 있어요</p></div>}
            {!catalog && !catalogError && <p className="fl-card__desc" role="status">종목 목록을 불러오고 있어요. 현금은 먼저 담을 수 있어요.</p>}
          </> : !holdings.length && !catalogError && <><p className="fl-sublabel">많이 담는 종목</p><ul className="fl-picks">{popular.map(item => pickRow(item, item.note))}</ul></>}
        </section>
        {query.trim() && options.some(o => o.kind === 'stock') && <p className="fl-info"><Glyph name="info" />개별 주식도 함께 분석해요. 새로 담을 종목은 ETF로만 추천하고, 갖고 있는 주식은 그대로 두기·줄이기·팔기 중에서 골라 드려요.</p>}
        {catalogError && <><div className="fl-warn" role="alert"><Glyph name="alert" /><div><b>종목 목록을 불러오지 못했어요</b><p>지금까지 넣은 건 그대로 있어요.</p><Button className="fl-textlink" onClick={() => setCatalogAttempt(n => n + 1)}>다시 불러오기 <Glyph name="refresh" size={16} /></Button></div></div>
          <section className="fl-card"><h2 className="fl-card__title">바로 담기</h2><ul className="fl-picks">{popular.map(item => pickRow(item, '목록 없이도 바로 담을 수 있어요'))}</ul></section></>}
        {!!holdings.length && <section className="fl-card fl-basket" aria-label="담은 종목">
          <div className="fl-basket__head"><div><span className="fl-basket__count">담은 종목 {holdings.length}개</span>
            <strong className="fl-basket__total" data-over={mode === 'percent' && percentHundredths > 10000}>{mode === 'percent' ? <><em>{percentSum}</em>%</> : amountSum >= 1e4 ? <><em>{man(amountSum)}</em> 원</> : <><em>{amountSum.toLocaleString('ko-KR')}</em>원</>}</strong></div>
            <div className="fl-mode" role="group" aria-label="숫자 적는 방법">{(['amount', 'percent'] as const).map(m => <Button key={m} aria-pressed={mode === m} onClick={() => changeMode(m)}>{m === 'amount' ? '금액' : '비중'}</Button>)}</div></div>
          {mode === 'percent' && <p className="fl-badge" data-tone={percentHundredths === 10000 ? 'success' : 'danger'} role="status">{percentHundredths === 10000 ? <><Glyph name="check" size={14} />합계 100%</> : percentHundredths > 10000 ? <><Glyph name="alert" size={14} />100%를 {((percentHundredths - 10000) / 100).toLocaleString('ko-KR')}%p 넘었어요</> : `${((10000 - percentHundredths) / 100).toLocaleString('ko-KR')}% 남았어요`}</p>}
          <div className="fl-allocation" role="img" aria-label={'비중 막대: ' + holdings.map((h, i) => `${h.name} ${pct(weights[i])}`).join(', ')}>{barSegments}</div>
          <ul className="fl-legend" aria-hidden="true">{segments.slice(0, 6).map(s => <li key={s.i}><i style={{ background: s.over ? 'var(--color-danger)' : chartPalette[s.i % chartPalette.length] }} />{s.over ? '넘침' : holdings[s.i].name} <b>{pct(s.over ? s.width : weights[s.i])}</b></li>)}{segments.length > 6 && <li>외 {segments.length - 6}개</li>}</ul>
          {groups.map(account => <div className="fl-group" key={account}><div className="fl-group__head"><span className="fl-badge">{account}</span><Button className="fl-textlink" aria-label={account + ' 계좌 바꾸기'} onClick={() => openSheet({ from: account })}>계좌 바꾸기</Button></div>
            <ul className="fl-holds">{holdings.map((h, i) => {
              if (h.account !== account) return null;
              const v = h.amount_krw || '', state = mode === 'percent' && v !== '' && !amountOk(v) ? Number(v) === 0 ? 'warning' : 'error' : mode === 'amount' && Number(v) > 1e13 ? 'error' : undefined;
              const message = state === 'warning' ? '0%면 분석에서 빠져요. 비중을 적거나 이 종목을 지워 주세요.' : state && mode === 'percent' ? '0.01%에서 100% 사이로, 소수 둘째 자리까지 적어 주세요.' : state ? '금액은 10조 원 이하로 적어 주세요.' : '';
              return <li className="fl-hold" key={h.account + h.instrument_id}><div className="fl-hold__row"><Avatar holding={h} color={chartPalette[i % chartPalette.length]} /><span className="fl-hold__name">{h.name}{h.kind === 'stock' && <span className="fl-badge">주식</span>}</span>
                <label className="fl-amount" data-state={state}>{mode === 'amount'
                  ? <TextInput className="fl-bare" aria-label={h.name + ' 금액'} aria-invalid={!!state || undefined} data-holding={h.account + '|' + h.instrument_id} inputMode="numeric" autoComplete="off" placeholder="0" value={h.amount_krw ? Number(h.amount_krw).toLocaleString('ko-KR') : ''} onKeyDown={nextSearch}
                    // 원에는 소수가 없다: 끝의 '.xx'(붙여 넣은 전)는 버리고, 나머지 점은 자릿수 구분으로 본다.
                    onChange={e => setHoldings(holdings.map((x, n) => n === i ? { ...x, amount_krw: e.target.value.replace(/[^\d.]/g, '').replace(/\.\d{0,2}$/, '').replace(/\D/g, '').replace(/^0+/, '') } : x))} />
                  : <TextInput className="fl-bare" aria-label={h.name + ' 비중'} aria-invalid={!!state || undefined} data-holding={h.account + '|' + h.instrument_id} inputMode="decimal" type="number" min={0.01} max={100} step={0.01} placeholder="0" value={h.amount_krw || ''} onKeyDown={nextSearch} onChange={e => setHoldings(holdings.map((x, n) => n === i ? { ...x, amount_krw: e.target.value } : x))} />}
                  <span>{mode === 'amount' ? '원' : '%'}</span></label>
                <Button className="fl-x" aria-label={h.name + ' 빼기'} onClick={() => { setHoldings(holdings.filter((_, n) => n !== i)); setAlternate(alternate.filter(a => a.account !== h.account || a.instrument_id !== h.instrument_id)); }}><Glyph name="x" size={16} /></Button></div>
                {message ? <p className="fl-hold__msg" data-state={state}>{message}</p> : mode === 'amount' && amountOk(h.amount_krw) && <span className="fl-hold__pct">{pct(weights[i])}</span>}</li>;
            })}</ul></div>)}
          <Button className="fl-more" onClick={() => { reveal(searchBox.current); searchBox.current?.scrollIntoView({ block: 'center' }); }}><Glyph name="plus" size={18} />종목 더 담기</Button>
        </section>}
        {!holdings.length && <section className="fl-card fl-rows">
          {hasSaved && <Button className="fl-row" disabled={saving} onClick={() => void reuse()}><span className="fl-icontile"><Glyph name="wallet" /></span><span><b>저장된 보유 종목 불러오기</b><small>지난번에 넣은 종목 그대로</small></span><Glyph name="right" /></Button>}
          <details className="fl-bulk" onToggle={e => { if (e.currentTarget.open) trackAction('bulk_entry_opened'); }}><summary className="fl-row"><span className="fl-icontile"><Glyph name="layers" /></span><span><b>여러 종목 한 번에 넣기</b><small>증권사 화면을 붙여 넣어요</small></span><Glyph name="right" /></summary>
            <div className="fl-bulk__body"><div className="fl-chips" role="group" aria-label="담을 계좌">{accounts.map(a => <Button key={a} className="fl-chip" aria-pressed={bulkAccount === a} onClick={() => setBulkAccount(a)}>{a}</Button>)}</div>
              <label className="fl-field">{bulkAccount} · 종목 코드와 {mode === 'amount' ? '금액(원)' : '비중(%)'}<TextArea className="fl-textarea" value={bulk} onChange={e => setBulk(e.target.value)} placeholder={mode === 'amount' ? '360750 5,000,000\n379810 3,000,000' : '360750 50\n379810 50'} /></label>
              <Button variant="secondary" disabled={!catalog || !bulk.trim()} onClick={() => {
                try {
                  const rows = bulkHoldings(bulk, catalog!, bulkAccount, mode);
                  if (rows.length + holdings.length > 20) throw new Error('최대 20종목까지 담을 수 있어요.');
                  if (rows.some(row => holdings.some(h => h.account === bulkAccount && h.instrument_id === row.instrument_id))) throw new Error('이미 담은 종목이 있어요. 기존 금액이나 비중을 수정해 주세요.');
                  setHoldings([...holdings, ...rows]); setBulk(''); setInputError('');
                  if (!selected.includes(bulkAccount)) setSelected([...selected, bulkAccount]);
                  trackAction('bulk_entry_added');
                } catch (error) { setInputError((error as Error).message); trackAction('bulk_entry_failed'); }
              }}>목록에 추가</Button></div></details>
        </section>}
        {!holdings.length && <p className="fl-foot">앞으로 사고 싶은 종목은 3단계에서 글로 적어 주세요.</p>}
      </>}
      {step === 1 && <>
        <div className="fl-title">{fresh && !holdings.length && <span className="fl-badge" data-tone="primary">처음 시작해요</span>}<h1>앞으로의 계획을<br />알려 주세요</h1></div>
        <PlanInput how={how} setHow={setHow} contribution={contribution} setContribution={setContribution} horizon={horizon} setHorizon={setHorizon} selected={selected} setSelected={setSelected} held={held} tax={tax} setTax={setTax} starting={fresh && !holdings.length} />
      </>}
      {step === 2 && <>
        <div className="fl-title"><h1>거의 다 왔어요</h1><p>적어 주신 질문에 먼저 답하고, 바꿀 것과 금액을 알려 드려요.</p></div>
        <section className="fl-card"><div className="fl-card__head"><label className="fl-card__title" htmlFor="ask">무엇이 가장 궁금하세요?</label><span className="fl-optional">선택</span></div>
          <TextArea className="fl-textarea" id="ask" maxLength={1000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="예: 3년 뒤 결혼 자금으로 써도 괜찮을까요?" />
          <p className="fl-note"><Glyph name="message" size={16} />결과 맨 위에서 이 질문에 먼저 답해 드려요</p>
          <p className="fl-sublabel">다른 분들은 이렇게 물었어요</p>
          <div className="fl-list">{asks.map(a => <Button key={a} className="fl-suggest" aria-pressed={question === a} onClick={() => setQuestion(a)}><Glyph name="plus" size={16} />{a}</Button>)}</div></section>
        <section className="fl-card"><div className="fl-card__head"><label className="fl-card__title" htmlFor="goal">원하는 포트폴리오</label><span className="fl-optional">선택</span></div>
          <TextArea className="fl-textarea" id="goal" maxLength={1000} value={note} onChange={e => setNote(e.target.value)} placeholder="예: S&P500 60%, 나스닥100 30%로 가고 싶어요" />
          <p className="fl-card__desc">참고해서 견줘 볼게요. 꼭 지킬 것은 아래 조건에서 골라 주세요.</p>
          {initial?.dividendGoal && <label className="fl-field">기존 배당 목표<TextInput className="fl-textarea" value={dividendGoal} maxLength={100} onChange={e => setDividendGoal(e.target.value)} /></label>}</section>
        <section className="fl-card" aria-labelledby="chips-title"><div className="fl-card__head"><h2 className="fl-card__title" id="chips-title">꼭 지켜야 할 조건</h2><span className="fl-optional">선택</span></div>
          <p className="fl-card__desc">여기서 고른 조건만 꼭 지켜요. 위에 적은 글은 참고만 해요.</p>
          <div className="fl-chips" data-align="start" role="group" aria-labelledby="chips-title">{chipLabels.filter(([kind]) => kind !== 'no_sell' || holdings.length).map(([kind, label]) => {
            const on = chips.some(c => c.kind === kind), toggle = () => setChips(on ? chips.filter(c => c.kind !== kind) : [...chips, { kind }]);
            return kind === 'max_positions' ? <span key={kind} className="fl-chip fl-chip--count" data-on={on}><Button aria-pressed={on} aria-label={`종목 ${count || 'n'}개 이하`} onClick={toggle}>{on && <Glyph name="check" size={16} />}종목</Button>
              <TextInput className="fl-bare" aria-label="최대 종목 수" inputMode="numeric" placeholder="n" value={count} onChange={e => { const digits = e.target.value.replace(/\D/g, '').slice(0, 3); /* 세 자리(100)는 잘라 맞추지 않고 1~20 검사로 고치게 한다 */ setChips([...chips.filter(c => c.kind !== kind), { kind, value: digits ? Number(digits) : undefined }]); }} /><span>개 이하</span></span>
              : <Button key={kind} className="fl-chip" aria-pressed={on} onClick={toggle}>{on && <Glyph name="check" size={16} />}{label}</Button>;
          })}</div>
          {chips.some(c => c.kind === 'kr_listed_etf_only') && <p className="fl-card__desc">국내 상장 ETF만: 미국 ETF도 국내에 상장된 것이면 돼요.</p>}</section>
      </>}
    </section>
    {confirmation}
    <dialog ref={sheetRef} className="fl-sheet" aria-labelledby="sheet-title" onClose={() => setSheet(null)} onClick={e => { if (e.target === e.currentTarget) sheetRef.current?.close(); }}>
      {sheet && <>
        <span className="fl-sheet__grab" aria-hidden="true" /><Button className="fl-sheet__close" aria-label="계좌 고르기 닫기" onClick={() => sheetRef.current?.close()}><Glyph name="x" size={20} /></Button>
        <h2 id="sheet-title">{sheet.item ? '어느 계좌에 있나요?' : `${accountName} 종목을 어느 계좌로 옮길까요?`}</h2>
        {sheet.item && <p className="fl-sheet__item"><Avatar holding={{ ...sheet.item, instrument_id: sheet.item.id }} />{sheet.item.name} · {itemLabel(sheet.item).split(' · ')[0]}</p>}
        <div className="fl-tiles" role="group" aria-label="계좌">{accounts.map(account => { const why = blockReason(account); return <Button key={account} className="fl-tile" aria-pressed={choice === account} disabled={!!why} aria-describedby={why ? 'sheet-why-' + account.replace(' ', '') : undefined} onClick={() => setChoice(account)}>
          <b>{account}</b><span>{sheetBenefit[account]}</span>{why && <em id={'sheet-why-' + account.replace(' ', '')}>{why}</em>}<i className="fl-check" aria-hidden="true">{choice === account && <Glyph name="check" size={16} />}</i></Button>; })}</div>
        <Button ref={sheetGo} variant="primary" className="fl-go" disabled={!!blockReason(choice)} onClick={confirmSheet}>{sheet.item ? '이 계좌에 담기' : '이 계좌로 옮기기'}</Button>
      </>}
    </dialog>
    <div ref={bar} className="fl-cta">
      {/* 입력 요약: 넓은 화면(flow.css)에서만 보인다 — 좁은 화면은 단계마다 한 화면이라 따로 두지 않는다. */}
      <section className="fl-recap" aria-labelledby="recap-title"><h2 className="fl-card__title" id="recap-title">입력 요약</h2>
        <dl>
          <div data-current={step === 0}><dt>{steps[0]}</dt><dd>{holdings.length ? <><b>종목 {holdings.length}개{mode === 'percent' ? totalOk && ` · ${wonText(Number(total) * 1e4)}` : ` · ${wonText(amountSum)}`}</b>
            <div className="fl-allocation" aria-hidden="true">{barSegments}</div><small>{groups.join(' · ')}</small></> : <small>{fresh ? '가진 종목 없이 시작해요' : '아직 담지 않았어요'}</small>}</dd></div>
          <div data-current={step === 1}><dt>{steps[1]}</dt><dd>{plan ? <small>아직 정하지 않았어요</small> : <p className="fl-recap__tags">{planText.map(s => <span key={s} className="fl-badge">{s}</span>)}</p>}</dd></div>
          <div data-current={step === 2}><dt>{steps[2]}</dt><dd>{question.trim() ? <q>{question.trim()}</q> : <small>아직 적지 않았어요</small>}
            {!!chipText.length && <p className="fl-recap__tags">{chipText.map(s => <span key={s} className="fl-badge">{s}</span>)}</p>}</dd></div>
        </dl></section>
      {step === 1 && !plan && <p className="fl-summary">{planText.map(s => <span key={s} className="fl-badge">{s}</span>)}</p>}
      {step === 2 && <Quota> · 보통 5분쯤 걸려요</Quota>}
      <Button variant="primary" className="fl-go" loading={busy || saving} aria-describedby={guidance ? 'step-guidance' : undefined} disabled={!valid || busy || saving} onClick={() => void advance()}>{busy ? '분석 접수 중…' : saving ? '저장 중…' : step === 2 ? '분석 시작하기' : '다음'}</Button>
      <p className="fl-cta__hint" id="step-guidance" aria-live="polite" hidden={!guidance}>{guidance}</p>
    </div>
  </div>;
}
