import { Fragment, useEffect, useRef, useState } from 'react';
import { request, ensureVisitorSession, type Catalog, type Instrument, type Holding, type Input } from './api';
import { InstrumentSearch } from './InstrumentSearch';
import { PortfolioPreview } from './PortfolioPreview';
import { TaxAccounts, missingTaxAnswers, type TaxProfile } from './TaxAccounts';

const accounts = ['일반 계좌', 'ISA', '연금저축', 'IRP'];
const hints = ['월 적립 계획까지 추천', '배당 얼마를 원해', '모은 돈이 있어', '목돈 넣는 방법', '대출이 있어', '레버리지는 빼줘', '개수는 적게'];
export function InputForm({ onSubmit, busy }: { onSubmit: (input: Input) => void; busy: boolean }) {
  const [step, setStep] = useState(0);
  const [holdings, setHoldings] = useState<Holding[]>([{ name: '현금', kind: 'cash', instrument_id: 'CASH:KRW', account: '일반 계좌', amount_krw: '100' }]);
  const [selected, setSelected] = useState(['일반 계좌']);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  useEffect(() => { let stopped = false; request<Catalog>('/catalog').then(data => { if (!stopped) setCatalog(data); }).catch(() => { if (!stopped) setCatalogError('ETF 목록을 불러오지 못했어요. 화면을 새로고침해 주세요.'); }); return () => { stopped = true; }; }, []);
  const [question, setQuestion] = useState('');
  const [horizon, setHorizon] = useState('');
  const [how, setHow] = useState('');
  const [interests, setInterests] = useState<string[]>([]);
  const [note, setNote] = useState('');
  const [contribution, setContribution] = useState('');
  const contributionRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === 1 && how === '매달 꾸준히') contributionRef.current?.scrollIntoView({ block: 'center', behavior: 'instant' });
  }, [step, how]);
  const [keyword, setKeyword] = useState('');
  const [taxPending, setTaxPending] = useState(false);
  const [tax, setTax] = useState<TaxProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [draftId] = useState(() => crypto.randomUUID());
  const [mode, setMode] = useState<'amount' | 'percent' | 'names'>('amount');
  const [fresh, setFresh] = useState(false);
  const [inputError, setInputError] = useState('');
  const holdingsValid = holdings.length > 0 && (mode === 'names' || holdings.every(h => Number(h.amount_krw) > 0) && (mode !== 'percent' || Math.abs(holdings.reduce((n, h) => n + Number(h.amount_krw), 0) - 100) < .001));
  function add(account: string, item: Instrument) {
    if (holdings.length >= 20 || holdings.some(h => h.account === account && h.instrument_id === item.id)) return;
    setHoldings([...holdings, { name: item.name, account, amount_krw: '', kind: item.kind, instrument_id: item.id }]);
  }
  function payload(profile = tax, planning = fresh): Input {
    return { holdings: holdings.map(h => ({ ...h, amount_krw: mode === 'names' || !Number(h.amount_krw) ? null : String(Number(h.amount_krw) * (mode === 'amount' ? 10000 : 1)) })), horizon, question, input_mode: mode, planning,
      preferences: { accounts: selected, contribution: how + (contribution && how === '매달 꾸준히' ? ` ${contribution}만 원` : ''), interests, note, tax_profile: profile ? { ...profile, accounts: profile.accounts.filter(a => selected.includes(a.account)) } : null } };
  }
  async function save(profile = tax, planning = fresh) {
    await ensureVisitorSession();
    await request('/drafts/' + draftId, { method: 'POST', body: JSON.stringify(payload(profile, planning)) });
  }
  async function advance(recommend = false) {
    if (saving) return;
    if (taxPending || tax && missingTaxAnswers(tax, selected)) { setInputError('절세 전략의 필수 항목을 모두 선택한 뒤 ‘선택한 내용 적용하기’를 눌러 주세요.'); setStep(0); return; }
    setSaving(true); setInputError('');
    try { await save(tax, recommend || fresh); } catch (error) { setInputError((error as Error).message); setSaving(false); return; }
    setSaving(false);
    if (recommend) setFresh(true);
    if (step < 4) { setStep(step + 1); window.scrollTo(0, 0); return; }
    if (holdings.length > 0 && !holdingsValid) { setInputError('금액을 입력하거나 ‘종목만 입력할래요’를 선택해 주세요. 비율로 입력했다면 합계가 100%여야 해요.'); setStep(0); window.scrollTo(0, 0); return; }
    onSubmit({ ...payload(), draft_id: draftId });
  }
  const valid = step === 0 ? holdingsValid || fresh && !holdings.length : step === 1 ? !!how : step === 2 ? !!horizon : true;
  const choices = (values: string[], current: string, change: (v: string) => void, labels = values) => <div className="chips big">{values.map((v, i) => <button className="chip" key={v} aria-pressed={current === v} onClick={() => change(v)}>{labels[i]}</button>)}</div>;
  return <>
    <div className="nav"><div className="nav__pill"><button disabled={!step || saving} className={'nav__back' + (!step ? ' off' : '')} aria-label="앞 질문으로" onClick={() => setStep(step - 1)}>‹</button><span className="nav__logo">ETF Orca</span><div className="prog"><i style={{ width: `${(step + 1) / 5 * 100}%` }} /></div><span className="nav__n">{step + 1} / 5</span></div></div>
    <section className="step in" key={step}>
      {inputError && <p role="alert">{inputError}</p>}
      {step === 0 && <><span className="eyebrow">내 구성 확인</span><h2>내 <em>ETF</em> 포트폴리오 분석</h2><p className="hint">고민중인 ETF 포트폴리오를 넣으면 놓친 점과 고칠 곳을 찾아드려요.</p>
        <PortfolioPreview holdings={holdings} mode={mode} />
        <>
          {!!holdings.length && <div className="unit"><div className="unit__row"><div className="switch" data-u={mode === 'percent' ? 'pct' : 'won'} aria-label="숫자 적는 방법">{(['amount', 'percent'] as const).map((m, i) => <button key={m} aria-pressed={mode === m} onClick={() => { if (m === mode) return; setMode(m); setHoldings(holdings.map(h => ({ ...h, amount_krw: '' }))); }}>{i ? '비율' : '금액'}</button>)}</div><button className="only" aria-pressed={mode === 'names'} onClick={() => setMode(mode === 'names' ? 'amount' : 'names')}>종목만 입력할래요</button></div><p className="unit__note">{mode === 'percent' ? '전체 계좌를 합쳐 100%가 되도록 적어 주세요.' : mode === 'names' ? '금액과 비중을 추정하지 않고 종목 구성만 살펴봐요.' : '현재 평가금액을 만 원 단위로 적어 주세요.'}</p></div>}
          {selected.map(account => <article className="acct" key={account}><div className="acct__head"><h3 className="acct__name">{account}</h3>{account !== '일반 계좌' && <button className="x" aria-label={account + ' 지우기'} onClick={() => { setSelected(selected.filter(a => a !== account)); setHoldings(holdings.filter(h => h.account !== account)); }}>×</button>}</div>
            {!!holdings.filter(h => h.account === account).length && <ul className="rows">{holdings.map((h, i) => h.account === account && <li className={'row' + (mode === 'names' ? ' bare' : '')} key={i}><span className="row__name">{h.name}</span>{mode !== 'names' && <label className="num won"><input aria-label={h.name + (mode === 'amount' ? ' 금액' : ' 비율')} inputMode="decimal" type="number" min="0.01" max={mode === 'percent' ? 100 : 1000000000} step="0.01" placeholder="0" value={h.amount_krw || ''} onChange={e => setHoldings(holdings.map((x, n) => n === i ? { ...x, amount_krw: e.target.value } : x))} /><span>{mode === 'amount' ? '만 원' : '%'}</span></label>}<button className="x" aria-label={h.name + ' 빼기'} onClick={() => setHoldings(holdings.filter((_, n) => n !== i))}>×</button></li>)}</ul>}
            <InstrumentSearch account={account} catalog={catalog} onAdd={item => add(account, item)} />
          </article>)}
          <div className="q"><TaxAccounts onPending={setTaxPending} accounts={selected} value={tax} onSave={async profile => { await save(profile); setTax(profile); }} /><div className="chips">{accounts.filter(a => !selected.includes(a)).map(a => <button className="chip add" key={a} onClick={() => setSelected([...selected, a])}>{a}</button>)}</div></div>
          <div className="entry"><button className="rec" disabled={saving} onClick={() => void advance(true)}><b>종목부터 추천받기</b><span>나에게 맞는 ETF부터 찾아드려요</span></button></div>
          {catalogError && <p role="alert">{catalogError}</p>}
        </>
        <div className="askbox"><label className="lab" htmlFor="ask">AI에게 추가로 요청하기</label><p>전문 분석 프롬프트가 이미 설정돼 있어요. 더 원하는 것만 편하게 적어 주세요.</p><details className="prompt"><summary>프롬프트 보기</summary><pre>한 AI가 질문 해석, 포트폴리오 분석, 전망 조사, 투자 의견 검토를 순서대로 수행합니다. 확인된 자료를 인용하고, 계산할 수 없는 수치를 만들지 않습니다. AWS 전망은 ETF 한 종목과 구체적인 조회 목적을 정한 뒤에만 읽습니다.</pre></details><textarea className="field" id="ask" maxLength={1000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="예: 3년 뒤 결혼인데 지금 구성으로 버텨도 되는지, 배당은 언제부터 섞어야 하는지 궁금해요" /><span className="hintlab">이런 것도 요청할 수 있어요</span><div className="chips">{hints.map(h => <button className="chip add" key={h} onClick={() => setQuestion((question + '\n' + h).trim().slice(0, 1000))}>{h}</button>)}</div></div>
      </>}
      {step === 1 && <><span className="eyebrow">넣는 방법</span><h2>어떻게 넣나요?</h2><div className="chips big">{['이미 다 매수한 상태에요', '매달 꾸준히', '목돈 한 번에', '아직 고민 중'].map(value => <Fragment key={value}><button className="chip" aria-pressed={how === value} onClick={() => setHow(value)}>{value}</button>{value === '매달 꾸준히' && how === value && <label className="money"><span>매달</span><input ref={contributionRef} className="field" aria-label="추가 투자금액" inputMode="numeric" maxLength={9} value={contribution} onChange={e => setContribution(e.target.value.replace(/\D/g, ''))} placeholder="0" /><span>만 원</span></label>}</Fragment>)}</div></>}
      {step === 2 && <><span className="eyebrow">쓸 시점</span><h2>언제쯤 쓸 돈인가요?</h2>{choices(['3년 안', '3~10년', '10년 이상', '정한 때 없음'], horizon, setHorizon, ['3년 안', '3~10년 뒤', '10년 넘게 뒤', '정한 때 없어요'])}</>}
      {step === 3 && <><span className="eyebrow">선택</span><h2>더 담고 싶은 쪽이 있나요?</h2><p className="hint">없으면 그냥 넘어가도 돼요.</p>{[['투자 성향', '지수 따라가기', '배당', '성장', '안정 (채권·금)'], ['테마', '반도체', 'AI·빅테크', '방산', '로봇', '원자력', '전력 인프라', '바이오·헬스케어', '소비재', '금융', '리츠·부동산', '친환경 에너지', '한국 주식', '미국 주식', '선진국', '신흥국', '단기채', '장기채', '금·원자재']].map(([title, ...values]) => <div className="q" key={title}><span className="lab">{title}</span><div className="chips">{values.map(v => <button className="chip" key={v} aria-pressed={interests.includes(v)} onClick={() => setInterests(interests.includes(v) ? interests.filter(x => x !== v) : interests.length < 10 ? [...interests, v] : interests)}>{v}</button>)}</div></div>)}</>}
      {step === 3 && <div className="q"><label className="lab" htmlFor="keyword">관심 키워드 직접 입력</label><div className="keyword-entry"><input className="field" id="keyword" maxLength={50} value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="예: 환율 부담을 줄이고 싶어요" /><button className="secondary" disabled={!keyword.trim() || interests.length >= 10} onClick={() => { const v = keyword.trim(); if (v && !interests.includes(v)) setInterests([...interests, v]); setKeyword(''); }}>추가</button></div><p className="hint sm">최대 10개까지 선택하거나 직접 적을 수 있어요.</p><div className="chips">{interests.map(v => <button className="chip" key={v} aria-label={v + ' 선택 취소'} onClick={() => setInterests(interests.filter(x => x !== v))}>{v} ×</button>)}</div></div>}
      {step === 4 && <><span className="eyebrow">선택</span><h2>추가적으로 고려할<br />것들을 알려주세요</h2><p className="hint">원하는 것을 편하게 적어 주세요. 적은 대로 반영해요.</p><textarea className="field" aria-label="추가 고려사항" maxLength={1000} value={note} onChange={e => setNote(e.target.value)} placeholder="예: 매달 100만 원씩 넣는 계획까지 추천해줘" /><p className="hint sm">자료 조사와 검토에 약 5분이 걸려요.</p></>}
    </section>
    <div className="bar"><div className="bar__in"><div className="bar__msg" aria-live="polite">{step === 4 ? '분석에 약 5분이 걸려요' : ''}</div><button className="btn" disabled={!valid || busy || saving} aria-disabled={!valid || busy || saving} onClick={() => void advance()}>{busy ? '분석 접수 중…' : !valid ? '포폴 입력 대기 중' : step === 4 ? '내 포트폴리오 분석하기' : '다음'}</button></div></div>
  </>;
}
