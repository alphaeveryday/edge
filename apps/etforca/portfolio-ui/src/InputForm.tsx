import { Button, TextInput, TextArea } from './design-system/components';
import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { request, ensureVisitorSession, type Catalog, type Instrument, type Holding, type Input } from './api';
import { InstrumentSearch } from './InstrumentSearch';
import { PortfolioPreview } from './PortfolioPreview';
import { TaxAccounts, type TaxProfile } from './TaxAccounts';
import { trackAction, trackScreen } from './analytics';

import { inputDraftKey, legacyDraftKey, readInputDraft, inputDraftFromInput } from './inputDraft';
import { useConfirmation } from './design-system/confirmation';
import { bulkHoldings, toPercents } from './bulkHoldings';
import type { Saved } from './workbook';
import { pendingKey } from './preview';

const accounts = ['일반 계좌', 'ISA', '연금저축', 'IRP'];
const hints = ['월 적립 계획까지 추천', '배당 얼마를 원해', '모은 돈이 있어', '목돈 넣는 방법', '대출이 있어', '레버리지는 빼줘', '개수는 적게'];
export function InputForm({ onSubmit, busy, initialInput }: { onSubmit: (input: Input) => void; busy: boolean; initialInput?: Input | null }) {
  const [initial] = useState(() => initialInput ? inputDraftFromInput(initialInput) : readInputDraft());
  const { ask, confirmation, restoreFocus } = useConfirmation();
  const heading = useRef<HTMLElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = bar.current, root = document.documentElement;
    if (!element) return;
    const reserve = () => root.style.setProperty('--input-cta-height', `${element.getBoundingClientRect().height + 24}px`);
    reserve();
    const observer = new ResizeObserver(reserve);
    observer.observe(element);
    return () => { observer.disconnect(); root.style.removeProperty('--input-cta-height'); };
  }, []);
  const [step, setStep] = useState(initial?.step ?? 0);
  useEffect(() => trackScreen((['input_holdings', 'input_funding', 'input_horizon', 'input_interests', 'input_question'] as const)[step]), [step]);
  const [holdings, setHoldings] = useState<Holding[]>(initial?.holdings ?? []);
  // After a search pick, move straight to that row's amount so each holding is pick → type → Enter.
  const focusRow = useRef<string | null>(null);
  useEffect(() => {
    if (!focusRow.current) return;
    reveal(document.querySelector<HTMLInputElement>(`[data-holding="${CSS.escape(focusRow.current)}"]`));
    focusRow.current = null;
  }, [holdings]);
  const [selected, setSelected] = useState(initial?.selected ?? ['일반 계좌']);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [catalogError, setCatalogError] = useState('');
  useEffect(() => { let stopped = false; setCatalogError(''); request<Catalog>('/catalog').then(data => { if (!stopped) setCatalog(data); }).catch(() => { if (!stopped) setCatalogError('ETF 목록을 불러오지 못했어요. 입력은 유지돼요.'); }); return () => { stopped = true; }; }, [catalogAttempt]);
  const [question, setQuestion] = useState(initial?.question ?? '');
  const [horizon, setHorizon] = useState(initial?.horizon ?? '');
  const [how, setHow] = useState(initial?.how ?? '');
  const [interests, setInterests] = useState<string[]>(initial?.interests ?? []);
  const [note, setNote] = useState(initial?.note ?? '');
  const [dividendGoal, setDividendGoal] = useState(initial?.dividendGoal ?? '');
  const [contribution, setContribution] = useState(initial?.contribution ?? '');
  const contributionRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === 1 && how === '매달 꾸준히') contributionRef.current?.scrollIntoView({ block: 'center', behavior: 'instant' });
  }, [step, how]);
  const [keyword, setKeyword] = useState(initial?.keyword ?? '');
  const [tax, setTax] = useState<TaxProfile | null>(initial?.tax ?? null);
  const [saving, setSaving] = useState(false);
  const [draftId] = useState(() => initial?.draftId ?? crypto.randomUUID());
  const [mode, setMode] = useState<'amount' | 'percent'>(initial?.mode ?? 'amount');
  const [fresh, setFresh] = useState(initial?.fresh ?? false);
  const [alternate, setAlternate] = useState<Holding[]>(initial?.alternate ?? []);
  const [storageError, setStorageError] = useState(false);
  useEffect(() => {
    try { sessionStorage.setItem(inputDraftKey, JSON.stringify({ step, holdings, alternate, selected, question, horizon, how, interests, note, dividendGoal, contribution, keyword, tax, draftId, mode, fresh }));
      // Any v2 write supersedes a leftover v1 draft, so clearing v2 later cannot bring the old one back.
      sessionStorage.removeItem(legacyDraftKey); setStorageError(false); }
    catch { setStorageError(true); }
  }, [step, holdings, alternate, selected, question, horizon, how, interests, note, dividendGoal, contribution, keyword, tax, draftId, mode, fresh]);
  const previousStep = useRef(step);
  useEffect(() => { if (step !== previousStep.current) { heading.current?.focus({ preventScroll: true }); window.scrollTo(0, 0); previousStep.current = step; } }, [step]);
  const [inputError, setInputError] = useState('');
  // 불러올 보유가 있는 방문자에게만 '불러오기'를 보인다 — 처음 온 방문자에게는 검색창을 첫 화면 밖으로 미는 빈 버튼이다.
  const [hasSaved, setHasSaved] = useState(false);
  useEffect(() => {
    let stopped = false;
    void ensureVisitorSession().then(() => request<Saved>('/workbook'))
      .then(saved => { if (!stopped) setHasSaved(!!saved.workbook?.assets?.length); })
      .catch(() => { /* 확인하지 못하면 버튼만 숨긴다 — 입력은 그대로 할 수 있다. */ });
    return () => { stopped = true; };
  }, []);
  const searchOpened = useRef(false);
  const revealing = useRef(false);
  // 포커스를 옮긴 칸이 하단 고정 버튼·화면 밖·모바일 키보드 뒤에 숨지 않게 직접 맞춘다. focus() 는 반쯤 보이는 칸을 다시
  // 스크롤하지 않고, 안내 문구가 막 생겨 버튼이 커진 순간에는 높이 변수(--input-cta-height)가 한 프레임 늦다 — 그래서 그때 잰다.
  function reveal(element: HTMLElement | null | undefined) {
    if (!element) return;
    revealing.current = true; // 코드가 옮긴 포커스는 방문자가 검색을 연 것이 아니다
    element.focus({ preventScroll: true });
    revealing.current = false;
    const view = window.visualViewport, viewBottom = view ? view.offsetTop + view.height : innerHeight;
    const box = element.getBoundingClientRect(), limit = Math.min(bar.current?.getBoundingClientRect().top ?? viewBottom, viewBottom) - 8;
    if (box.bottom > limit) window.scrollBy(0, box.bottom - limit);
    else if (box.top < 8) window.scrollBy(0, box.top - 8);
  }
  function openedSearch() {
    if (searchOpened.current || revealing.current) return;
    searchOpened.current = true;
    trackAction('input_search_opened');
  }
  const [bulk, setBulk] = useState('');
  const invalidHolding = holdings.some(h => !(mode === 'amount' ? /^\d+$/ : /^(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/).test(h.amount_krw || '') || !(Number(h.amount_krw) > 0) || Number(h.amount_krw) > (mode === 'percent' ? 100 : 1e13));
  const percentHundredths = holdings.reduce((n, h) => n + Math.round(Number(h.amount_krw) * 100), 0);
  const percentSum = (percentHundredths / 100).toLocaleString('ko-KR', { maximumFractionDigits: 2 });
  const percentLeft = ((10000 - percentHundredths) / 100).toLocaleString('ko-KR', { maximumFractionDigits: 2 });
  const holdingsValid = holdings.length > 0 && !invalidHolding && (mode !== 'percent' || percentHundredths === 10000);
  function add(account: string, item: Instrument) {
    if (item.kind === 'stock') return '개별 주식은 대시보드에서 관리할 수 있어요. AI 분석에는 ETF를 담아 주세요.';
    if (holdings.some(h => h.account === account && h.instrument_id === item.id)) return '이미 담은 종목이에요. 보유 금액이나 비율을 수정해 주세요.';
    if (holdings.length >= 20) return '최대 20종목까지 담을 수 있어요. 기존 종목을 빼고 추가해 주세요.';
    focusRow.current = account + '|' + item.id;
    setHoldings([...holdings, { name: item.name, account, amount_krw: '', kind: item.kind, instrument_id: item.id }]);
    trackAction('input_holding_added');
  }
  // Enter on an amount returns to the account search, so the next holding needs no pointer.
  // Won has no decimals: a trailing '.xx' (pasted cents) is dropped instead of shifting the amount 100x; other dots are separators.
  // ponytail: comma formatting re-renders the amount, so mid-number edits jump the caret to the end and Backspace on a comma is a no-op; track selection if visitors edit mid-number often.
  function nextSearch(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    reveal(e.currentTarget.closest('.acct')?.querySelector<HTMLInputElement>('[role=combobox]'));
  }
  function changeMode(next: 'amount' | 'percent') {
    if (next === mode) return;
    const kept = holdings.map(h => alternate.find(a => a.account === h.account && a.instrument_id === h.instrument_id)?.amount_krw ?? '');
    // Amounts fully determine the weights, so converting beats asking the visitor to retype them.
    const converted = next === 'percent' && !invalidHolding ? toPercents(holdings.map(h => Number(h.amount_krw))) : kept;
    setAlternate(holdings); setHoldings(holdings.map((h, i) => ({ ...h, amount_krw: converted[i] }))); setMode(next);
  }
  function payload(profile = tax, planning = fresh): Input {
    return { holdings: holdings.map(h => ({ ...h, amount_krw: !Number(h.amount_krw) ? null : String(mode === 'amount' ? Math.round(Number(h.amount_krw)) : Number(h.amount_krw)) })), horizon, question, input_mode: mode, planning,
      preferences: { accounts: selected, contribution: how + (contribution && how === '매달 꾸준히' ? ` ${contribution}만 원` : ''), ...(initial?.dividendGoal ? { dividend_goal: dividendGoal } : {}), interests, note, tax_profile: profile ? { ...profile, accounts: profile.accounts.filter(a => selected.includes(a.account)) } : null } };
  }
  async function save(profile = tax, planning = fresh) {
    await ensureVisitorSession();
    await request('/drafts/' + draftId, { method: 'POST', body: JSON.stringify(payload(profile, planning)) });
  }
  async function reuse() {
    setSaving(true); setInputError('');
    try {
      await ensureVisitorSession();
      const saved = await request<Saved>('/workbook');
      const assets = saved.workbook?.assets || [];
      const etfs = catalog ?? await request<Catalog>('/catalog');
      if (!assets.length) throw new Error('저장된 보유 종목이 없어요.');
      if (assets.length > 20) throw new Error('분석은 최대 20종목까지 가능해요. 분석할 종목을 입력해 주세요.');
      const rows: Holding[] = assets.map(asset => {
        const instrumentId = asset.instrument_id || (asset.category === '현금' ? 'CASH:KRW' : null);
        if (!instrumentId) throw new Error(`${asset.name}의 종목 코드를 확인할 수 없어요. 대시보드에서 종목을 다시 선택해 주세요.`);
        if (instrumentId !== 'CASH:KRW' && !etfs.instruments.some(item => item.id === instrumentId && item.kind === 'etf')) throw new Error(`${asset.name}은 ETF 분석 대상이 아니에요. 분석할 ETF를 직접 담아 주세요. 대시보드의 보유 종목은 그대로 유지돼요.`);
        const market = saved.tracking?.market?.version === saved.version ? saved.tracking.market.payload : null;
        const missing = market?.missing.includes(asset.id) || market?.pending_prices?.includes(asset.id);
        const quote = market?.assets.find(row => row.id === asset.id);
        const amount = instrumentId === 'CASH:KRW' ? asset.amount : missing ? null : quote?.value;
        return { name: asset.name, instrument_id: instrumentId, account: asset.account,
          kind: asset.category === '현금' ? 'cash' : 'etf', amount_krw: amount == null ? '' : String(Math.round(amount)) };
      });
      if (!await ask('현재 입력을 저장된 보유 종목으로 바꿀까요?', '불러오기')) return;
      try { sessionStorage.removeItem(pendingKey); } catch { /* Import remains available in memory without browser storage. */ }
      setHoldings(rows); setSelected([...new Set(rows.map(row => row.account))]); setAlternate([]); setMode('amount'); setFresh(false);
    } catch (error) { setInputError((error as Error).message); }
    finally { setSaving(false); restoreFocus(); }
  }
  async function advance(recommend = false, finish = false) {
    if (saving) return;
    if (holdings.length && !holdingsValid) { setInputError('보유 종목의 금액 또는 비율을 입력해 주세요. 비율의 합계는 100%여야 해요.'); setStep(0); return; }
    setSaving(true); setInputError('');
    try { await save(tax, recommend || fresh); } catch (error) { setInputError((error as Error).message); setSaving(false); return; }
    setSaving(false);
    if (recommend) setFresh(true);
    if (step < 4 && !finish) { setStep(step + 1); window.scrollTo(0, 0); return; }
    if (!how || how === '매달 꾸준히' && Number(contribution) <= 0) { setInputError('투자 방법과 월 투자금액을 입력해 주세요.'); setStep(1); return; }
    onSubmit({ ...payload(), draft_id: draftId });
  }
  const valid = step === 0 ? holdingsValid || fresh && !holdings.length : step === 1 ? !!how && (how !== '매달 꾸준히' || Number(contribution) > 0) : step === 2 ? !!horizon : true;
  // 종목이 없을 때 안내는 본문 첫 줄이 맡는다 — 하단 고정 영역이 두 줄로 커져 검색창을 가리지 않게 비운다.
  const guidance = !valid ? step === 0 ? !holdings.length ? '' : holdings.some(h => !(Number(h.amount_krw) > 0)) ? mode === 'percent' ? '각 종목의 비중을 입력해 주세요.' : '보유 종목의 금액을 입력해 주세요.' : invalidHolding ? mode === 'percent' ? '비중은 100% 이하, 소수 둘째 자리까지 적어 주세요.' : '금액은 10조 원 이하로 적어 주세요.' : `비중 합계를 100%로 맞춰 주세요. 지금 ${percentSum}%예요.` : step === 1 ? how === '매달 꾸준히' ? '매달 투자할 금액을 입력해 주세요.' : '투자 방법을 골라 주세요.' : '투자 기간을 골라 주세요.' : step === 4 ? '분석에 약 5분이 걸려요' : '';
  const choices = (values: string[], current: string, change: (v: string) => void, labels = values) => <div className="chips big">{values.map((v, i) => <Button className="chip" key={v} aria-pressed={current === v} onClick={() => change(v)}>{labels[i]}</Button>)}</div>;
  return <>
    <div className="nav"><div className="nav__pill"><Button disabled={!step || saving || busy} className={'nav__back' + (!step ? ' off' : '')} aria-label="앞 질문으로" onClick={() => setStep(step - 1)}>‹</Button><span className="nav__logo">ETF Orca</span><div className="prog"><i style={{ width: `${(step + 1) / 5 * 100}%` }} /></div><span className="nav__n" aria-label={`전체 5단계 중 ${step + 1}단계`}>{step + 1} / 5</span></div></div>
    <section inert={saving || busy} ref={heading} tabIndex={-1} aria-label={`${step + 1}단계`} className="step in" key={step}>
      {storageError && <p role="alert">이 브라우저에서는 입력을 임시 보관할 수 없어요. 완료할 때까지 이 화면을 유지해 주세요.</p>}
      {initial && step === 0 && <div className="draft-resume"><span>입력하던 내용을 이어서 작성해요.</span><Button onClick={async () => { if (await ask('입력한 내용을 지우고 새로 시작할까요?', '새로 시작하기')) { try { sessionStorage.removeItem(inputDraftKey); sessionStorage.removeItem(legacyDraftKey); sessionStorage.removeItem(pendingKey); location.reload(); } catch { setInputError('임시 입력을 지우지 못했어요. 이 브라우저의 저장 설정을 확인해 주세요.'); restoreFocus(); } } }}>새로 작성</Button></div>}
      {inputError && <p role="alert">{inputError}</p>}
      {step === 0 && <><span className="eyebrow holdings-eyebrow">내 구성 확인</span><h2>내 포트폴리오를<br />더 좋게 바꿔볼까요?</h2><p className="hint">보유 ETF를 검색해 평가금액을 적어 주세요.</p>
        {hasSaved && <Button className="reuse-link" disabled={saving} onClick={() => void reuse()}>저장된 보유 종목 불러오기</Button>}
        <div className="holdings-layout"><div className="holdings-input">
        <>
          {!!holdings.length && <div className="unit"><div className="unit__row"><div className="switch" data-u={mode === 'percent' ? 'pct' : 'won'} aria-label="숫자 적는 방법">{(['amount', 'percent'] as const).map((m, i) => <Button key={m} aria-pressed={mode === m} onClick={() => changeMode(m)}>{i ? '비율' : '금액'}</Button>)}</div></div><p className="unit__note" data-mode={mode}>{mode === 'percent' ? `전체 계좌 합계 ${percentSum}% · ${percentHundredths === 10000 ? '100%를 맞췄어요' : percentHundredths < 10000 ? `${percentLeft}% 남았어요` : `${percentLeft.replace('-', '')}% 넘었어요`}` : '증권사 앱의 평가금액을 원 단위 그대로 적어 주세요.'}</p></div>}
          {selected.map(account => <article className="acct" key={account}><div className="acct__head"><h3 className="acct__name">{account}</h3>{account !== '일반 계좌' && <Button className="x" aria-label={account + ' 지우기'} onClick={async () => { if (holdings.some(h => h.account === account) && !await ask(account + '와 담은 종목을 입력에서 지울까요?')) return; setSelected(selected.filter(a => a !== account)); setHoldings(holdings.filter(h => h.account !== account)); setAlternate(alternate.filter(h => h.account !== account)); restoreFocus(heading.current); }}>×</Button>}</div>
            {!!holdings.filter(h => h.account === account).length && <ul className="rows">{holdings.map((h, i) => h.account === account && <li className="row" key={i}><span className="row__name">{h.name}</span><label className="num won" data-unit={mode}>{mode === 'amount'
                ? <TextInput aria-label={h.name + ' 금액'} data-holding={h.account + '|' + h.instrument_id} inputMode="numeric" autoComplete="off" placeholder="0" value={h.amount_krw ? Number(h.amount_krw).toLocaleString('ko-KR') : ''} onKeyDown={nextSearch} onChange={e => setHoldings(holdings.map((x, n) => n === i ? { ...x, amount_krw: e.target.value.replace(/[^\d.]/g, '').replace(/\.\d{0,2}$/, '').replace(/\D/g, '').replace(/^0+/, '') } : x))} />
                : <TextInput aria-label={h.name + ' 비율'} data-holding={h.account + '|' + h.instrument_id} inputMode="decimal" type="number" min={0.01} max={100} step={0.01} placeholder="0" value={h.amount_krw || ''} onKeyDown={nextSearch} onChange={e => setHoldings(holdings.map((x, n) => n === i ? { ...x, amount_krw: e.target.value } : x))} />}<span>{mode === 'amount' ? '원' : '%'}</span></label><Button className="x" aria-label={h.name + ' 빼기'} onClick={() => { setHoldings(holdings.filter((_, n) => n !== i)); setAlternate(alternate.filter(a => a.account !== h.account || a.instrument_id !== h.instrument_id)); }}>×</Button></li>)}</ul>}
            <InstrumentSearch account={account} catalog={catalog} onAdd={item => add(account, item)} onOpen={openedSearch} />
            <details className="bulk-entry" onToggle={e => { if (e.currentTarget.open) trackAction('bulk_entry_opened'); }}><summary>여러 종목 한 번에 넣기</summary><label>{account} · 종목 코드와 {mode === 'amount' ? '금액(원)' : '비중(%)'}<TextArea className="field" value={bulk} onChange={e => setBulk(e.target.value)} placeholder={mode === 'amount' ? '360750 5,000,000\n379810 3,000,000' : '360750 50\n379810 50'} /></label><Button variant="secondary" disabled={!catalog || !bulk.trim()} onClick={() => {
              try {
                const rows = bulkHoldings(bulk, catalog!, account, mode);
                if (rows.length + holdings.length > 20) throw new Error('최대 20종목까지 담을 수 있어요.');
                if (rows.some(row => holdings.some(h => h.account === account && h.instrument_id === row.instrument_id))) throw new Error('이미 담은 종목이 있어요. 기존 금액이나 비중을 수정해 주세요.');
                setHoldings([...holdings, ...rows]); setBulk(''); setInputError('');
                trackAction('bulk_entry_added');
              } catch (error) { setInputError((error as Error).message); trackAction('bulk_entry_failed'); }
            }}>목록에 추가</Button></details>
          </article>)}
          <div className="q"><TaxAccounts accounts={selected} value={tax} onSave={async profile => { await save(profile); setTax(profile); }} /><div className="chips">{accounts.filter(a => !selected.includes(a)).map(a => <Button className="chip add" key={a} onClick={() => setSelected([...selected, a])}>{a}</Button>)}</div></div>
          <div className="entry"><Button className="rec" disabled={saving} onClick={() => void advance(true)}><b>종목부터 추천받기</b><span>나에게 맞는 ETF부터 찾아드려요</span></Button></div>
          {catalogError && <p role="alert">{catalogError} <Button onClick={() => setCatalogAttempt(n => n + 1)}>목록 다시 불러오기</Button></p>}
        </>
        </div><aside className="holdings-context"><PortfolioPreview holdings={holdings} mode={mode} /><div className="askbox"><label className="lab" htmlFor="ask">요청사항</label><p>원하는 변경이나 궁금한 점을 적어 주세요.</p><TextArea className="field" id="ask" maxLength={1000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="예: 3년 뒤 결혼인데 지금 구성으로 버텨도 되는지, 배당은 언제부터 섞어야 하는지 궁금해요" /><span className="hintlab">이런 것도 요청할 수 있어요</span><div className="chips">{hints.map(h => <Button className="chip add" key={h} onClick={() => setQuestion((question + '\n' + h).trim().slice(0, 1000))}>{h}</Button>)}</div></div>
      </aside></div></>}
      {step === 0 && initial?.dividendGoal && <label>기존 배당 목표<TextInput className="field" value={dividendGoal} maxLength={100} onChange={e => setDividendGoal(e.target.value)} /></label>}
      {step === 1 && <><span className="eyebrow">넣는 방법</span><h2>어떻게 투자할 계획인가요?</h2><div className="chips big">{['이미 다 매수한 상태에요', '매달 꾸준히', '목돈 한 번에', '아직 고민 중'].map(value => <Fragment key={value}><Button className="chip" aria-pressed={how === value} onClick={() => setHow(value)}>{value}</Button>{value === '매달 꾸준히' && how === value && <label className="money"><span>매달</span><TextInput ref={contributionRef} className="field" aria-label="추가 투자금액" inputMode="numeric" maxLength={9} value={contribution} onChange={e => setContribution(e.target.value.replace(/\D/g, ''))} placeholder="0" /><span>만 원</span></label>}</Fragment>)}</div></>}
      {step === 2 && <><span className="eyebrow">쓸 시점</span><h2>언제쯤 쓸 돈인가요?</h2>{choices(['3년 안', '3~10년', '10년 이상', '정한 때 없음'], horizon, setHorizon, ['3년 안', '3~10년 뒤', '10년 넘게 뒤', '정한 때 없어요'])}</>}
      {step === 3 && <><span className="eyebrow">투자 목적 · 선택</span><h2>더 담고 싶은 쪽이 있나요?</h2><p className="hint">원하는 방향이 있으면 골라 주세요. 선택하지 않아도 현재 보유와 요청을 바탕으로 분석해요.</p>{[['투자 성향', '균형 잡힌 분산', '지수 따라가기', '배당', '성장', '안정 (채권·금)'], ['테마', '반도체', 'AI·빅테크', '방산', '로봇', '원자력', '전력 인프라', '바이오·헬스케어', '소비재', '금융', '리츠·부동산', '친환경 에너지', '한국 주식', '미국 주식', '선진국', '신흥국', '단기채', '장기채', '금·원자재']].map(([title, ...values]) => <div className="q" key={title}><span className="lab">{title}</span><div className="chips">{values.map(v => <Button className="chip" key={v} disabled={interests.length >= 10 && !interests.includes(v)} aria-pressed={interests.includes(v)} onClick={() => setInterests(interests.includes(v) ? interests.filter(x => x !== v) : interests.length < 10 ? [...interests, v] : interests)}>{v}</Button>)}</div></div>)}</>}
      {step === 3 && <div className="q"><label className="lab" htmlFor="keyword">관심 키워드 직접 입력</label><div className="keyword-entry"><TextInput className="field" id="keyword" maxLength={50} value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="예: 환율 부담을 줄이고 싶어요" /><Button variant="secondary" className="secondary" disabled={!keyword.trim() || interests.length >= 10} onClick={() => { const v = keyword.trim(); if (v && !interests.includes(v)) setInterests([...interests, v]); setKeyword(''); }}>추가</Button></div><p className="hint sm" role="status">{interests.length}/10개 선택 · {interests.length >= 10 ? '다른 항목을 고르려면 하나를 해제해 주세요.' : '선택하지 않고 넘어가도 괜찮아요.'}</p><div className="chips">{interests.map(v => <Button className="chip" key={v} aria-label={v + ' 선택 취소'} onClick={() => setInterests(interests.filter(x => x !== v))}>{v} ×</Button>)}</div></div>}
      {step === 4 && <><span className="eyebrow">선택</span><h2>추가적으로 고려할<br />것들을 알려주세요</h2><p className="hint">원하는 것을 편하게 적어 주세요. 적은 대로 반영해요.</p><TextArea className="field" aria-label="추가 고려사항" maxLength={1000} value={note} onChange={e => setNote(e.target.value)} placeholder="예: 매달 100만 원씩 넣는 계획까지 추천해줘" /><p className="hint sm">자료 조사와 검토에 약 5분이 걸려요.</p></>}
    </section>
    {confirmation}
    <div ref={bar} className="bar"><div className="bar__in"><div className="bar__msg" id="step-guidance" aria-live="polite" hidden={!guidance}>{guidance}</div>{step >= 2 && step < 4 && <Button variant="secondary" disabled={!valid || busy || saving} onClick={() => void advance(false, true)}>선택 질문 건너뛰고 분석하기</Button>}<Button variant="primary" className="btn" loading={busy || saving} aria-describedby={guidance ? 'step-guidance' : undefined} disabled={!valid || busy || saving} aria-disabled={!valid || busy || saving} onClick={() => void advance()}>{busy ? '분석 접수 중…' : saving ? '저장 중…' : step === 4 ? '내 포트폴리오 분석하기' : '다음'}</Button></div></div>
  </>;
}
